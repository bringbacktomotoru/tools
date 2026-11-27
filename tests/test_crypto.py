"""Tests for SakanaGL Blowfish wire protocol (112.sk.bin)."""
import base64
import struct
import zlib

import pytest
from Crypto.Cipher import Blowfish

from tests.sakana_wire import BLOWFISH_KEY, COMPRESS_THRESHOLD, decrypt_payload, encrypt_payload


def test_key_matches_game_script():
    assert BLOWFISH_KEY == b"test"


def test_roundtrip_small_payload():
    payload = [{"ret": 1, "sessionId": "abc-123", "server_time": 1700000000}]
    encoded = encrypt_payload(payload)
    assert isinstance(encoded, str)

    # Verify raw SakanaGL frame: flag=0, little-endian size (ebb.endian = 0)
    raw = Blowfish.new(b"test", Blowfish.MODE_ECB).decrypt(base64.b64decode(encoded))
    flag = raw[0]
    size = struct.unpack_from("<I", raw, 1)[0]
    assert flag == 0
    assert size > 0
    assert 5 + size <= len(raw)

    assert decrypt_payload(encoded) == payload


def test_roundtrip_large_compressed_payload():
    payload = [{"ret": 1, "blob": "A" * (COMPRESS_THRESHOLD + 500)}]
    encoded = encrypt_payload(payload)

    raw = Blowfish.new(b"test", Blowfish.MODE_ECB).decrypt(base64.b64decode(encoded))
    flag = raw[0]
    size = struct.unpack_from("<I", raw, 1)[0]
    assert flag == 1
    decompressed = zlib.decompress(raw[5 : 5 + size])
    assert b"AAAA" in decompressed

    assert decrypt_payload(encoded) == payload


def test_decrypt_rejects_corrupt_input():
    with pytest.raises(Exception):
        decrypt_payload("not-valid-base64!!!")
    with pytest.raises(ValueError):
        decrypt_payload(base64.b64encode(b"123").decode())
