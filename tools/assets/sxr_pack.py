#!/usr/bin/env python3
"""Repack or patch SXR bundles.

SXR format (reverse-engineered from libsakanagl.so & extract.py):
  [Header: 22 bytes]
    0x00: "SXR " (4 bytes magic)
    0x04: "DEFL" (4 bytes crypter)
    0x08: "&F" (2 bytes magic code)
    0x0A: trailer_offset (u64 BE)
    0x12: trailer_length (u32 BE)
  [Preamble: 42 bytes] (offsets 0x16..0x3F -> data starts at 0x40 = 64)
  [Data Section: variable, 16-byte aligned]
    Entity payloads (optionally compressed and/or XOR-encrypted)
  [Trailer Section: at trailer_offset]
    XOR-encrypted entity metadata entries terminated by u16(0)

Entity metadata entry:
  [child_count:u16][name_size:u16][name:utf8][unknown:8bytes]
  [data_offset:u64][data_size:u32][encrypted:u8][compression:u8][decompressed_size:u32]

XOR symmetry: decode_trailer() and decrypt_entity() work for both encrypt and decrypt.
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

# Import crypto/parsing primitives from extractor (XOR is symmetric)
from tools.assets.sxr_extract import (
    decode_trailer,
    decrypt_entity,
    read_u16_be,
    read_u32_be,
    read_u64_be,
)

HEADER_SIZE = 22
PREAMBLE_SIZE = 42
DATA_START_OFFSET = HEADER_SIZE + PREAMBLE_SIZE  # 64 (0x40)


def write_u16_be(value: int) -> bytes:
    return struct.pack(">H", value)


def write_u32_be(value: int) -> bytes:
    return struct.pack(">I", value)


def write_u64_be(value: int) -> bytes:
    return struct.pack(">Q", value)


def _align16(offset: int) -> int:
    rem = offset & 0xF
    return offset if rem == 0 else offset + (16 - rem)


def compress_data(data: bytes, compression_type: int):
    """Compress data with the specified SXR compression algorithm."""
    if compression_type == 0:
        return data, 0, 0
    elif compression_type == 6:
        compressed = zlib.compress(data, level=6)
        return compressed, 6, len(data)
    elif compression_type == 10:
        try:
            import lz4.block

            compressed = lz4.block.compress(data, store_size=False)
            return compressed, 10, len(data)
        except ImportError:
            print("Warning: lz4 not installed, falling back to zlib")
            compressed = zlib.compress(data, level=6)
            return compressed, 6, len(data)
    else:
        raise ValueError(f"Unknown compression type: {compression_type}")


def decompress_data(data: bytes, compression_type: int, decompressed_size: int) -> bytes:
    """Decompress entity data according to its SXR compression type."""
    if compression_type == 0:
        return data
    elif compression_type == 6:
        return zlib.decompress(data)
    elif compression_type == 10:
        import lz4.block

        return lz4.block.decompress(data, uncompressed_size=decompressed_size)
    else:
        raise ValueError(f"Unknown compression type: {compression_type}")


def build_entity_entry(
    name: str,
    data_offset: int,
    data_size: int,
    encrypted: bool,
    compression: int,
    decompressed_size: int,
    child_count: int = 0,
    unknown: bytes = b"\x00" * 8,
) -> bytes:
    """Build a single entity metadata entry for the trailer."""
    name_bytes = name.encode("utf-8")
    entry = bytearray()
    entry += write_u16_be(child_count)
    entry += write_u16_be(len(name_bytes))
    entry += name_bytes
    entry += (unknown + b"\x00" * 8)[:8]
    entry += write_u64_be(data_offset)
    entry += write_u32_be(data_size)
    entry += struct.pack("B", 1 if encrypted else 0)
    entry += struct.pack("B", compression)
    entry += write_u32_be(decompressed_size)
    return bytes(entry)


def build_trailer(entities_data: list, trailer_offset: int):
    """Build and encrypt the trailer from entity entries."""
    trailer = bytearray()
    for entry in entities_data:
        entry_bytes = build_entity_entry(**entry)
        trailer += write_u16_be(len(entry_bytes))
        trailer += entry_bytes

    trailer_bytes = bytes(trailer)
    trailer_len = len(trailer_bytes)
    encrypted_trailer = decode_trailer(trailer_offset, trailer_len, trailer_bytes)
    return encrypted_trailer, trailer_len


def parse_trailer_full(trailer_data: bytes) -> list:
    """Parse all entity metadata entries, preserving child_count and unknown bytes."""
    entities = []
    pos = 0
    while pos < len(trailer_data) - 1:
        size = read_u16_be(trailer_data, pos)
        if size == 0:
            break
        pos += 2
        entity_buf = trailer_data[pos : pos + size]
        pos += size

        if len(entity_buf) < 20:
            continue

        child_count = read_u16_be(entity_buf, 0)
        name_size = read_u16_be(entity_buf, 2)
        name = entity_buf[4 : 4 + name_size].decode("utf-8", errors="replace")
        unknown = entity_buf[4 + name_size : 4 + name_size + 8]
        data_offset_pos = 4 + name_size + 8
        data_offset = read_u64_be(entity_buf, data_offset_pos)
        data_size = read_u32_be(entity_buf, data_offset_pos + 8)
        encrypted = entity_buf[data_offset_pos + 12] != 0
        compression = entity_buf[data_offset_pos + 13]
        decompressed_size = read_u32_be(entity_buf, data_offset_pos + 14)

        entities.append(
            {
                "child_count": child_count,
                "name": name,
                "unknown": unknown,
                "data_offset": data_offset,
                "data_size": data_size,
                "encrypted": encrypted,
                "compression": compression,
                "decompressed_size": decompressed_size,
            }
        )
    return entities


def patch_sxr(sxr_path, output_path, patch_fn):
    """Patch selected entities in an existing SXR bundle while preserving full structure.

    Args:
        sxr_path: Path to source SXR file.
        output_path: Path to write patched SXR file (may equal sxr_path).
        patch_fn: Callable(name: str, data: bytes) -> Optional[bytes].
                  Return modified bytes to replace the entity, or None to keep unchanged.

    Returns:
        (patches_applied, total_entities)
    """
    sxr_data = Path(sxr_path).read_bytes()
    if sxr_data[:4] != b"SXR " or sxr_data[4:8] != b"DEFL" or sxr_data[8:10] != b"&F":
        raise ValueError(f"Invalid SXR header in {sxr_path}")

    orig_trailer_offset = read_u64_be(sxr_data, 10)
    orig_trailer_length = read_u32_be(sxr_data, 18)
    orig_preamble = sxr_data[HEADER_SIZE:DATA_START_OFFSET]
    if len(orig_preamble) < PREAMBLE_SIZE:
        orig_preamble = orig_preamble + b"\x00" * (PREAMBLE_SIZE - len(orig_preamble))

    trailer_raw = sxr_data[orig_trailer_offset : orig_trailer_offset + orig_trailer_length]
    trailer_decoded = decode_trailer(orig_trailer_offset, orig_trailer_length, trailer_raw)
    orig_entities = parse_trailer_full(trailer_decoded)

    data_payload = bytearray()
    new_entities = []
    patches_applied = 0

    for ent in orig_entities:
        if ent["data_size"] == 0:
            new_entities.append(
                {
                    "name": ent["name"],
                    "data_offset": 0,
                    "data_size": 0,
                    "encrypted": ent["encrypted"],
                    "compression": ent["compression"],
                    "decompressed_size": ent["decompressed_size"],
                    "child_count": ent["child_count"],
                    "unknown": ent["unknown"],
                }
            )
            continue

        raw_stored = sxr_data[ent["data_offset"] : ent["data_offset"] + ent["data_size"]]
        decrypted = decrypt_entity(ent["name"], raw_stored) if ent["encrypted"] else raw_stored

        # Try decompressing before passing to patch_fn
        can_decompress = True
        try:
            plain = decompress_data(decrypted, ent["compression"], ent["decompressed_size"])
        except Exception:
            plain = decrypted
            can_decompress = False

        patched = patch_fn(ent["name"], plain) if can_decompress else None

        # Align current write offset to 16 bytes
        cur_offset = DATA_START_OFFSET + len(data_payload)
        aligned_offset = _align16(cur_offset)
        if aligned_offset > cur_offset:
            data_payload += b"\x00" * (aligned_offset - cur_offset)

        if patched is not None:
            patches_applied += 1
            comp_bytes, comp_type, decomp_size = compress_data(patched, ent["compression"])
            stored = decrypt_entity(ent["name"], comp_bytes) if ent["encrypted"] else comp_bytes
            new_entities.append(
                {
                    "name": ent["name"],
                    "data_offset": aligned_offset,
                    "data_size": len(stored),
                    "encrypted": ent["encrypted"],
                    "compression": comp_type,
                    "decompressed_size": decomp_size,
                    "child_count": ent["child_count"],
                    "unknown": ent["unknown"],
                }
            )
            data_payload += stored
        else:
            # Keep original raw bytes untouched
            new_entities.append(
                {
                    "name": ent["name"],
                    "data_offset": aligned_offset,
                    "data_size": len(raw_stored),
                    "encrypted": ent["encrypted"],
                    "compression": ent["compression"],
                    "decompressed_size": ent["decompressed_size"],
                    "child_count": ent["child_count"],
                    "unknown": ent["unknown"],
                }
            )
            data_payload += raw_stored

    if patches_applied == 0 and Path(sxr_path).resolve() == Path(output_path).resolve():
        return 0, len(orig_entities)

    # Align trailer_offset to 16 bytes
    cur_end = DATA_START_OFFSET + len(data_payload)
    trailer_offset = _align16(cur_end)
    if trailer_offset > cur_end:
        data_payload += b"\x00" * (trailer_offset - cur_end)

    trailer_encrypted, trailer_length = build_trailer(new_entities, trailer_offset)

    header = bytearray()
    header += b"SXR "
    header += b"DEFL"
    header += b"&F"
    header += write_u64_be(trailer_offset)
    header += write_u32_be(trailer_length)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        f.write(header)
        f.write(orig_preamble)
        f.write(data_payload)
        f.write(trailer_encrypted)

    return patches_applied, len(orig_entities)


def repack_sxr(input_dir, output_path, compression="zlib", encrypt=False):
    """Repack extracted files into an SXR bundle.

    Args:
        input_dir: Directory containing extracted files
        output_path: Output SXR file path
        compression: Compression algorithm (zlib, lz4, none)
        encrypt: Whether to encrypt entity data
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    compression_type = {"zlib": 6, "lz4": 10, "none": 0}[compression]

    files = [f for f in sorted(input_path.iterdir()) if f.is_file()]
    if not files:
        raise ValueError(f"No files found in {input_dir}")

    print(f"Repacking {len(files)} files from {input_dir}")
    print(f"  Compression: {compression} (type {compression_type})")
    print(f"  Encryption: {encrypt}")

    data_payload = bytearray()
    entities = []

    for f in files:
        raw_data = f.read_bytes()
        compressed_data, comp_type, decompressed_size = compress_data(raw_data, compression_type)

        if encrypt:
            encrypted_data = decrypt_entity(f.name, compressed_data)
            is_encrypted = True
        else:
            encrypted_data = compressed_data
            is_encrypted = False

        cur_offset = DATA_START_OFFSET + len(data_payload)
        aligned_offset = _align16(cur_offset)
        if aligned_offset > cur_offset:
            data_payload += b"\x00" * (aligned_offset - cur_offset)

        data_payload += encrypted_data

        entities.append(
            {
                "name": f.name,
                "data_offset": aligned_offset,
                "data_size": len(encrypted_data),
                "encrypted": is_encrypted,
                "compression": comp_type,
                "decompressed_size": decompressed_size,
                "child_count": 0,
            }
        )

    cur_end = DATA_START_OFFSET + len(data_payload)
    trailer_offset = _align16(cur_end)
    if trailer_offset > cur_end:
        data_payload += b"\x00" * (trailer_offset - cur_end)

    trailer_encrypted, trailer_length = build_trailer(entities, trailer_offset)

    header = bytearray()
    header += b"SXR "
    header += b"DEFL"
    header += b"&F"
    header += write_u64_be(trailer_offset)
    header += write_u32_be(trailer_length)

    preamble = b"\x00" * PREAMBLE_SIZE

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "wb") as f:
        f.write(header)
        f.write(preamble)
        f.write(data_payload)
        f.write(trailer_encrypted)

    total_size = DATA_START_OFFSET + len(data_payload) + len(trailer_encrypted)
    print(f"  Output: {output_path}")
    print(f"  Header: {HEADER_SIZE} bytes, Preamble: {PREAMBLE_SIZE} bytes")
    print(f"  Data: {len(data_payload)} bytes")
    print(f"  Trailer: {len(trailer_encrypted)} bytes (encrypted)")
    print(f"  Total: {total_size} bytes")

    return True


def main():
    parser = argparse.ArgumentParser(description="Repack extracted files into SXR bundles")
    parser.add_argument("input_dir", help="Directory containing extracted files")
    parser.add_argument("-o", "--output", required=True, help="Output SXR file path")
    parser.add_argument(
        "-c",
        "--compression",
        choices=["zlib", "lz4", "none"],
        default="zlib",
        help="Compression algorithm (default: zlib)",
    )
    parser.add_argument("-e", "--encrypt", action="store_true", help="Encrypt entity data")

    args = parser.parse_args()

    try:
        repack_sxr(args.input_dir, args.output, args.compression, args.encrypt)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
