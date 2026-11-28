"""Opt-in CC0 audio replacement smoke test against the verified public base APK."""
import hashlib
import zipfile
from pathlib import Path

import pytest

from tools.build.build_apk import patch_sxr_bundle
from tools.assets.sxr_extract import decode_trailer, decrypt_entity, read_u32_be, read_u64_be
from tools.assets.sxr_pack import decompress_data, parse_trailer_full

ROOT = Path(__file__).resolve().parents[1]
BASE_APK = ROOT / ".cache" / "tomotoru_v1.0.4.apk"
CC0_ZIP = ROOT / ".cache" / "kenney_interfaceSounds.zip"


def test_cc0_ogg_replaces_one_sxr_entity_and_updates_archive(tmp_path):
    if not BASE_APK.is_file() or not CC0_ZIP.is_file():
        pytest.skip("Run the debug-assets fixture download steps documented in docs/debug-assets.md")

    with zipfile.ZipFile(BASE_APK) as apk:
        source = tmp_path / "b-010c.sxr"
        source.write_bytes(apk.read("assets/sxr/b-010c.sxr"))
    with zipfile.ZipFile(CC0_ZIP) as sounds:
        replacement = sounds.read("Audio/click_001.ogg")
    assert replacement.startswith(b"OggS")

    output = tmp_path / "patched.sxr"
    output.write_bytes(source.read_bytes())
    patch_sxr_bundle(
        output, "http://127.0.0.1:18880",
        debug_audio=replacement, debug_audio_entity="1",
    )

    data = output.read_bytes()
    trailer_offset = read_u64_be(data, 10)
    trailer_size = read_u32_be(data, 18)
    entries = parse_trailer_full(
        decode_trailer(trailer_offset, trailer_size, data[trailer_offset:trailer_offset + trailer_size])
    )
    target = next(item for item in entries if item["name"] == "1")
    stored = data[target["data_offset"]:target["data_offset"] + target["data_size"]]
    plain = decrypt_entity("1", stored) if target["encrypted"] else stored
    decoded = decompress_data(plain, target["compression"], target["decompressed_size"])
    assert decoded == replacement

    # The stock archive itself remains byte-for-byte unchanged by this opt-in patch.
    assert hashlib.sha256(source.read_bytes()).hexdigest() == hashlib.sha256(
        zipfile.ZipFile(BASE_APK).read("assets/sxr/b-010c.sxr")
    ).hexdigest()
