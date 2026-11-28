"""Build a separately installable APK from an existing APK using apktool.

This is deliberately opt-in: it changes the manifest application ID, preserves
Java component class names, gives manifest providers distinct authorities,
then aligns/signs and verifies the rebuilt APK. It never invokes ADB.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"
CLASS_ATTRIBUTES = ("name", "targetActivity", "parentActivityName")
COMPONENT_TAGS = {
    "application",
    "activity",
    "activity-alias",
    "service",
    "receiver",
    "provider",
    "instrumentation",
}


def _run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command, check=False, text=True, capture_output=capture
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout if capture else ""


def _resolve_class(value: str, old_package: str) -> str:
    if value.startswith("."):
        return old_package + value
    if "." not in value:
        return f"{old_package}.{value}"
    return value


def rewrite_manifest(
    manifest: Path, new_package: str
) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Rewrite decoded apktool XML and return old ID, authority changes, classes."""
    tree = ET.parse(manifest)
    root = tree.getroot()
    old_package = root.get("package")
    if not old_package or old_package == new_package:
        raise ValueError(f"Unexpected manifest package: {old_package!r}")
    if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", new_package):
        raise ValueError(f"Invalid Android application ID: {new_package}")

    classes: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        component = tag in COMPONENT_TAGS
        for key, value in list(element.attrib.items()):
            attr = key.rsplit("}", 1)[-1]
            if component and attr in CLASS_ATTRIBUTES and not value.startswith("@"):
                resolved = _resolve_class(value, old_package)
                element.set(key, resolved)
                classes.append(resolved)
            elif attr != "authorities" and old_package in value:
                # This covers package-scoped permissions, task affinities, and
                # other manifest strings, but leaves Java class names intact.
                element.set(key, value.replace(old_package, new_package))

    authorities: list[tuple[str, str]] = []
    seen: set[str] = set()
    for provider in root.iter("provider"):
        key = ANDROID + "authorities"
        original = provider.get(key)
        if not original or original.startswith("@"):
            raise ValueError(
                "Provider authority is missing or resource-based; resolve it "
                "explicitly before producing a parallel APK."
            )
        replacement_parts: list[str] = []
        for authority in original.split(";"):
            authority = authority.strip()
            if not authority:
                raise ValueError(f"Malformed provider authorities: {original!r}")
            if "${applicationId}" in authority:
                suffix = authority.replace("${applicationId}", "").lstrip(".")
            elif authority.startswith(old_package):
                suffix = authority[len(old_package) :].lstrip(".")
            else:
                suffix = _resolve_class(
                    provider.get(ANDROID + "name", "provider"), old_package
                ).rsplit(".", 1)[-1].lower()
            candidate = new_package + (f".{suffix}" if suffix else ".provider")
            if candidate in seen:
                raise ValueError(f"Provider authority collision: {candidate}")
            seen.add(candidate)
            replacement_parts.append(candidate)
            authorities.append((authority, candidate))
        provider.set(key, ";".join(replacement_parts))

    root.set("package", new_package)
    ET.register_namespace("android", ANDROID_NS)
    tree.write(manifest, encoding="utf-8", xml_declaration=True)
    return old_package, authorities, classes


def rewrite_hardcoded_ids(decoded: Path, old_package: str, new_package: str) -> int:
    """Update package-ID string constants, never Java/Dalvik class descriptors."""
    changed = 0
    smali_literal = re.compile(r'(")([^"\r\n]*)(")')
    for path in decoded.rglob("*"):
        if not path.is_file() or path.name == "AndroidManifest.xml":
            continue
        if path.suffix.lower() == ".xml":
            text = path.read_text(encoding="utf-8")
            updated = text.replace(old_package, new_package)
            if updated != text:
                changed += text.count(old_package)
                path.write_text(updated, encoding="utf-8")
        elif path.suffix.lower() == ".smali":
            text = path.read_text(encoding="utf-8")

            def replace_literal(match: re.Match[str]) -> str:
                nonlocal changed
                literal = match.group(2)
                updated = literal.replace(old_package, new_package)
                if updated != literal:
                    changed += literal.count(old_package)
                return match.group(1) + updated + match.group(3)

            updated = smali_literal.sub(replace_literal, text)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
    return changed


def _find_apktool(explicit: str | None) -> list[str]:
    if explicit:
        path = Path(explicit)
        if path.suffix.lower() == ".jar":
            java = shutil.which("java")
            if not java:
                raise FileNotFoundError("Java is required to run apktool.jar")
            return [java, "-jar", str(path.resolve())]
        return [str(path.resolve())]
    apktool = shutil.which("apktool") or shutil.which("apktool.bat")
    if apktool:
        return [apktool]
    raise FileNotFoundError(
        "apktool not found. Install apktool or pass --apktool-jar PATH."
    )


def _find_sdk_tool(name: str, sdk: Path) -> str:
    candidates = sorted((sdk / "build-tools").glob(f"*/{name}.exe"), reverse=True)
    if not candidates:
        candidates = sorted((sdk / "build-tools").glob(f"*/{name}.bat"), reverse=True)
    if candidates:
        return str(candidates[0])
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{name} not found under {sdk / 'build-tools'} or PATH")


def build_parallel_apk(
    source_apk: Path,
    output_apk: Path,
    package: str,
    *,
    apktool: str | None = None,
    sdk: Path,
    keystore: Path | None = None,
    ks_pass: str = "android",
    key_pass: str = "android",
    alias: str = "tomotoru",
) -> None:
    source_apk = source_apk.resolve()
    output_apk = output_apk.resolve()
    if not source_apk.is_file():
        raise FileNotFoundError(source_apk)
    output_apk.parent.mkdir(parents=True, exist_ok=True)
    apktool_cmd = _find_apktool(apktool)
    zipalign = _find_sdk_tool("zipalign", sdk)
    apksigner = _find_sdk_tool("apksigner", sdk)
    keytool = shutil.which("keytool")
    if not keytool:
        raise FileNotFoundError("keytool not found on PATH (set JAVA_HOME)")

    with tempfile.TemporaryDirectory(prefix="tomotoru_parallel_") as temp:
        work = Path(temp)
        decoded = work / "decoded"
        unsigned = work / "unsigned.apk"
        aligned = work / "aligned.apk"
        _run(apktool_cmd + ["d", "-f", "-o", str(decoded), str(source_apk)])
        old_package, authorities, classes = rewrite_manifest(
            decoded / "AndroidManifest.xml", package
        )
        literal_changes = rewrite_hardcoded_ids(decoded, old_package, package)
        _run(apktool_cmd + ["b", str(decoded), "-o", str(unsigned)])

        # aapt's package dump is an independent check of the built manifest.
        aapt = _find_sdk_tool("aapt", sdk)
        badging = _run([aapt, "dump", "badging", str(unsigned)], capture=True)
        expected = f"package: name='{package}'"
        if expected not in badging:
            raise RuntimeError(f"aapt did not report expected package {package}")

        _run([zipalign, "-f", "-p", "4", str(unsigned), str(aligned)])
        temporary_keystore = keystore is None
        if temporary_keystore:
            keystore = work / "parallel-debug.keystore"
            _run(
                [
                    keytool,
                    "-genkeypair",
                    "-keystore",
                    str(keystore),
                    "-alias",
                    alias,
                    "-keyalg",
                    "RSA",
                    "-keysize",
                    "2048",
                    "-validity",
                    "10000",
                    "-storepass",
                    ks_pass,
                    "-keypass",
                    key_pass,
                    "-dname",
                    "CN=Tomotoru Parallel Test, OU=Local, O=Tomotoru, C=US",
                ]
            )
        _run(
            [
                apksigner,
                "sign",
                "--ks",
                str(keystore),
                "--ks-key-alias",
                alias,
                "--ks-pass",
                f"pass:{ks_pass}",
                "--key-pass",
                f"pass:{key_pass}",
                "--out",
                str(output_apk),
                str(aligned),
            ]
        )
        _run([apksigner, "verify", "--verbose", str(output_apk)])
        with zipfile.ZipFile(output_apk) as apk:
            dex_strings = b"\n".join(
                apk.read(name)
                for name in apk.namelist()
                if re.fullmatch(r"classes(\d+)?\.dex", name)
            )
        old_identifiers = {
            old_package.encode(),
            *(old.encode() for old, _ in authorities),
        }
        # DEX strings are NUL-terminated. Check for whole old identifiers,
        # not prefixes: the new application ID intentionally starts with the
        # old production package.
        hardcoded = sorted(
            value for value in old_identifiers if value + b"\0" in dex_strings
        )
        print(f"APK: {output_apk}")
        print(f"Package: {old_package} -> {package}")
        print(f"Preserved component class names: {len(classes)}")
        print(f"Provider authority changes: {authorities}")
        print(f"Updated hardcoded package-ID literals in decoded XML/DEX: {literal_changes}")
        if hardcoded:
            output_apk.unlink(missing_ok=True)
            raise RuntimeError(
                "Refusing to leave an APK with old package/authority DEX "
                "literals: " + ", ".join(value.decode() for value in hardcoded)
            )
        else:
            print("No old package/provider-authority literals found in DEX.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Opt-in: rebuild an APK with a separate application ID."
    )
    parser.add_argument("--apk", type=Path, required=True, help="Input APK")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--package",
        default="com.cyberstep.sanriocharacters.smoketest",
        help="Test application ID (must differ from the installed production ID)",
    )
    parser.add_argument("--apktool-jar", help="Path to apktool.jar (or apktool on PATH)")
    parser.add_argument(
        "--sdk",
        type=Path,
        default=Path(
            os.environ.get(
                "ANDROID_HOME",
                os.environ.get(
                    "ANDROID_SDK_ROOT",
                    str(Path.home() / "AppData/Local/Temp/opencode/tomotoru-android-sdk"),
                ),
            )
        ),
    )
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--ks-pass", default=os.environ.get("SIGNING_KS_PASS", "android"))
    parser.add_argument("--key-pass", default=os.environ.get("SIGNING_KEY_PASS", "android"))
    parser.add_argument("--ks-alias", default=os.environ.get("SIGNING_KEY_ALIAS", "tomotoru"))
    args = parser.parse_args()
    if args.package == "com.cyberstep.sanriocharacters":
        parser.error("refusing production application ID; select a unique test ID")
    build_parallel_apk(
        args.apk,
        args.output,
        args.package,
        apktool=args.apktool_jar,
        sdk=args.sdk,
        keystore=args.keystore,
        ks_pass=args.ks_pass,
        key_pass=args.key_pass,
        alias=args.ks_alias,
    )


if __name__ == "__main__":
    main()
