#!/usr/bin/env python3
"""Align, sign, and verify an Android APK using zipalign and apksigner."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_sdk_tool(tool_name: str) -> str:
    """Locate an Android build-tools executable (zipalign, apksigner) or JDK tool (keytool)."""
    found = shutil.which(tool_name)
    if found:
        return found

    candidates = []
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env_var)
        if sdk:
            candidates.append(Path(sdk))

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "Android" / "Sdk")
    else:
        candidates.append(Path.home() / "Android" / "Sdk")
        candidates.append(Path.home() / "Library" / "Android" / "sdk")

    exts = (".exe", ".bat", "") if os.name == "nt" else ("",)
    for sdk in candidates:
        bt_dir = sdk / "build-tools"
        if bt_dir.is_dir():
            for ver_dir in sorted(bt_dir.iterdir(), reverse=True):
                for ext in exts:
                    p = ver_dir / f"{tool_name}{ext}"
                    if p.is_file():
                        return str(p)

    raise FileNotFoundError(
        f"Could not find '{tool_name}'. Add Android SDK build-tools to PATH or set ANDROID_HOME."
    )


def _run(args: list[str], desc: str = "") -> str:
    if desc:
        print(f"  > {desc}")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Command failed ({args[0]}): {err}")
    return result.stdout


def align_and_sign_apk(
    unsigned_apk: str | Path,
    output_apk: str | Path,
    keystore_path: str | Path | None = None,
    ks_pass: str = "android",
    key_pass: str = "android",
    ks_alias: str = "tomotoru",
) -> Path:
    """Run zipalign -f 4, apksigner sign, and apksigner verify."""
    unsigned_apk = Path(unsigned_apk).resolve()
    output_apk = Path(output_apk).resolve()
    output_apk.parent.mkdir(parents=True, exist_ok=True)

    zipalign_bin = find_sdk_tool("zipalign")
    apksigner_bin = find_sdk_tool("apksigner")
    keytool_bin = find_sdk_tool("keytool")

    work_dir = Path(tempfile.mkdtemp(prefix="tomotoru_sign_"))
    try:
        aligned_apk = work_dir / "aligned.apk"
        _run([zipalign_bin, "-f", "4", str(unsigned_apk), str(aligned_apk)], "zipalign -f 4")

        if keystore_path:
            keystore = Path(keystore_path).resolve()
        else:
            keystore = work_dir / "debug.keystore"
            _run(
                [
                    keytool_bin,
                    "-genkeypair",
                    "-keystore",
                    str(keystore),
                    "-alias",
                    ks_alias,
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
                    "CN=Tomotoru Offline, OU=Dev, O=Local, L=Local, ST=Local, C=JP",
                ],
                "Generating temporary debug keystore",
            )

        _run(
            [
                apksigner_bin,
                "sign",
                "--ks",
                str(keystore),
                "--ks-key-alias",
                ks_alias,
                "--ks-pass",
                f"pass:{ks_pass}",
                "--key-pass",
                f"pass:{key_pass}",
                "--out",
                str(output_apk),
                str(aligned_apk),
            ],
            "Signing with apksigner",
        )
        _run([apksigner_bin, "verify", str(output_apk)], "Verifying APK signature")
        return output_apk
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Align, sign, and verify an APK")
    parser.add_argument("unsigned_apk", type=Path, help="Input unsigned APK")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output signed APK")
    parser.add_argument("--keystore", type=Path, default=None, help="Path to existing keystore")
    parser.add_argument("--ks-pass", default=os.environ.get("SIGNING_KS_PASS", "android"))
    parser.add_argument("--key-pass", default=os.environ.get("SIGNING_KEY_PASS", "android"))
    parser.add_argument("--ks-alias", default=os.environ.get("SIGNING_KEY_ALIAS", "tomotoru"))
    args = parser.parse_args()
    align_and_sign_apk(
        args.unsigned_apk,
        args.output,
        keystore_path=args.keystore,
        ks_pass=args.ks_pass,
        key_pass=args.key_pass,
        ks_alias=args.ks_alias,
    )


if __name__ == "__main__":
    main()
