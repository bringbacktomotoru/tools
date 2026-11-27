#!/usr/bin/env python3
"""Extract SXR bundles from Tomotoru APK assets."""
import struct
import zlib
import hashlib
import os
import sys
import json
from pathlib import Path

from tools.assets.fetch_base import DEFAULT_SXR_DIR

def read_u16_be(data, offset):
    return struct.unpack('>H', data[offset:offset+2])[0]

def read_u32_be(data, offset):
    return struct.unpack('>I', data[offset:offset+4])[0]

def read_u64_be(data, offset):
    return struct.unpack('>Q', data[offset:offset+8])[0]

def fnv1a_32(data):
    """FNV-1a 32-bit hash matching the native implementation at 0x16d804.

    Names <= 16 codepoints: standard forward FNV-1a (byte-by-byte).
    Names >  16 codepoints: FNV-1a over UTF-32 codepoints in reverse order
                           with stride = max(1, len/16).
    """
    if isinstance(data, bytes):
        name = data.decode('utf-8')
    else:
        name = data
    codepoints = [ord(c) for c in name]
    n = len(codepoints)

    h = 0x811c9dc5
    if n <= 16:
        for cp in codepoints:
            h ^= cp
            h = (h * 0x01000193) & 0xFFFFFFFF
    else:
        stride = max(1, n >> 4)
        idx = n - stride
        while idx >= 0:
            h ^= codepoints[idx]
            h = (h * 0x01000193) & 0xFFFFFFFF
            idx -= stride
    return h

def decode_trailer(header_offset, trailer_length, trailer_data):
    """Decode the XOR-encrypted trailer."""
    trailer = bytearray(trailer_data)
    
    offset_lo = header_offset & 0xFFFFFFFF
    offset_hi = header_offset >> 32
    
    # Sign mask: if bit 31 of (trailer_length << 16) is set, all 1s
    signmask = 0xFFFFFFFF if ((trailer_length << 16) & 0x80000000) else 0
    
    state_a = (offset_hi ^ signmask ^ 0x075bcff3) & 0xFFFFFFFF
    state_a = (state_a ^ (state_a << 11)) & 0xFFFFFFFF
    state_a = (state_a ^ ((state_a >> 8) ^ 0x0549139a)) & 0xFFFFFFFF
    
    state_b = (offset_lo ^ (trailer_length << 16) ^ 0x3bec56ae) & 0xFFFFFFFF
    state_b = (state_b ^ (state_b << 11)) & 0xFFFFFFFF
    
    state_c = (state_b ^ (state_b >> 8) ^ state_a) & 0xFFFFFFFF
    state_d = (state_c ^ (state_a >> 19)) & 0xFFFFFFFF
    state_b = (state_d ^ 0x8E415C26) & 0xFFFFFFFF
    state_c = (state_b ^ (state_c >> 19)) & 0xFFFFFFFF
    state_b = (state_c ^ (state_b >> 19) ^ 0x4D9D5BB8) & 0xFFFFFFFF
    
    words = (trailer_length + 3) // 4
    for word_index in range(words):
        prev_c = state_c
        state_a = (state_a ^ (state_a << 11)) & 0xFFFFFFFF
        keystream = (state_b ^ state_a ^ (state_a >> 8) ^ (state_b >> 19)) & 0xFFFFFFFF
        
        keystream_parts = struct.pack('<I', keystream)
        
        base = word_index * 4
        for i, part in enumerate(keystream_parts):
            idx = base + i
            if idx >= len(trailer):
                break
            trailer[idx] ^= part
        
        state_c = state_b
        state_a = state_d
        state_b = keystream
        state_d = prev_c
    
    return bytes(trailer)

def decrypt_entity(name, payload):
    """Decrypt entity data using FNV-1a hash of name as key."""
    if len(payload) == 0:
        return payload
    
    stored_size = len(payload)
    key32 = fnv1a_32(name.encode('utf-8') if isinstance(name, str) else name)
    
    seed = ((137 * stored_size) ^ key32 ^ 0xC413A951) & 0xFFFFFFFF
    
    t = (seed ^ (seed << 11)) & 0xFFFFFFFF
    v31 = (t ^ (t >> 8)) & 0xFFFFFFFF
    
    v32 = (v31 ^ (v31 >> 19) ^ 0x52F53BE0) & 0xFFFFFFFF
    v34 = (v31 ^ 0xDCB47C50) & 0xFFFFFFFF
    v35 = ((v31 >> 19) ^ v32 ^ 0x4D9D51E6) & 0xFFFFFFFF
    v36 = 0xDCB467C6
    
    result = bytearray(payload)
    nwords = (len(payload) + 3) // 4
    
    for i in range(nwords):
        base = i * 4
        rem = len(payload) - base
        take = min(4, rem)
        
        # Read word as little-endian
        tmp = bytearray(4)
        tmp[:take] = payload[base:base+take]
        word = struct.unpack('<I', tmp)[0]
        
        x = (v36 ^ (v36 << 11)) & 0xFFFFFFFF
        ks = (v35 ^ x ^ (x >> 8) ^ (v35 >> 19)) & 0xFFFFFFFF
        
        word ^= ks
        
        v36 = v34
        v34 = v32
        v32 = v35
        v35 = ks
        
        out = struct.pack('<I', word & 0xFFFFFFFF)
        result[base:base+take] = out[:take]
    
    return bytes(result)

def decompress_zlib(data):
    return zlib.decompress(data)

def find_extension(buf):
    """Detect file type from magic bytes."""
    if not buf:
        return 'bin'
    if buf[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if buf[:4] == b'\xff\xd8\xff\xdb' or buf[:12] == b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01':
        return 'jpg'
    if buf[:4] == b'OggS':
        return 'ogg'
    if buf[:4] == b'RIFF' and buf[8:12] == b'WAVE':
        return 'wav'
    if buf[:4] == b'RIFF' and buf[8:12] == b'AVI ':
        return 'avi'
    if buf[:4] == b'SMOT':
        return 'smot'
    if buf[:4] == b'SMDL' or (len(buf) > 2 and buf[2:6] == b'SMDL'):
        return 'smdl'
    if buf[:2] == b'\xfa\xfa':
        return 'sk.bin'
    if buf[:4] == b'SQIR':
        return 'nut'
    return 'bin'

def parse_entities(trailer_data, sxr_data):
    """Parse entities from decoded trailer."""
    entities = []
    pos = 0
    while pos < len(trailer_data) - 1:
        size = read_u16_be(trailer_data, pos)
        if size == 0:
            break
        pos += 2
        entity_buf = trailer_data[pos:pos+size]
        pos += size
        
        if len(entity_buf) < 20:
            continue
        
        child_count = read_u16_be(entity_buf, 0)
        name_size = read_u16_be(entity_buf, 2)
        name = entity_buf[4:4+name_size].decode('utf-8', errors='replace')
        
        unk_a = entity_buf[4+name_size:4+name_size+8]
        data_offset_pos = 4 + name_size + 8
        data_offset = read_u64_be(entity_buf, data_offset_pos)
        data_size = read_u32_be(entity_buf, data_offset_pos + 8)
        encrypted = entity_buf[data_offset_pos + 12] != 0
        compression = entity_buf[data_offset_pos + 13]
        decompressed_size = read_u32_be(entity_buf, data_offset_pos + 14)
        
        entities.append({
            'child_count': child_count,
            'name': name,
            'data_offset': data_offset,
            'data_size': data_size,
            'encrypted': encrypted,
            'compression': compression,
            'decompressed_size': decompressed_size,
        })
    
    return entities

def extract_sxr(sxr_path, output_dir):
    """Extract a single SXR file."""
    with open(sxr_path, 'rb') as f:
        sxr_data = f.read()
    
    # Parse header
    magic = sxr_data[:4]
    if magic != b'SXR ':
        print(f"  Invalid SXR header: {magic}")
        return False
    
    crypter = sxr_data[4:8]
    if crypter != b'DEFL':
        print(f"  Invalid crypter string: {crypter}")
        return False
    
    magic_code = sxr_data[8:10]
    if magic_code != b'&F':
        print(f"  Invalid magic code: {magic_code}")
        return False
    
    trailer_offset = read_u64_be(sxr_data, 10)
    trailer_length = read_u32_be(sxr_data, 18)
    
    print(f"  Header: trailer_offset=0x{trailer_offset:x}, trailer_length={trailer_length}")
    
    # Decode trailer
    trailer_raw = sxr_data[trailer_offset:trailer_offset+trailer_length]
    trailer_decoded = decode_trailer(trailer_offset, trailer_length, trailer_raw)
    
    # Parse entities
    entities = parse_entities(trailer_decoded, sxr_data)
    
    os.makedirs(output_dir, exist_ok=True)
    
    count = 0
    for entity in entities:
        if entity['data_size'] == 0:
            continue
        
        # Read raw data
        data = sxr_data[entity['data_offset']:entity['data_offset']+entity['data_size']]
        
        # Decrypt if needed
        if entity['encrypted']:
            data = decrypt_entity(entity['name'], data)
        
        # Decompress if needed
        if entity['compression'] == 6:  # zlib
            try:
                data = decompress_zlib(data)
            except Exception:
                pass  # keep raw data, like fishing_rod
        elif entity['compression'] == 10:  # lz4 raw block
            try:
                import lz4.block
                data = lz4.block.decompress(data, uncompressed_size=entity['decompressed_size'])
            except ImportError:
                pass
            except Exception:
                pass  # keep raw data
        
        ext = find_extension(data)
        name = entity['name']
        # If name already has an extension matching detected type, don't double-extend
        name_base = os.path.splitext(name)[0]
        name_ext = os.path.splitext(name)[1]
        if name_ext and ext in ('sk.bin', 'nut', 'png', 'jpg', 'ogg', 'wav', 'smot', 'smdl'):
            out_path = os.path.join(output_dir, name)
        else:
            out_path = os.path.join(output_dir, f"{name}.{ext}")
        
        with open(out_path, 'wb') as f:
            f.write(data)
        
        count += 1
    
    print(f"  Extracted {count} entities")
    return True

def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    sxr_dir = project_root / 'sxr'
    if not sxr_dir.is_dir():
        sxr_dir = DEFAULT_SXR_DIR
    output_base = project_root / 'extracted'
    
    # Read pkg.json
    with open(sxr_dir / 'pkg.json', 'r') as f:
        pkg = json.load(f)
    
    print(f"Package version: {pkg['version']}")
    print(f"Main SXR: mainsxr")
    print(f"SXR list: {[s['sxrname'] for s in pkg['sxrlist']]}")
    
    # Extract main.sxr
    main_sxr = sxr_dir / 'main.sxr'
    if main_sxr.exists():
        print(f"\nExtracting main.sxr...")
        extract_sxr(str(main_sxr), str(output_base / 'main'))
    
    # Extract each SXR from list
    for sxr_meta in pkg['sxrlist']:
        sxr_name = sxr_meta['sxrname']
        sxr_version = sxr_meta['version']
        # Format: name-0hex_version.sxr
        sxr_filename = f"{sxr_name}-0{sxr_version:x}.sxr"
        sxr_path = sxr_dir / sxr_filename
        
        if sxr_path.exists():
            print(f"\nExtracting {sxr_filename}...")
            extract_sxr(str(sxr_path), str(output_base / f"{sxr_name}-0{sxr_version:x}"))
        else:
            print(f"\nSXR not found: {sxr_path}")

if __name__ == '__main__':
    main()
