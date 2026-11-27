#!/usr/bin/env python3
"""Build patched Tomotoru APKs by orchestrating the single-purpose tools.

Supported build modes:
  - original     : Copies the verified unmodified v1.0.4 base APK
  - offline      : Embeds tomoffline (127.0.0.1:18080), normal mode
  - offline-test : Embeds tomoffline (127.0.0.1:18080), --debug enabled
  - online       : Points to --url (or ONLINE_SERVER_URL env var), normal mode
  - online-test  : Points to http://127.0.0.1:18880, --debug enabled
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from tools.android.embed_server import inject_embedded_server_into_dex
from tools.android.sign_apk import align_and_sign_apk
from tools.assets.fetch_base import DEFAULT_APK_PATH, ensure_base_apk
from tools.assets.sxr_pack import patch_sxr
from tools.squirrel.patch import apply_debug_patch

DEFAULT_OFFLINE_PORT = 18080
DEFAULT_ONLINE_TEST_URL = "http://127.0.0.1:18880"

OLD_SK_URLS = (
    "http://13.113.101.2:8080",
    "http://52.194.165.198:8080",
)

OLD_PROJ_URLS = (
    ("http://52.69.85.104:8080", ""),
    ("http://download.tomotoru.cyberstep.com/bin/main", "/dl"),
    ("http://download.tomotoru.cyberstep.com/bin/bin_inspect", "/dl"),
)


def patch_sk_strings(sk_bytes: bytes, replacements: dict[str, str]) -> tuple[bytes, int]:
    """Replace exact string entries in a .sk bytecode file without shifting indices."""
    if len(sk_bytes) < 8 or sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    data_sz = struct.unpack_from(">I", sk_bytes, 2)[0]
    num_str = struct.unpack_from(">H", sk_bytes, 6)[0]
    header_and_code = sk_bytes[: 8 + data_sz]
    str_section = sk_bytes[8 + data_sz :]

    has_trailing_null = str_section.endswith(b"\x00")
    raw_parts = str_section[:-1].split(b"\x00") if has_trailing_null else str_section.split(b"\x00")

    count = 0
    new_parts = []
    for part in raw_parts:
        try:
            text = part.decode("ascii")
        except UnicodeDecodeError:
            new_parts.append(part)
            continue
        if text in replacements:
            new_parts.append(replacements[text].encode("ascii"))
            count += 1
        else:
            new_parts.append(part)

    if count == 0:
        return sk_bytes, 0

    rebuilt_strs = b"\x00".join(new_parts) + (b"\x00" if has_trailing_null else b"")
    assert len(new_parts) == num_str, f"String table count mismatch: {len(new_parts)} != {num_str}"
    return header_and_code + rebuilt_strs, count


def patch_sakana_proj(raw_bytes: bytes, server_url: str) -> tuple[bytes, int]:
    """Replace serverurl, pkgurl, and inspect_pkgurl inside sakana.proj text."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes, 0

    count = 0
    base = server_url.rstrip("/")
    for old, suffix in OLD_PROJ_URLS:
        if old in text:
            text = text.replace(old, f"{base}{suffix}")
            count += 1

    if count == 0:
        return raw_bytes, 0
    return text.encode("utf-8"), count


def patch_sxr_bundle(
    sxr_path: Path,
    server_url: str,
    updated_sxr_meta: dict[str, tuple[int, str]] | None = None,
    debug: bool = False,
    runtime_fixes: bool = False,
) -> int:
    """Patch URL-bearing entities (and internal pkg.json in main.sxr) inside an SXR bundle."""
    base = server_url.rstrip("/")
    sk_replacements = {old: base for old in OLD_SK_URLS}
    url_patches = 0

    def _patch_entity(name: str, data: bytes) -> bytes | None:
        nonlocal url_patches
        if data[:2] == b"\xfa\xfa":
            current = data
            modified = False

            patched, n = patch_sk_strings(current, sk_replacements)
            if n > 0:
                url_patches += n
                current = patched
                modified = True

            if debug or runtime_fixes:
                patched_dbg, desc = apply_debug_patch(name, current, include_telemetry=debug)
                if desc is not None:
                    tag = "debug" if debug else "fix"
                    print(f"    [{tag}] {desc}")
                    current = patched_dbg
                    modified = True

            if modified:
                return current

        if b"download.tomotoru.cyberstep.com" in data or b"52.69.85.104" in data:
            patched, n = patch_sakana_proj(data, base)
            if n > 0:
                url_patches += n
                return patched

        if name == "pkg.json" and updated_sxr_meta:
            pkg = json.loads(data.decode("utf-8"))
            changed = False
            for item in pkg.get("sxrlist", []):
                fname = f"{item['sxrname']}-0{item['version']:x}.sxr"
                if fname in updated_sxr_meta:
                    new_size, new_md5 = updated_sxr_meta[fname]
                    item["size"] = new_size
                    item["md5"] = new_md5
                    changed = True
            if changed:
                return json.dumps(pkg, separators=(",", ":")).encode("utf-8")

        return None

    entities_patched, _ = patch_sxr(sxr_path, sxr_path, _patch_entity)
    return url_patches if entities_patched > 0 else 0


def _patch_libsakanagl_dns(so_bytes: bytes, member_name: str) -> bytes | None:
    """Fix IPAddress::resolve() in libsakanagl.so for dual-stack (A + AAAA) domains on IPv4 networks.

    Original IPAddress::resolve() calls getaddrinfo(host, NULL, NULL, &res) and iterates over the
    entire addrinfo linked list without breaking, allowing trailing AF_INET6 records to overwrite
    the AF_INET result with an unreachable IPv6 address on IPv4-only Wi-Fi networks.
    """
    buf = bytearray(so_bytes)
    if member_name.endswith("arm64-v8a/libsakanagl.so"):
        off = 0x16ABF8
        if (
            len(buf) > off + 16
            and buf[0x16ABD4:0x16ABDC] == bytes.fromhex("7fb200396002803d")
            and buf[off : off + 4] == bytes.fromhex("29000014")
        ):
            buf[off : off + 4] = bytes.fromhex("2c000014")
            return bytes(buf)
    elif member_name.endswith("armeabi-v7a/libsakanagl.so"):
        off = 0x139338
        if (
            len(buf) > off + 16
            and buf[0x139304:0x13930C] == bytes.fromhex("0000a0e32c00c4e5")
            and buf[off : off + 4] == bytes.fromhex("210000ea")
        ):
            buf[off : off + 4] = bytes.fromhex("250000ea")
            return bytes(buf)
    return None


def _update_pkg_json(pkg_path: Path, modified_sxrs: dict[str, Path]) -> None:
    if not pkg_path.exists():
        return
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))

    if "main.sxr" in modified_sxrs and "mainsxr" in pkg:
        raw = modified_sxrs["main.sxr"].read_bytes()
        pkg["mainsxr"]["size"] = len(raw)
        pkg["mainsxr"]["md5"] = hashlib.md5(raw).hexdigest().upper()

    for item in pkg.get("sxrlist", []):
        filename = f"{item['sxrname']}-0{item['version']:x}.sxr"
        if filename in modified_sxrs:
            raw = modified_sxrs[filename].read_bytes()
            item["size"] = len(raw)
            item["md5"] = hashlib.md5(raw).hexdigest().upper()

    pkg_path.write_text(json.dumps(pkg, indent="\t"), encoding="utf-8")


def build_apk(
    apk_path: str | Path | None = None,
    mode: str = "offline",
    url: str | None = None,
    output_path: str | Path | None = None,
    port: int = DEFAULT_OFFLINE_PORT,
    debug: bool | None = None,
    embed_server: bool | None = None,
    keystore_path: str | Path | None = None,
    ks_pass: str = "android",
    key_pass: str = "android",
    ks_alias: str = "tomotoru",
) -> Path:
    """Build a Tomotoru APK according to the requested mode."""
    base_apk = Path(apk_path).resolve() if apk_path else ensure_base_apk(DEFAULT_APK_PATH)
    if not base_apk.exists():
        raise FileNotFoundError(f"Base APK not found: {base_apk}")

    if mode == "original":
        out = Path(output_path or "dist/tomotoru-v1.0.4-original.apk").resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_apk, out)
        print(f"Copied original APK -> {out}")
        return out

    if mode == "offline":
        embed_server = True if embed_server is None else embed_server
        debug = False if debug is None else debug
        server_url = url or f"http://127.0.0.1:{port}"
        default_out = "dist/tomotoru-offline.apk"
    elif mode == "offline-test":
        embed_server = True if embed_server is None else embed_server
        debug = True if debug is None else debug
        server_url = url or f"http://127.0.0.1:{port}"
        default_out = "dist/tomotoru-offline-test.apk"
    elif mode == "online":
        embed_server = False if embed_server is None else embed_server
        debug = False if debug is None else debug
        server_url = url or os.environ.get("ONLINE_SERVER_URL")
        if not server_url:
            raise ValueError("online mode requires --url or ONLINE_SERVER_URL environment variable")
        default_out = "dist/tomotoru-online.apk"
    elif mode == "online-test":
        embed_server = False if embed_server is None else embed_server
        debug = True if debug is None else debug
        server_url = url or DEFAULT_ONLINE_TEST_URL
        default_out = "dist/tomotoru-online-test.apk"
    else:
        raise ValueError(f"Unknown build mode: {mode}")

    server_url = server_url.rstrip("/")
    out_path = Path(output_path or default_out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(tempfile.mkdtemp(prefix="tomotoru_build_"))
    sxr_work_dir = work_dir / "sxr"
    sxr_work_dir.mkdir()

    try:
        print(f"\n[1/4] Extracting SXR assets from {base_apk.name}...")
        target_bundles = {"assets/sxr/1-011b.sxr", "assets/sxr/main.sxr", "assets/sxr/0-0111.sxr"}
        extracted_map: dict[str, Path] = {}
        with zipfile.ZipFile(base_apk, "r") as zin:
            for member in zin.namelist():
                if member in target_bundles or member == "assets/sxr/pkg.json":
                    dest = sxr_work_dir / Path(member).name
                    dest.write_bytes(zin.read(member))
                    extracted_map[member] = dest

        if not extracted_map:
            raise RuntimeError("No assets/sxr/ bundles found in APK")

        print(f"\n[2/4] Patching SXR bundles [mode={mode}, debug={debug}] -> {server_url} ...")
        modified_files: dict[str, Path] = {}
        modified_sxr_names: dict[str, Path] = {}
        updated_meta: dict[str, tuple[int, str]] = {}
        total_patches = 0

        ordered_members = sorted(
            (m for m in extracted_map if m.endswith(".sxr")),
            key=lambda m: (1 if m.endswith("/main.sxr") else 0, m),
        )
        for member in ordered_members:
            local_path = extracted_map[member]
            is_main = member.endswith("/main.sxr")
            n = patch_sxr_bundle(
                local_path,
                server_url,
                updated_sxr_meta=updated_meta if is_main else None,
                debug=debug,
                runtime_fixes=True,
            )
            if n > 0:
                print(f"  Patched {local_path.name}: {n} URL(s)")
                modified_files[member] = local_path
                modified_sxr_names[local_path.name] = local_path
                raw = local_path.read_bytes()
                updated_meta[local_path.name] = (len(raw), hashlib.md5(raw).hexdigest().upper())
                total_patches += n

        if total_patches == 0:
            raise RuntimeError("No URL patches were applied — unexpected APK contents")

        pkg_local = extracted_map.get("assets/sxr/pkg.json")
        if pkg_local and modified_sxr_names:
            _update_pkg_json(pkg_local, modified_sxr_names)
            modified_files["assets/sxr/pkg.json"] = pkg_local

        added_files: dict[str, bytes] = {}
        if embed_server:
            print(f"\n[2.5/4] Injecting embedded offline server into classes.dex (127.0.0.1:{port})...")
            with zipfile.ZipFile(base_apk, "r") as zin:
                orig_dex = zin.read("classes.dex")
            patched_dex = inject_embedded_server_into_dex(orig_dex)
            dex_local = work_dir / "classes.dex"
            dex_local.write_bytes(patched_dex)
            modified_files["classes.dex"] = dex_local
            added_files["assets/tomotoru_offline.json"] = json.dumps(
                {"port": port, "debug": bool(debug)}, separators=(",", ":")
            ).encode("utf-8")

        print("\n[3/4] Repacking APK...")
        unsigned_apk = work_dir / "unsigned.apk"
        with zipfile.ZipFile(base_apk, "r") as zin, zipfile.ZipFile(unsigned_apk, "w") as zout:
            written_names: set[str] = set()
            for info in zin.infolist():
                if info.filename.startswith("META-INF/"):
                    upper = info.filename.upper()
                    if upper == "META-INF/MANIFEST.MF" or upper.endswith(
                        (".SF", ".RSA", ".DSA", ".EC")
                    ):
                        continue

                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr

                if info.filename in modified_files:
                    payload = modified_files[info.filename].read_bytes()
                elif info.filename in added_files:
                    payload = added_files[info.filename]
                else:
                    payload = zin.read(info.filename)
                    if info.filename.endswith("/libsakanagl.so"):
                        patched_so = _patch_libsakanagl_dns(payload, info.filename)
                        if patched_so is not None:
                            payload = patched_so
                            print(f"  Patched dual-stack DNS resolver in {info.filename}")

                zout.writestr(new_info, payload)
                written_names.add(info.filename)

            for new_name, new_payload in added_files.items():
                if new_name not in written_names:
                    zout.writestr(new_name, new_payload, compress_type=zipfile.ZIP_DEFLATED)

        print("\n[4/4] Aligning & signing APK...")
        align_and_sign_apk(
            unsigned_apk,
            out_path,
            keystore_path=keystore_path,
            ks_pass=ks_pass,
            key_pass=key_pass,
            ks_alias=ks_alias,
        )

        print(f"\n{'=' * 50}")
        print(f"Built APK:  {out_path}")
        print(f"Size:       {out_path.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"Mode:       {mode} (embed={embed_server}, debug={debug})")
        print(f"Server URL: {server_url}")
        print(f"{'=' * 50}")
        return out_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build patched Tomotoru APK variants")
    parser.add_argument(
        "--mode",
        choices=("original", "offline", "offline-test", "online", "online-test"),
        default="offline",
        help="Build variant (default: offline)",
    )
    parser.add_argument("--apk", type=Path, default=None, help="Path to base APK (auto-fetched if omitted)")
    parser.add_argument("--url", default=None, help="Override server URL (required for --mode online unless ONLINE_SERVER_URL is set)")
    parser.add_argument("--port", type=int, default=DEFAULT_OFFLINE_PORT, help="Embedded server loopback port (default: 18080)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output APK path")
    parser.add_argument("--keystore", type=Path, default=None, help="Optional signing keystore path")
    parser.add_argument("--ks-pass", default=os.environ.get("SIGNING_KS_PASS", "android"))
    parser.add_argument("--key-pass", default=os.environ.get("SIGNING_KEY_PASS", "android"))
    parser.add_argument("--ks-alias", default=os.environ.get("SIGNING_KEY_ALIAS", "tomotoru"))
    args = parser.parse_args()

    build_apk(
        apk_path=args.apk,
        mode=args.mode,
        url=args.url,
        output_path=args.output,
        port=args.port,
        keystore_path=args.keystore,
        ks_pass=args.ks_pass,
        key_pass=args.key_pass,
        ks_alias=args.ks_alias,
    )


if __name__ == "__main__":
    main()
