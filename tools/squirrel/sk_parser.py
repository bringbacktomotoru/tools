#!/usr/bin/env python3
"""Parser for .sk pre-tokenized script files (CyberStep modified Squirrel 3.x).

Format (reverse-engineered from libsakanagl.so):
  Header:     FA FA | data_sz:u32_be | num_str:u16_be
  Token_data: data_sz bytes of serialized SSLexer token stream
  Strings:    num_str null-terminated UTF-8 strings

Token stream encoding:
  80 xx xx       = identifier (u16_be index into string table)
  81 xx xx       = string literal (u16_be index into string table)
  82 xx xx xx xx = 32-bit integer literal (u32_be)
  83 xx xx xx xx = 32-bit IEEE-754 float literal (f32_be)
  FD             = newline (+1 source line)
  FE xx          = newline skip (+xx source lines, u8)
  FF xx xx       = newline skip (+xxxx source lines, u16_be)
  Other bytes    = single-byte punctuation, keyword, or multi-char operator
                   (see token_table.py)
"""
import struct
import sys
import os


def decode_string_bytes(raw):
    """Decode null-terminated string table bytes: strict UTF-8, then CP932, then replace."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return raw.decode('cp932')
        except UnicodeDecodeError:
            return raw.decode('utf-8', errors='replace')


def parse_header(data):
    assert data[:2] == b'\xfa\xfa', "Not a .sk file (missing 0xFAFA magic)"
    data_sz = struct.unpack_from('>I', data, 2)[0]
    num_str = struct.unpack_from('>H', data, 6)[0]
    func_data = data[8:8 + data_sz]
    str_data = data[8 + data_sz:]

    strings = []
    i = 0
    while i < len(str_data):
        end = str_data.find(b'\x00', i)
        if end == -1:
            if i < len(str_data):
                s = decode_string_bytes(str_data[i:])
                if s:
                    strings.append(s)
            break
        strings.append(decode_string_bytes(str_data[i:end]))
        i = end + 1

    if len(strings) != num_str:
        print(f"  WARNING: expected {num_str} strings, parsed {len(strings)}")
    return data_sz, func_data, strings


class FuncReader:
    """Reads values from a token_data byte stream."""

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def eof(self):
        return self.pos >= len(self.data)

    def peek(self):
        if self.pos < len(self.data):
            return self.data[self.pos]
        return None

    def read_u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read_u16(self):
        v = struct.unpack_from('>H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def read_u32(self):
        v = struct.unpack_from('>I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_f32(self):
        v = struct.unpack_from('>f', self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_bytes(self, n):
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v


class FuncNode:
    """Represents a parsed script/token container."""

    def __init__(self):
        self.num_subs = 0
        self.source_name = ''
        self.func_name = ''
        self.attrs = []
        self.body_tokens = []
        self.sub_functions = []


def parse_func_data(func_data, strings):
    """Parse a .sk token stream into a FuncNode with flat body_tokens."""
    r = FuncReader(func_data)
    root = FuncNode()
    root.source_name = '<main>'
    root.func_name = '<main>'
    tokens = root.body_tokens

    while not r.eof():
        b = r.read_u8()

        if b == 0x80:
            if r.pos + 2 > len(func_data):
                break
            idx = r.read_u16()
            s = strings[idx] if idx < len(strings) else f'?{idx}'
            tokens.append(('ID', idx, s))

        elif b == 0x81:
            if r.pos + 2 > len(func_data):
                break
            idx = r.read_u16()
            s = strings[idx] if idx < len(strings) else f'?{idx}'
            tokens.append(('STR', idx, s))

        elif b == 0x82:
            if r.pos + 4 > len(func_data):
                break
            val = r.read_u32()
            tokens.append(('INT', val))

        elif b == 0x83:
            if r.pos + 4 > len(func_data):
                break
            val = r.read_f32()
            tokens.append(('FLT', val))

        elif b == 0xFD:
            tokens.append(('NL', 1))

        elif b == 0xFE:
            if r.pos + 1 > len(func_data):
                break
            skip = r.read_u8()
            tokens.append(('NL', max(1, skip)))

        elif b == 0xFF:
            if r.pos + 2 > len(func_data):
                break
            skip = r.read_u16()
            tokens.append(('NL', max(1, skip)))

        else:
            tokens.append(('OP', b))

    return root


def assign_names(node, strings):
    """Assign source_name / func_name from the first two identifier tokens."""
    for tok in node.body_tokens:
        if tok[0] == 'ID' and not node.source_name:
            node.source_name = tok[2]
        elif tok[0] == 'ID' and node.source_name and not node.func_name:
            node.func_name = tok[2]
            break


def print_tree(node, indent=0):
    pad = '  ' * indent
    name = node.func_name or '<anon>'
    src = node.source_name or ''
    print(f"{pad}script {name!r} (src={src!r}, tokens={len(node.body_tokens)})")
    for tok in node.body_tokens:
        print(f"{pad}  {tok}")


def analyze_sk(filename):
    with open(filename, 'rb') as f:
        data = f.read()

    name = os.path.basename(filename)
    print(f"\n{'=' * 60}")
    print(f"  {name} ({len(data)} bytes)")
    print(f"{'=' * 60}")

    data_sz, func_data, strings = parse_header(data)
    print(f"  data_sz={data_sz}, {len(strings)} strings")
    print(f"  Strings: {strings[:10]}{'...' if len(strings) > 10 else ''}")

    if len(func_data) == 0:
        print("  (empty)")
        return

    root = parse_func_data(func_data, strings)
    print_tree(root)


if __name__ == '__main__':
    import glob
    if len(sys.argv) > 1:
        for fn in sys.argv[1:]:
            analyze_sk(fn)
    else:
        files = sorted(glob.glob('../../extracted/main/*.sk'),
                       key=lambda f: os.path.getsize(f))
        for fn in files[:10]:
            analyze_sk(fn)
