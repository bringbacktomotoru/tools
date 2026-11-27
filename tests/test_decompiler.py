"""Unit tests for the CyberStep .sk bytecode/token-stream decompiler, encoder, and metadata."""
import json
from pathlib import Path
import struct
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SXR_1_011B = REPO_ROOT / ".cache" / "sxr" / "1-011b.sxr"

from tools.assets.sxr_extract import decode_trailer, decrypt_entity, parse_entities, read_u32_be, read_u64_be  # noqa: E402
from tools.assets.sxr_pack import decompress_data  # noqa: E402
from tools.squirrel.sk_decompiler import Decompiler  # noqa: E402
from tools.squirrel.sk_encoder import encode_sk  # noqa: E402
from tools.squirrel.sk_parser import decode_string_bytes, parse_func_data, parse_header  # noqa: E402

_SXR_CACHE: dict[str, bytes] = {}


def _read_sk_from_bundle(entity_id: str) -> bytes:
    if not _SXR_CACHE:
        if not SXR_1_011B.is_file():
            pytest.skip("Missing .cache/sxr/1-011b.sxr (run python -m tools.assets.fetch_base --extract-sxr)")
        raw = SXR_1_011B.read_bytes()
        to, tl = read_u64_be(raw, 10), read_u32_be(raw, 18)
        for ent in parse_entities(decode_trailer(to, tl, raw[to : to + tl]), raw):
            if ent["data_size"] == 0:
                continue
            payload = raw[ent["data_offset"] : ent["data_offset"] + ent["data_size"]]
            if ent["encrypted"]:
                payload = decrypt_entity(ent["name"], payload)
            if ent["compression"] != 0:
                payload = decompress_data(payload, ent["compression"], ent["decompressed_size"])
            _SXR_CACHE[ent["name"]] = payload
    return _SXR_CACHE[entity_id]


def _decompile_entity(entity_id: str) -> str:
    _, fd, strs = parse_header(_read_sk_from_bundle(entity_id))
    return Decompiler(parse_func_data(fd, strs), strs).decompile()


KEY_SCRIPT_IDS = [
    "112", "114", "126", "129", "144", "162", "163",
    "166", "174", "175", "176", "194", "214",
]


def _normalize_tokens(tokens):
    """Normalize token stream for semantic round-trip comparison (ignoring NL formatting)."""
    out = []
    for tok in tokens:
        kind = tok[0]
        if kind == "NL":
            continue
        if kind in ("ID", "STR"):
            out.append((kind, tok[2]))
        elif kind == "INT":
            out.append(("INT", tok[1]))
        elif kind == "FLT":
            out.append(("FLT", struct.pack(">f", tok[1])))
        elif kind == "OP":
            out.append(("OP", tok[1]))
    return out


def test_decompile_112_game_connection():
    out = _decompile_entity("112")

    assert 'Blowfish("test")' in out
    assert "Blowfish(17)" not in out
    assert '["ret"]' in out
    assert 'ret["server_time"]' in out
    assert "entrance_url = _url <+ ::ENTRANCE_URL;" in out
    assert "entrance_url = _url in ENTRANCE_URL;" not in out
    assert "/*0x" not in out


def test_decompile_194_config():
    out = _decompile_entity("194")

    assert 'SERVER_URL <- "http://13.113.101.2:8080";' in out
    assert 'ENTRANCE_URL <- ::CMD_URL <+ "/entrance";' in out
    assert 'ENABLE_AR <- _os.equals("Android") || _os.equals("iOS");' in out
    assert "TEST_ROOM_SCALE_X <- 1.0;" in out
    assert "TARGET_DEFAULT_X <- -5.0;" in out
    assert "CAMERA_ROTATE_H <- 45;" in out
    assert "/*0x" not in out


def test_decompile_144_package_updator():
    out = _decompile_entity("144")

    assert '"/setting.json"' in out
    assert "release_pkg_version" in out
    assert '"/pkg.json"' in out
    assert 'pkgjson["version"]' in out
    assert "totalsize += dlpkg.mainsxr.size;" in out
    assert "dlsize += size;" in out
    assert "_maintenance_info.min_sk_ver_ios : _maintenance_info.min_sk_ver_android" in out
    assert "/*0x" not in out


@pytest.mark.parametrize("entity_id", ["114", "176"])
def test_decompile_additional_key_scripts(entity_id):
    out = _decompile_entity(entity_id)
    assert len(out) > 200
    assert "/*0x" not in out


def test_synthetic_sk_operators_and_literals():
    """Verify 0x81 strings, 0x82 ints, 0x83 floats, and multi-char operators."""
    strings = ["x", "hello\n\"world\"", "y"]
    str_blob = b"".join(s.encode("utf-8") + b"\x00" for s in strings)

    # x <- "hello\n\"world\"";\nif (x && y || !y) x += 1.5;
    toks = bytearray()
    toks += b"\x80\x00\x00\x97\x81\x00\x01\x3b\xfd"
    toks += b"\x8e\x28\x80\x00\x00\x8c\x80\x00\x02\x8d\x21\x80\x00\x02\x29"
    toks += b"\x80\x00\x00\x9f" + b"\x83" + struct.pack(">f", 1.5) + b"\x3b"

    raw = b"\xfa\xfa" + struct.pack(">IH", len(toks), len(strings)) + bytes(toks) + str_blob
    _, func_data, parsed_strings = parse_header(raw)
    root = parse_func_data(func_data, parsed_strings)
    out = Decompiler(root, parsed_strings).decompile()

    assert out == 'x <- "hello\\n\\"world\\"";\nif (x && y || !y) x += 1.5;'


def test_utf8_and_cp932_decoding():
    """Verify 79.sk (rooms.conf) has clean Japanese text and CP932 fallback works."""
    out = _decompile_entity("79")
    assert "\ufffd" not in out
    assert "はじめてのお部屋" in out
    assert "メロディのお部屋" in out
    assert "シナモンのお部屋" in out

    # Direct CP932 fallback test (Shift-JIS bytes invalid as UTF-8)
    sjis_text = "ハローキティのお部屋"
    sjis_bytes = sjis_text.encode("cp932")
    assert decode_string_bytes(sjis_bytes) == sjis_text

    # Synthetic .sk with CP932 string table entry
    toks = b"\x80\x00\x00\x3d\x81\x00\x01\x3b"
    str_blob = b"name\x00" + sjis_bytes + b"\x00"
    raw = b"\xfa\xfa" + struct.pack(">IH", len(toks), 2) + toks + str_blob
    _, func_data, parsed_strings = parse_header(raw)
    root = parse_func_data(func_data, parsed_strings)
    decompiled = Decompiler(root, parsed_strings).decompile()
    assert decompiled == f'name = "{sjis_text}";'
    assert "\ufffd" not in decompiled


@pytest.mark.parametrize("script_id", KEY_SCRIPT_IDS)
def test_encoder_roundtrip_key_scripts(script_id):
    """Verify token-stream equivalence and decompile idempotence on key 1-011b scripts."""
    orig_bytes = _read_sk_from_bundle(script_id)
    _, orig_fd, orig_strs = parse_header(orig_bytes)
    orig_root = parse_func_data(orig_fd, orig_strs)
    src1 = Decompiler(orig_root, orig_strs).decompile()

    reencoded = encode_sk(src1)
    assert reencoded[:2] == b"\xfa\xfa"
    _, re_fd, re_strs = parse_header(reencoded)
    re_root = parse_func_data(re_fd, re_strs)
    src2 = Decompiler(re_root, re_strs).decompile()

    assert _normalize_tokens(orig_root.body_tokens) == _normalize_tokens(re_root.body_tokens)
    assert src1 == src2


def test_encoder_synthetic_squirrel_snippets():
    """Verify sk_encoder on synthetic Squirrel code including try/catch, hex, floats, escapes."""
    src = (
        'function safeCall(x) {\n'
        '  try {\n'
        '    local color = 0xc85c78ff;\n'
        '    local delta = -5.25;\n'
        '    local msg = "line1\\n\\"quoted\\"\\t\\\\end";\n'
        '    foreach (k, v in ::items) {\n'
        '      x <- v + delta;\n'
        '    }\n'
        '    with (x) {\n'
        '      return color;\n'
        '    }\n'
        '  } catch (ex) {\n'
        '    return null;\n'
        '  }\n'
        '}'
    )
    blob = encode_sk(src)
    assert blob[:2] == b"\xfa\xfa"

    _, fd, strs = parse_header(blob)
    root = parse_func_data(fd, strs)
    out = Decompiler(root, strs).decompile()
    assert out == src

    # Second round-trip is identical
    blob2 = encode_sk(out)
    assert blob2 == blob


def test_resource_index_and_tree_mirror():
    """Verify the generated resource index and logical Squirrel tree."""
    index_path = REPO_ROOT / "decompiled" / "resource_index.json"
    if not index_path.is_file():
        pytest.skip("Run tools.assets.sxr_extract and tools.squirrel.decompile first")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert "by_path" in index and "by_entity" in index
    assert index["total_paths"] >= 2500
    assert index["total_decompiled"] >= 1124

    rooms_entry = index["by_path"]["/project/conf/rooms.conf"]
    assert rooms_entry["bundle"] == "1-011b"
    assert rooms_entry["entity_id"] == 79

    by_ent = index["by_entity"]["1-011b/79"]
    assert by_ent["path"] == "/project/conf/rooms.conf"

    tree_rooms = REPO_ROOT / "decompiled" / "tree" / "project" / "conf" / "rooms.conf.sq"
    assert tree_rooms.exists()
    assert "はじめてのお部屋" in tree_rooms.read_text(encoding="utf-8")


def test_game_metadata_json():
    """Verify the extracted game metadata structure and key relationships."""
    meta_path = REPO_ROOT / "decompiled" / "game_metadata.json"
    if not meta_path.is_file():
        pytest.skip("Run tools.squirrel.export_conf first")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for section in (
        "summary", "game_rule", "rooms", "room_slots", "sanrio_charas",
        "costumes", "costume_to_chara", "furnitures", "shops",
        "gachas", "tutorials", "panel_missions",
    ):
        assert section in meta, f"Missing section {section}"

    room1 = meta["rooms"]["1"]
    assert room1["name"] == "はじめてのお部屋"
    assert room1["chara_ids"] == [1, 2]
    assert len(room1["init_furnitures"]) >= 2

    assert meta["costume_to_chara"]["109001"] == 1
    assert meta["costumes"]["109001"]["chara_id"] == 1
    assert meta["sanrio_charas"]["1"]["def_costume_id"] == 109001
    assert len(meta["summary"]["tutorial_ids"]) > 0
    assert len(meta["summary"]["shop_ids"]) > 0
    assert len(meta["summary"]["gacha_ids"]) > 0
