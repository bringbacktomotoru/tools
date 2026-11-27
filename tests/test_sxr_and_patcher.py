"""Tests for SXR repacker, .sk string table patcher, and URL patching."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SXR_DIR = ROOT / ".cache" / "sxr"

from tools.assets.sxr_extract import decode_trailer, decrypt_entity, extract_sxr, parse_entities, read_u32_be, read_u64_be
from tools.assets.sxr_pack import decompress_data, repack_sxr
from tools.build.build_apk import patch_sakana_proj, patch_sk_strings, patch_sxr_bundle
from tools.squirrel.patch import apply_debug_patch
from tools.squirrel.sk_decompiler import Decompiler
from tools.squirrel.sk_parser import parse_func_data, parse_header


@pytest.mark.parametrize("compression", ["none", "zlib", "lz4"])
@pytest.mark.parametrize("encrypt", [False, True])
def test_sxr_repack_extract_roundtrip(tmp_path, compression, encrypt):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    sample_files = {
        "alpha.txt": b"hello tomotoru world " * 20,
        "beta.bin": bytes(range(256)) * 4,
    }
    for name, content in sample_files.items():
        (src_dir / name).write_bytes(content)

    out_sxr = tmp_path / "bundle.sxr"
    repack_sxr(str(src_dir), str(out_sxr), compression=compression, encrypt=encrypt)

    ext_dir = tmp_path / "extracted"
    assert extract_sxr(str(out_sxr), str(ext_dir))

    # Verify extracted contents match originals
    extracted_blobs = {p.name: p.read_bytes() for p in ext_dir.iterdir() if p.is_file()}
    assert len(extracted_blobs) == len(sample_files)
    for orig_name, orig_bytes in sample_files.items():
        matches = [b for k, b in extracted_blobs.items() if k.startswith(orig_name)]
        assert matches == [orig_bytes]


def test_patch_sk_strings_preserves_string_table_indices():
    src_1 = SXR_DIR / "1-011b.sxr"
    if not src_1.exists():
        pytest.skip(".cache/sxr/1-011b.sxr not present")

    orig_bytes = _read_sxr_entity(src_1, "194")
    _, orig_code, orig_strs = parse_header(orig_bytes)

    new_url = "http://127.0.0.1:5000"
    patched_bytes, count = patch_sk_strings(
        orig_bytes,
        {
            "http://13.113.101.2:8080": new_url,
            "http://52.194.165.198:8080": new_url,
        },
    )
    assert count == 2

    _, new_code, new_strs = parse_header(patched_bytes)
    assert new_code == orig_code
    assert len(new_strs) == len(orig_strs)
    assert new_strs[1] == new_url
    assert new_strs[3] == new_url
    # Critical: subsequent indices must NOT shift!
    assert new_strs[4] == "CMD_URL"
    assert new_strs[5] == "/app_kitty"
    assert new_strs[7] == "/entrance"
    assert new_strs[9] == "/main"
    assert new_strs[11] == "/report"


def test_patch_sxr_bundle_real_bundles(tmp_path):
    mock_url = "http://127.0.0.1:5000"

    # 1. Patch 1-011b.sxr (entity 194)
    src_1 = SXR_DIR / "1-011b.sxr"
    if src_1.exists():
        dst_1 = tmp_path / "1-011b.sxr"
        dst_1.write_bytes(src_1.read_bytes())
        assert patch_sxr_bundle(dst_1, mock_url) == 2

        # Verify entity 194 inside patched bundle
        raw = dst_1.read_bytes()
        to, tl = read_u64_be(raw, 10), read_u32_be(raw, 18)
        ents = parse_entities(decode_trailer(to, tl, raw[to : to + tl]), raw)
        ent_194 = next(e for e in ents if e["name"] == "194")
        payload = raw[ent_194["data_offset"] : ent_194["data_offset"] + ent_194["data_size"]]
        plain = decrypt_entity("194", payload)
        _, _, strs = parse_header(plain)
        assert strs[1] == mock_url
        assert strs[3] == mock_url
        assert strs[5] == "/app_kitty"

    # 2. Patch main.sxr (LZ4-compressed sakana.proj)
    src_main = SXR_DIR / "main.sxr"
    if src_main.exists():
        dst_main = tmp_path / "main.sxr"
        dst_main.write_bytes(src_main.read_bytes())
        assert patch_sxr_bundle(dst_main, mock_url) == 3

        raw = dst_main.read_bytes()
        to, tl = read_u64_be(raw, 10), read_u32_be(raw, 18)
        ents = parse_entities(decode_trailer(to, tl, raw[to : to + tl]), raw)
        ent_proj = next(e for e in ents if e["name"] == "sakana.proj")
        payload = raw[ent_proj["data_offset"] : ent_proj["data_offset"] + ent_proj["data_size"]]
        plain = decompress_data(
            decrypt_entity("sakana.proj", payload),
            ent_proj["compression"],
            ent_proj["decompressed_size"],
        ).decode("utf-8")
        assert f'"{mock_url}"' in plain
        assert f'"{mock_url}/dl"' in plain
        assert "download.tomotoru.cyberstep.com" not in plain
        assert "52.69.85.104" not in plain


def test_patch_sakana_proj():
    sample = (
        b'{"serverurl":"http://52.69.85.104:8080",'
        b'"pkgurl":"http://download.tomotoru.cyberstep.com/bin/main",'
        b'"inspect_pkgurl":"http://download.tomotoru.cyberstep.com/bin/bin_inspect"}'
    )
    out, count = patch_sakana_proj(sample, "http://10.0.2.2:5000")
    assert count == 3
    assert b"http://10.0.2.2:5000" in out
    assert b"http://10.0.2.2:5000/dl" in out


def test_patched_offline_apk_integrity():
    """Verify the built offline APK's manifest checksums, SXR endpoints, and DNS fix."""
    import hashlib
    import json
    import zipfile

    apk_path = ROOT / "dist" / "tomotoru-offline.apk"
    if not apk_path.exists():
        pytest.skip("dist/tomotoru-offline.apk not built")

    with zipfile.ZipFile(apk_path, "r") as z:
        assert z.read("lib/arm64-v8a/libsakanagl.so")[0x16ABF8:0x16ABFC] == bytes.fromhex("2c000014")
        assert z.read("lib/armeabi-v7a/libsakanagl.so")[0x139338:0x13933C] == bytes.fromhex("250000ea")

        pkg = json.loads(z.read("assets/sxr/pkg.json"))
        checks = [("main.sxr", pkg["mainsxr"]["md5"], pkg["mainsxr"]["size"])] + [
            (f"{x['sxrname']}-0{x['version']:x}.sxr", x["md5"], x["size"])
            for x in pkg["sxrlist"]
            if x["sxrname"] in ("1", "0")
        ]
        for fn, expected_md5, expected_size in checks:
            raw = z.read(f"assets/sxr/{fn}")
            assert len(raw) == expected_size, f"{fn} size mismatch"
            assert hashlib.md5(raw).hexdigest().upper() == expected_md5, f"{fn} md5 mismatch"

        # Verify 194 inside 1-011b.sxr
        r1 = z.read("assets/sxr/1-011b.sxr")
        to, tl = read_u64_be(r1, 10), read_u32_be(r1, 18)
        e194 = next(
            e for e in parse_entities(decode_trailer(to, tl, r1[to : to + tl]), r1) if e["name"] == "194"
        )
        strs = parse_header(
            decrypt_entity("194", r1[e194["data_offset"] : e194["data_offset"] + e194["data_size"]])
        )[2]
        assert "http://127.0.0.1:18080" in strs
        assert "http://13.113.101.2:8080" not in strs
        assert "http://52.194.165.198:8080" not in strs
        assert "/app_kitty" in strs

        # Verify sakana.proj inside main.sxr
        rm = z.read("assets/sxr/main.sxr")
        to, tl = read_u64_be(rm, 10), read_u32_be(rm, 18)
        ep = next(
            e for e in parse_entities(decode_trailer(to, tl, rm[to : to + tl]), rm)
            if e["name"] == "sakana.proj"
        )
        proj = decompress_data(
            decrypt_entity("sakana.proj", rm[ep["data_offset"] : ep["data_offset"] + ep["data_size"]]),
            ep["compression"],
            ep["decompressed_size"],
        ).decode("utf-8")
        assert "http://127.0.0.1:18080" in proj
        assert "download.tomotoru.cyberstep.com" not in proj


def _read_sxr_entity(sxr_path: Path, entity_name: str) -> bytes:
    raw = sxr_path.read_bytes()
    to, tl = read_u64_be(raw, 10), read_u32_be(raw, 18)
    ents = parse_entities(decode_trailer(to, tl, raw[to : to + tl]), raw)
    ent = next(e for e in ents if e["name"] == entity_name)
    payload = raw[ent["data_offset"] : ent["data_offset"] + ent["data_size"]]
    decrypted = decrypt_entity(entity_name, payload)
    if ent["compression"] != 0:
        return decompress_data(decrypted, ent["compression"], ent["decompressed_size"])
    return decrypted


def _decompile_sk(sk_bytes: bytes) -> str:
    _, token_data, strings = parse_header(sk_bytes)
    return Decompiler(parse_func_data(token_data, strings), strings).decompile()


def test_normal_mode_leaves_squirrel_logic_untouched(tmp_path):
    """Normal mode (debug=False) must only patch URLs and leave all Squirrel logic scripts untouched."""
    src_1 = SXR_DIR / "1-011b.sxr"
    src_main = SXR_DIR / "main.sxr"
    if not src_1.exists() or not src_main.exists():
        pytest.skip("sxr/1-011b.sxr or sxr/main.sxr not present")

    mock_url = "http://127.0.0.1:5000"
    dst_1 = tmp_path / "1-011b.sxr"
    dst_main = tmp_path / "main.sxr"
    dst_1.write_bytes(src_1.read_bytes())
    dst_main.write_bytes(src_main.read_bytes())

    assert patch_sxr_bundle(dst_1, mock_url, debug=False) == 2
    assert patch_sxr_bundle(dst_main, mock_url, debug=False) == 3

    # All non-URL scripts in 1-011b.sxr must be byte-for-byte identical to original
    for name in ("114", "129", "158", "267", "270", "384"):
        assert _read_sxr_entity(dst_1, name) == _read_sxr_entity(src_1, name), (
            f"Normal mode unexpectedly modified {name}.sk"
        )

    # SK3DObject.sk in main.sxr must be byte-for-byte identical to original
    assert _read_sxr_entity(dst_main, "SK3DObject.sk") == _read_sxr_entity(src_main, "SK3DObject.sk")

    # 194.sk must have URLs patched, but ENABLE_AR logic left untouched
    src_194 = _decompile_sk(_read_sxr_entity(dst_1, "194"))
    assert f'SERVER_URL <- "{mock_url}";' in src_194
    assert f'INSPECT_SERVER_URL <- "{mock_url}";' in src_194
    assert 'ENABLE_AR <- _os.equals("Android") || _os.equals("iOS");' in src_194


def test_debug_mode_applies_all_debug_patches_and_roundtrips(tmp_path):
    """--debug mode must apply all 7 debug patches cleanly (chaining 194.sk URL + AR disable) and produce valid .sk."""
    src_1 = SXR_DIR / "1-011b.sxr"
    src_main = SXR_DIR / "main.sxr"
    if not src_1.exists() or not src_main.exists():
        pytest.skip("sxr/1-011b.sxr or sxr/main.sxr not present")

    mock_url = "http://127.0.0.1:5000"
    dst_1 = tmp_path / "1-011b.sxr"
    dst_main = tmp_path / "main.sxr"
    dst_1.write_bytes(src_1.read_bytes())
    dst_main.write_bytes(src_main.read_bytes())

    assert patch_sxr_bundle(dst_1, mock_url, debug=True) == 2
    assert patch_sxr_bundle(dst_main, mock_url, debug=True) == 3

    # 1. 114.sk: UserData.load + changeSceneBody stack-trace hooks
    src_114 = _decompile_sk(_read_sxr_entity(dst_1, "114"))
    assert "USERDATA_LOAD_ERR: " in src_114
    assert "CHANGESCENE_ERR(" in src_114
    assert "sendReportToServer(_dbg_msg);" in src_114

    # 2. 129.sk: _pressedStart stack-trace hook
    src_129 = _decompile_sk(_read_sxr_entity(dst_1, "129"))
    assert "PRESSED_START_ERR: " in src_129
    assert "::gamesession.sendReportToServer(_dbg_msg);" in src_129

    # 3. 158.sk: missing BGM fallback
    src_158 = _decompile_sk(_read_sxr_entity(dst_1, "158"))
    assert 'if (!r) r = ::getResource("/music/BGM01.ogg");' in src_158

    # 4. 194.sk: URLs patched FIRST, then ENABLE_AR <- false chained on top
    src_194 = _decompile_sk(_read_sxr_entity(dst_1, "194"))
    assert f'SERVER_URL <- "{mock_url}";' in src_194
    assert f'INSPECT_SERVER_URL <- "{mock_url}";' in src_194
    assert "ENABLE_AR <- false;" in src_194
    assert 'ENABLE_AR <- _os.equals("Android")' not in src_194

    # 5. 267.sk: StageObject.setEffectScript(null) guard
    src_267 = _decompile_sk(_read_sxr_entity(dst_1, "267"))
    assert "if (!r) return;" in src_267

    # 6. 270.sk: missing icon/bg fallback
    src_270 = _decompile_sk(_read_sxr_entity(dst_1, "270"))
    assert "/ui/image/base/icon_change.png" in src_270
    assert "/ui/image/base/back_y.png" in src_270

    # 7. 384.sk: HomeUI.movePanel eventstopper guard + MOVEPANEL_ERR + AR alert + screenshot fix
    src_384 = _decompile_sk(_read_sxr_entity(dst_1, "384"))
    assert "MOVEPANEL_ERR: " in src_384
    assert "::gamesession.sendReportToServer(_dbg_msg);" in src_384
    assert 'prev_clazz ? (prev_clazz["unique_bgm"] != null) : false' in src_384
    assert '::dict.get("can_not_use_AR")' in src_384
    assert "RGBAImage(int(w), int(h))" in src_384

    # 7b. 107.sk: skLoadAddon("sksmartar") loaded outside ENABLE_AR for ARUtils
    src_107 = _decompile_sk(_read_sxr_entity(dst_1, "107"))
    assert 'if (skGetOSName() == "Android")' in src_107
    assert 'skLoadAddon("sksmartar");' in src_107

    # 7c. 376.sk & 421.sk: dialogue bubble end-of-talk softlock fix (_mdl -> this.model + cleanup guard)
    src_376 = _decompile_sk(_read_sxr_entity(dst_1, "376"))
    assert "showGetHapinessEffect(this.model);" in src_376
    assert "showGetHapinessEffect(_mdl);" not in src_376
    src_421 = _decompile_sk(_read_sxr_entity(dst_1, "421"))
    assert "printexception(ex);" in src_421

    # 7c. 376.sk & 421.sk: dialogue bubble end-of-talk softlock fix (_mdl -> this.model + cleanup guard)
    src_376 = _decompile_sk(_read_sxr_entity(dst_1, "376"))
    assert "showGetHapinessEffect(this.model);" in src_376
    assert "showGetHapinessEffect(_mdl);" not in src_376
    src_421 = _decompile_sk(_read_sxr_entity(dst_1, "421"))
    assert "printexception(ex);" in src_421

    # 8. SK3DObject.sk: missing .pm fallback
    src_sk3d = _decompile_sk(_read_sxr_entity(dst_main, "SK3DObject.sk"))
    assert "/res/goods/goods_1/flower.pm" in src_sk3d
    assert "/res/facial/kitty_face.pm" in src_sk3d


def test_apply_debug_patch_ignores_unknown_and_non_sk():
    raw = b"not a sk file"
    assert apply_debug_patch("158", raw) == (raw, None)
    fake_sk = b"\xfa\xfa\x00\x00\x00\x00\x00\x00"
    assert apply_debug_patch("999", fake_sk) == (fake_sk, None)
