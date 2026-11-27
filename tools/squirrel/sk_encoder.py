#!/usr/bin/env python3
"""Lossless encoder from Squirrel/SakanaGL source code back to .sk binary format.

Format:
  Header:     FA FA | data_sz:u32_be | num_str:u16_be
  Token_data: data_sz bytes of serialized SSLexer token stream
  Strings:    num_str null-terminated strings
"""
import struct
import sys
from pathlib import Path

from tools.squirrel.token_table import KEYWORDS, OPERATORS, PUNCTUATION

# Reverse lookup tables
# Note: CyberStep's SSLexer encodes 'in' as an IDENTIFIER (0x80) rather than 0x96
# across the entire .sk corpus (1,676 occurrences of ID 'in', 0 occurrences of 0x96).
KEYWORD_TO_BYTE = {kw: b for b, kw in KEYWORDS.items() if kw != 'in'}

OPERATOR_TO_BYTE = {}
for b, op in OPERATORS.items():
    OPERATOR_TO_BYTE[op] = b
for b, op in PUNCTUATION.items():
    OPERATOR_TO_BYTE[op] = b

# Operators sorted longest-first for greedy matching
OPS_3 = sorted((op for op in OPERATOR_TO_BYTE if len(op) == 3), key=len, reverse=True)
OPS_2 = sorted((op for op in OPERATOR_TO_BYTE if len(op) == 2), key=len, reverse=True)
OPS_1 = {op: OPERATOR_TO_BYTE[op] for op in OPERATOR_TO_BYTE if len(op) == 1}

ESCAPE_MAP = {
    'n': '\n',
    'r': '\r',
    't': '\t',
    '\\': '\\',
    '"': '"',
    "'": "'",
    '0': '\0',
    'a': '\a',
    'b': '\b',
    'f': '\f',
    'v': '\v',
}


def _emit_newline_delta(buf, delta):
    """Append .sk line-delta token(s) for `delta` newlines."""
    while delta > 0:
        if delta == 1:
            buf.append(0xFD)
            delta -= 1
        elif delta <= 0xFF:
            buf.append(0xFE)
            buf.append(delta)
            delta = 0
        else:
            chunk = min(delta, 0xFFFF)
            buf.append(0xFF)
            buf.extend(struct.pack('>H', chunk))
            delta -= chunk


def _read_quoted_string(src, pos, quote_char):
    """Parse a quoted string literal starting after the opening quote.

    Returns (decoded_str, new_pos, newlines_seen).
    """
    n = len(src)
    chars = []
    newlines = 0
    while pos < n:
        ch = src[pos]
        if ch == quote_char:
            return ''.join(chars), pos + 1, newlines
        if ch == '\\':
            pos += 1
            if pos >= n:
                raise SyntaxError("Unterminated escape sequence in string literal")
            esc = src[pos]
            if esc in ESCAPE_MAP:
                chars.append(ESCAPE_MAP[esc])
                pos += 1
            elif esc == 'x' and pos + 2 < n:
                hex_part = src[pos + 1:pos + 3]
                chars.append(chr(int(hex_part, 16)))
                pos += 3
            elif esc == 'u' and pos + 4 < n:
                hex_part = src[pos + 1:pos + 5]
                chars.append(chr(int(hex_part, 16)))
                pos += 5
            elif esc == '\n':
                newlines += 1
                pos += 1
            else:
                chars.append(esc)
                pos += 1
        else:
            if ch == '\n':
                newlines += 1
            chars.append(ch)
            pos += 1
    raise SyntaxError(f"Unterminated string literal ({quote_char})")


def _read_verbatim_string(src, pos):
    """Parse a Squirrel verbatim string @"..." starting after '@\"'."""
    n = len(src)
    chars = []
    newlines = 0
    while pos < n:
        ch = src[pos]
        if ch == '"':
            if pos + 1 < n and src[pos + 1] == '"':
                chars.append('"')
                pos += 2
                continue
            return ''.join(chars), pos + 1, newlines
        if ch == '\n':
            newlines += 1
        chars.append(ch)
        pos += 1
    raise SyntaxError('Unterminated verbatim string literal @"..."')


def tokenize_squirrel(source_code):
    """Tokenize Squirrel/SakanaGL source into (.sk token list, string table).

    Returns:
      tokens: list of tuples matching sk_parser.parse_func_data format:
        ('ID', idx, text)
        ('STR', idx, text)
        ('INT', u32_val)
        ('FLT', f32_val)
        ('NL', delta)
        ('OP', byte_val)
      strings: list of unique strings in order of first appearance.
    """
    src = source_code.replace('\r\n', '\n').replace('\r', '\n')
    n = len(src)
    pos = 0
    tokens = []
    strings = []
    str_to_idx = {}
    pending_nl = 0

    def intern(s):
        idx = str_to_idx.get(s)
        if idx is None:
            idx = len(strings)
            strings.append(s)
            str_to_idx[s] = idx
        return idx

    def flush_nl():
        nonlocal pending_nl
        if pending_nl > 0:
            tokens.append(('NL', pending_nl))
            pending_nl = 0

    while pos < n:
        ch = src[pos]

        # Whitespace
        if ch in (' ', '\t', '\f', '\v'):
            pos += 1
            continue
        if ch == '\n':
            pending_nl += 1
            pos += 1
            continue

        # Comments: // and /* ... */
        if ch == '/' and pos + 1 < n:
            nxt = src[pos + 1]
            if nxt == '/':
                end = src.find('\n', pos + 2)
                if end == -1:
                    pos = n
                else:
                    pos = end
                continue
            if nxt == '*':
                end = src.find('*/', pos + 2)
                if end == -1:
                    raise SyntaxError(f"Unterminated block comment at offset {pos}")
                pending_nl += src[pos + 2:end].count('\n')
                pos = end + 2
                continue

        flush_nl()

        # Verbatim string @"..."
        if ch == '@' and pos + 1 < n and src[pos + 1] == '"':
            val, pos, nl = _read_verbatim_string(src, pos + 2)
            idx = intern(val)
            tokens.append(('STR', idx, val))
            pending_nl += nl
            continue

        # Double-quoted string literal
        if ch == '"':
            val, pos, nl = _read_quoted_string(src, pos + 1, '"')
            idx = intern(val)
            tokens.append(('STR', idx, val))
            pending_nl += nl
            continue

        # Single-quoted character literal -> INT
        if ch == "'":
            val, pos, nl = _read_quoted_string(src, pos + 1, "'")
            if len(val) != 1:
                raise SyntaxError(f"Invalid character literal {val!r}")
            tokens.append(('INT', ord(val) & 0xFFFFFFFF))
            pending_nl += nl
            continue

        # Numeric literals (hex int, float, decimal int)
        if ch.isdigit() or (ch == '.' and pos + 1 < n and src[pos + 1].isdigit()):
            start = pos
            # Hex literal: 0x...
            if ch == '0' and pos + 1 < n and src[pos + 1] in ('x', 'X'):
                pos += 2
                hex_start = pos
                while pos < n and src[pos] in '0123456789abcdefABCDEF':
                    pos += 1
                if pos == hex_start:
                    raise SyntaxError(f"Invalid hex literal at offset {start}")
                val = int(src[hex_start:pos], 16) & 0xFFFFFFFF
                tokens.append(('INT', val))
                continue

            is_float = False
            while pos < n and src[pos].isdigit():
                pos += 1
            if pos < n and src[pos] == '.' and (
                (pos + 1 < n and src[pos + 1].isdigit())
                or (pos == start)
            ):
                is_float = True
                pos += 1
                while pos < n and src[pos].isdigit():
                    pos += 1
            if pos < n and src[pos] in ('e', 'E'):
                exp_pos = pos + 1
                if exp_pos < n and src[exp_pos] in ('+', '-'):
                    exp_pos += 1
                if exp_pos < n and src[exp_pos].isdigit():
                    is_float = True
                    pos = exp_pos
                    while pos < n and src[pos].isdigit():
                        pos += 1

            num_text = src[start:pos]
            if is_float:
                fval = struct.unpack('>f', struct.pack('>f', float(num_text)))[0]
                tokens.append(('FLT', fval))
            else:
                ival = int(num_text, 10) & 0xFFFFFFFF
                tokens.append(('INT', ival))
            continue

        # Identifiers and keywords
        if ch == '_' or ch.isalpha():
            start = pos
            pos += 1
            while pos < n and (src[pos] == '_' or src[pos].isalnum()):
                pos += 1
            ident = src[start:pos]
            kw_byte = KEYWORD_TO_BYTE.get(ident)
            if kw_byte is not None:
                tokens.append(('OP', kw_byte))
            else:
                idx = intern(ident)
                tokens.append(('ID', idx, ident))
            continue

        # 3-char operators
        if pos + 3 <= n:
            tri = src[pos:pos + 3]
            if tri in OPS_3:
                tokens.append(('OP', OPERATOR_TO_BYTE[tri]))
                pos += 3
                continue

        # 2-char operators
        if pos + 2 <= n:
            duo = src[pos:pos + 2]
            if duo in OPS_2:
                tokens.append(('OP', OPERATOR_TO_BYTE[duo]))
                pos += 2
                continue

        # 1-char punctuation/operators
        b = OPS_1.get(ch)
        if b is not None:
            tokens.append(('OP', b))
            pos += 1
            continue

        raise SyntaxError(f"Unexpected character {ch!r} at offset {pos}")

    return tokens, strings


def encode_tokens(tokens, strings, encoding='utf-8'):
    """Serialize a parsed token list and string table into .sk binary bytes."""
    tok_buf = bytearray()
    for tok in tokens:
        ttype = tok[0]
        if ttype == 'ID':
            tok_buf.append(0x80)
            tok_buf.extend(struct.pack('>H', tok[1]))
        elif ttype == 'STR':
            tok_buf.append(0x81)
            tok_buf.extend(struct.pack('>H', tok[1]))
        elif ttype == 'INT':
            tok_buf.append(0x82)
            tok_buf.extend(struct.pack('>I', tok[1] & 0xFFFFFFFF))
        elif ttype == 'FLT':
            tok_buf.append(0x83)
            tok_buf.extend(struct.pack('>f', tok[1]))
        elif ttype == 'NL':
            _emit_newline_delta(tok_buf, tok[1])
        elif ttype == 'OP':
            tok_buf.append(tok[1])
        else:
            raise ValueError(f"Unknown token type: {ttype!r}")

    str_buf = bytearray()
    for s in strings:
        str_buf.extend(s.encode(encoding))
        str_buf.append(0)

    header = b'\xfa\xfa' + struct.pack('>IH', len(tok_buf), len(strings))
    return header + bytes(tok_buf) + bytes(str_buf)


def encode_sk(source_code: str, encoding: str = 'utf-8') -> bytes:
    """Encode Squirrel/SakanaGL source code into the .sk binary format."""
    tokens, strings = tokenize_squirrel(source_code)
    return encode_tokens(tokens, strings, encoding=encoding)


def main():
    if len(sys.argv) < 3:
        print("Usage: python sk_encoder.py <input.sq> <output.sk.bin>")
        sys.exit(1)
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    src = in_path.read_text(encoding='utf-8')
    blob = encode_sk(src)
    out_path.write_bytes(blob)
    print(f"Encoded {in_path} -> {out_path} ({len(blob)} bytes)")


if __name__ == '__main__':
    main()
