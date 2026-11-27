#!/usr/bin/env python3
"""Download and verify the unmodified Tomotoru v1.0.4 base APK from GitHub Releases.

Stores the base APK in `.cache/tomotoru_v1.0.4.apk` and optionally extracts
`assets/sxr/*` into `.cache/sxr/` for tests and for syncing assets to tomoserver.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = ROOT / ".cache"
DEFAULT_APK_PATH = DEFAULT_CACHE_DIR / "tomotoru_v1.0.4.apk"
DEFAULT_SXR_DIR = DEFAULT_CACHE_DIR / "sxr"

BASE_RELEASE_URL = (
    "https://github.com/bringbacktomotoru/builds/releases/download/"
    "v0.0.1/tomotoru-v1.0.4-original.apk"
)
EXPECTED_SHA256 = "e457b42783e245677cdf775fa161a5a54aefaaf788d1c73dcd67470f67fa831b"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_base_apk(
    dest: Path = DEFAULT_APK_PATH,
    url: str = BASE_RELEASE_URL,
    extract_sxr: bool = False,
    sxr_dir: Path = DEFAULT_SXR_DIR,
) -> Path:
    """Return path to verified base APK, downloading it if missing or corrupted."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    need_download = True
    if dest.is_file():
        actual = sha256_file(dest)
        if actual == EXPECTED_SHA256:
            need_download = False
        else:
            print(f"Checksum mismatch on {dest} ({actual[:12]}...), re-downloading.")

    if need_download:
        print(f"Downloading base APK from {url} ...")
        req = urllib.request.Request(url, headers={"User-Agent": "tomotoru-fetch-base"})
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        tmp = dest.with_suffix(".apk.tmp")
        with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        actual = sha256_file(tmp)
        if actual != EXPECTED_SHA256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Downloaded APK SHA-256 mismatch: expected {EXPECTED_SHA256}, got {actual}"
            )
        tmp.replace(dest)
        print(f"Verified base APK: {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")

    if extract_sxr:
        sxr_dir = Path(sxr_dir)
        sxr_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "r") as z:
            for name in z.namelist():
                if name.startswith("assets/sxr/") and not name.endswith("/"):
                    target = sxr_dir / Path(name).name
                    if not target.is_file():
                        target.write_bytes(z.read(name))

    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and verify Tomotoru v1.0.4 base APK")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_APK_PATH, help="Output APK path")
    parser.add_argument("--url", default=BASE_RELEASE_URL, help="Download URL")
    parser.add_argument(
        "--extract-sxr",
        action="store_true",
        help="Also extract assets/sxr/* into .cache/sxr/",
    )
    parser.add_argument("--sxr-dir", type=Path, default=DEFAULT_SXR_DIR, help="Directory for extracted SXR bundles")
    args = parser.parse_args()
    ensure_base_apk(args.output, url=args.url, extract_sxr=args.extract_sxr, sxr_dir=args.sxr_dir)


if __name__ == "__main__":
    main()
