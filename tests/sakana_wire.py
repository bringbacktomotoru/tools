"""Reference SakanaGL wire codec for testing interoperability with the embedded Java server.

Reverse-engineered from 112.sk.bin (GameConnection.encrypt / decrypt):
  - Key: b"test" (string table index 17 in 112.sk.bin: Blowfish("test"))
  - Mode: Blowfish ECB (8-byte blocks)
  - Framing before Blowfish:
      [flag: 1 byte (0=raw, 1=zlib compressed if json >= 10240 bytes)]
      [size: 4 bytes little-endian (ebb.endian = 0 in SakanaGL ByteBuffer; ebb.puti(jb.size))]
      [payload: `size` bytes of UTF-8 JSON or zlib-compressed UTF-8 JSON]
      [padding: 0..7 bytes to align to 8-byte Blowfish block size]
  - Transport: Base64-encoded ciphertext
"""
import base64
import json
import struct
import zlib
from typing import Any

from Crypto.Cipher import Blowfish

BLOWFISH_KEY = b"test"
BLOCK_SIZE = Blowfish.block_size  # 8 bytes
COMPRESS_THRESHOLD = 10240


def _pad_block(data: bytes) -> bytes:
    """Zero-pad data to a multiple of Blowfish block size (8 bytes).

    Client decrypt() reads the 5-byte [flag, size] header and calls
    ebb.resize(size + 5), so trailing padding bytes are discarded.
    """
    rem = len(data) % BLOCK_SIZE
    if rem == 0:
        return data
    return data + b"\x00" * (BLOCK_SIZE - rem)


def encrypt_payload(data: Any) -> str:
    """Encrypt a JSON-serializable payload using the SakanaGL wire format."""
    json_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(json_bytes) >= COMPRESS_THRESHOLD:
        flag = 1
        body = zlib.compress(json_bytes)
    else:
        flag = 0
        body = json_bytes

    framed = struct.pack("<BI", flag, len(body)) + body
    cipher = Blowfish.new(BLOWFISH_KEY, Blowfish.MODE_ECB)
    encrypted = cipher.encrypt(_pad_block(framed))
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_payload(encoded: str | bytes) -> Any:
    """Decrypt a Base64+Blowfish framed payload from the game client."""
    if isinstance(encoded, str):
        encoded = encoded.strip().encode("ascii")
    raw = base64.b64decode(encoded)
    if len(raw) == 0 or len(raw) % BLOCK_SIZE != 0:
        raise ValueError(f"Invalid Blowfish ciphertext length: {len(raw)}")

    cipher = Blowfish.new(BLOWFISH_KEY, Blowfish.MODE_ECB)
    decrypted = cipher.decrypt(raw)

    if len(decrypted) < 5:
        raise ValueError("Decrypted payload too short for SakanaGL header")

    flag = decrypted[0]
    size = struct.unpack_from("<I", decrypted, 1)[0]

    # Fallback to big-endian length if little-endian length is out of bounds
    if flag not in (0, 1) or 5 + size > len(decrypted):
        size_be = struct.unpack_from(">I", decrypted, 1)[0]
        if flag in (0, 1) and 5 + size_be <= len(decrypted):
            size = size_be
        else:
            raise ValueError(f"Invalid SakanaGL frame header: flag={flag}, size={size}")

    body = decrypted[5 : 5 + size]
    if flag == 1:
        body = zlib.decompress(body)
    return json.loads(body.decode("utf-8"))
