"""Opt-in PNG replacement regression against the release APK's real SXR."""
import hashlib
import struct
import zlib
import zipfile
from pathlib import Path

import pytest

from tools.build.build_apk import build_apk, patch_sxr_bundle
from tools.assets.sxr_extract import (
    decode_trailer, decrypt_entity, read_u32_be, read_u64_be,
)
from tools.assets.sxr_pack import decompress_data, parse_trailer_full

ROOT = Path(__file__).resolve().parents[1]
BASE_APK = ROOT / ".cache" / "tomotoru_v1.0.4.apk"
UI_PACK = ROOT / ".cache" / "kenney_ui_pack.zip"
KENNEY_PNG = "PNG/Blue/Default/button_rectangle_depth_flat.png"
KENNEY_ZIP_SHA256 = "a8a14a234911eb648c062622915c93e79e94e97cb7f9f375a70f6617f1174318"
KENNEY_SOURCE_SHA256 = "6c709a45aae0330ffff5b060d9f14cc2297839f664e0a4c9e516d90ad085ec0b"
BUNDLE = "assets/sxr/1-011b.sxr"
ENTITY = "92"


def _read_entity(bundle: bytes, entity_name: str) -> bytes:
    trailer_offset = read_u64_be(bundle, 10)
    trailer_size = read_u32_be(bundle, 18)
    entries = parse_trailer_full(
        decode_trailer(
            trailer_offset, trailer_size,
            bundle[trailer_offset:trailer_offset + trailer_size],
        )
    )
    item = next(entry for entry in entries if entry["name"] == entity_name)
    stored = bundle[item["data_offset"]:item["data_offset"] + item["data_size"]]
    plain = decrypt_entity(entity_name, stored) if item["encrypted"] else stored
    return decompress_data(plain, item["compression"], item["decompressed_size"])


def _png(width: int, height: int) -> bytes:
    """Small valid RGBA PNG fixture with caller-selected IHDR dimensions."""
    raw = b"".join(b"\0" + b"\x35\x8a\xc1\xff" * width for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def real_loading_bundle():
    if not BASE_APK.is_file() or not UI_PACK.is_file():
        pytest.skip("Prepare .cache fixtures as documented in docs/debug-assets.md")
    with zipfile.ZipFile(BASE_APK) as apk:
        return apk.read(BUNDLE)


def test_png_opt_in_preserves_stock_entity_and_replaces_exact_bytes(
    tmp_path, real_loading_bundle
):
    with zipfile.ZipFile(UI_PACK) as pack:
        kenney_source = pack.read(KENNEY_PNG)
    assert hashlib.sha256(UI_PACK.read_bytes()).hexdigest() == KENNEY_ZIP_SHA256
    assert hashlib.sha256(kenney_source).hexdigest() == KENNEY_SOURCE_SHA256
    assert kenney_source.startswith(b"\x89PNG\r\n\x1a\n")

    replacement = _png(1280, 960)
    untouched = tmp_path / "stock.sxr"
    untouched.write_bytes(real_loading_bundle)
    before = _read_entity(untouched.read_bytes(), ENTITY)
    patch_sxr_bundle(untouched, "http://127.0.0.1:18880")
    assert _read_entity(untouched.read_bytes(), ENTITY) == before
    assert hashlib.sha256(real_loading_bundle).digest() == hashlib.sha256(
        zipfile.ZipFile(BASE_APK).read(BUNDLE)
    ).digest()

    patched = tmp_path / "replacement.sxr"
    patched.write_bytes(real_loading_bundle)
    patch_sxr_bundle(
        patched, "http://127.0.0.1:18880",
        debug_png=replacement, debug_png_entity=ENTITY,
    )
    assert _read_entity(patched.read_bytes(), ENTITY) == replacement


def test_png_replacement_rejects_bad_format_and_different_dimensions(
    tmp_path, real_loading_bundle
):
    path = tmp_path / "target.sxr"
    path.write_bytes(real_loading_bundle)
    with pytest.raises(ValueError, match="valid PNG IHDR"):
        patch_sxr_bundle(
            path, "http://127.0.0.1:18880",
            debug_png=b"not a png", debug_png_entity=ENTITY,
        )

    path.write_bytes(real_loading_bundle)
    with pytest.raises(ValueError, match="must match target dimensions"):
        patch_sxr_bundle(
            path, "http://127.0.0.1:18880",
            debug_png=_png(32, 32), debug_png_entity=ENTITY,
        )


def test_png_option_is_restricted_to_test_modes(tmp_path):
    apk = tmp_path / "minimal-base.apk"
    apk.write_bytes(b"placeholder")
    png = tmp_path / "replacement.png"
    png.write_bytes(_png(1, 1))
    with pytest.raises(ValueError, match="only available in test modes"):
        build_apk(apk_path=apk, mode="online", debug_png_path=png)
