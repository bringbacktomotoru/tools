#!/usr/bin/env python3
"""Decompiler for .sk pre-tokenized script files (SakanaGL modified Squirrel 3.x).

Reconstructs readable, properly indented Squirrel source code from the
serialized SSLexer token stream.
"""
import math
import os
import struct
import sys

from tools.squirrel.sk_parser import parse_header, parse_func_data
from tools.squirrel.token_table import KEYWORDS, TOKENS


# Operators that always take spaces on both sides when used as binary ops
BINARY_OPS = {
    '=', '<-', '==', '!=', '<=', '>=', '<', '>', '&&', '||',
    '+', '-', '*', '/', '%', '+=', '-=', '*=', '/=', '%=',
    '|=', '&=', '<<', '>>', '>>>', '|', '&', '^', '?',
}

# Keywords that precede a space before '('
CONTROL_PAREN_KEYWORDS = {'if', 'while', 'for', 'foreach', 'switch', 'catch', 'with'}

# Unary-only prefix operators (no space after)
PREFIX_UNARY_OPS = {'!', '~', '#', '$', '::'}

# Tokens after which '+' or '-' is unary rather than binary
UNARY_CONTEXT_TEXTS = (
    BINARY_OPS
    | PREFIX_UNARY_OPS
    | {'(', '[', '{', ',', ';', ':', 'return', 'case', 'throw', 'yield', 'delete', 'clone', 'else'}
)

# Tokens that never have a space before them
NO_SPACE_BEFORE = {'.', ',', ';', ')', ']', '++', '--'}

# Tokens that never have a space after them
NO_SPACE_AFTER = {'.', '(', '[', '!', '~', '#', '$', '::'}


def format_string_literal(s):
    """Format a Python string as a double-quoted Squirrel string literal."""
    out = ['"']
    for ch in s:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\0':
            out.append('\\0')
        elif ord(ch) < 0x20:
            out.append(f'\\x{ord(ch):02x}')
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def format_float_literal(val):
    """Format a 32-bit IEEE-754 float cleanly and losslessly (e.g. 1.0, 0.0, 20.0)."""
    if math.isnan(val):
        return 'NaN'
    if math.isinf(val):
        return '-Infinity' if val < 0 else 'Infinity'
    target = struct.pack('>f', val)
    for prec in (7, 8, 9):
        s = f'{val:.{prec}g}'
        if '.' not in s and 'e' not in s and 'E' not in s:
            s += '.0'
        if struct.pack('>f', float(s)) == target:
            return s
    s = repr(val)
    if '.' not in s and 'e' not in s and 'E' not in s:
        s += '.0'
    return s


def format_int_literal(val):
    """Format a 32-bit integer literal (hex for high-bit values like ARGB colors)."""
    if val >= 0x80000000:
        return f'0x{val:08x}'
    return str(val)


class Decompiler:
    def __init__(self, root, strings):
        self.root = root
        self.strings = strings
        self.output = []
        self.indent = 0

    def token_text(self, tok):
        """Return (kind, text) for a parsed token."""
        ttype = tok[0]
        if ttype == 'ID':
            return ('ID', tok[2])
        if ttype == 'STR':
            return ('STR', format_string_literal(tok[2]))
        if ttype == 'INT':
            return ('INT', format_int_literal(tok[1]))
        if ttype == 'FLT':
            return ('FLT', format_float_literal(tok[1]))
        if ttype == 'OP':
            b = tok[1]
            if b in KEYWORDS:
                return ('KW', KEYWORDS[b])
            if b in TOKENS:
                return ('OP', TOKENS[b])
            return ('OP', f'/*0x{b:02X}*/')
        return (ttype, '')

    def _format_line(self, line_tokens):
        """Format a single source line's tokens with idiomatic Squirrel spacing."""
        parts = []
        prev_kind = None
        prev_text = None
        prev_was_unary = False
        ternary_depth = 0

        for kind, text in line_tokens:
            if not text:
                continue

            if text == '?':
                ternary_depth += 1

            is_unary_sign = (
                kind == 'OP'
                and text in ('+', '-')
                and (prev_text is None or prev_text in UNARY_CONTEXT_TEXTS)
            )

            if prev_text is not None:
                need_space = True

                if prev_was_unary:
                    need_space = False
                elif prev_text in NO_SPACE_AFTER:
                    need_space = False
                elif text in NO_SPACE_BEFORE:
                    need_space = False
                elif text == ':':
                    if ternary_depth > 0:
                        need_space = True
                        ternary_depth -= 1
                    else:
                        need_space = False
                elif text == '(':
                    # Space before '(' after control keywords, binary ops, ',', ';', ':', 'return', etc.
                    if prev_kind == 'KW' and prev_text in CONTROL_PAREN_KEYWORDS:
                        need_space = True
                    elif prev_text in BINARY_OPS or prev_text in {',', ';', ':', 'return', 'throw', 'yield', 'case', 'in', 'instanceof'}:
                        need_space = True
                    else:
                        need_space = False
                elif text == '[':
                    # Space before '[' after keywords/operators/delimiters, tight after identifiers/expressions
                    if prev_text in BINARY_OPS or prev_text in {',', ';', ':', 'return', 'throw', 'case', 'in'}:
                        need_space = True
                    else:
                        need_space = False
                elif text == '::':
                    # Global scope prefix: space before if preceded by keyword/ident/op, tight after '(', '[', '!'
                    if prev_text in {'(', '[', '!', '~', ',', ';'}:
                        need_space = prev_text in {',', ';'}
                    else:
                        need_space = True
                elif prev_text in ('++', '--'):
                    # Prefix ++i / --i vs postfix i++ / i--
                    if kind == 'ID' or text == 'this':
                        need_space = False
                    else:
                        need_space = True
                elif text == '}' and prev_text == '{':
                    need_space = False
                elif text == ']' and prev_text == '[':
                    need_space = False

                if need_space:
                    parts.append(' ')

            parts.append(text)
            prev_kind = kind
            prev_text = text
            prev_was_unary = is_unary_sign

        return ''.join(parts)

    def decompile(self):
        tokens = self.root.body_tokens
        if not tokens:
            return ''

        # Split flat token stream into source lines using NL markers
        lines = []
        current = []
        pending_blank = False

        for tok in tokens:
            if tok[0] == 'NL':
                if current:
                    lines.append((pending_blank, current))
                    current = []
                    pending_blank = False
                if tok[1] >= 2 and lines:
                    pending_blank = True
            else:
                current.append(self.token_text(tok))

        if current:
            lines.append((pending_blank, current))

        # Emit lines with brace-based indentation
        self.output = []
        self.indent = 0

        for blank_before, line_toks in lines:
            if blank_before and self.output and self.output[-1] != '':
                self.output.append('')

            # Count leading '}' / ']' to dedent before emitting this line
            leading_closes = 0
            for kind, text in line_toks:
                if text == '}':
                    leading_closes += 1
                else:
                    break

            if leading_closes:
                self.indent = max(0, self.indent - leading_closes)

            # Slight dedent for case / default labels inside switch blocks
            is_label = (
                len(line_toks) >= 2
                and line_toks[0][0] == 'KW'
                and line_toks[0][1] in ('case', 'default')
            )
            emit_indent = max(0, self.indent - 1) if is_label and self.indent > 0 else self.indent

            formatted = self._format_line(line_toks)
            if formatted:
                self.output.append('  ' * emit_indent + formatted)

            # Update indent for remaining braces on this line
            net_braces = 0
            for idx, (kind, text) in enumerate(line_toks):
                if idx < leading_closes:
                    continue
                if text == '{':
                    net_braces += 1
                elif text == '}':
                    net_braces -= 1
            self.indent = max(0, self.indent + net_braces)

        return '\n'.join(self.output)


# Main -----------------------------------------------------------------

def decompile_sk(filename):
    with open(filename, 'rb') as f:
        data = f.read()

    data_sz, func_data, strings = parse_header(data)
    root = parse_func_data(func_data, strings)

    decomp = Decompiler(root, strings)
    return decomp.decompile()


def main():
    import glob
    files = []
    if len(sys.argv) > 1:
        for fn in sys.argv[1:]:
            if os.path.isdir(fn):
                files.extend(sorted(glob.glob(os.path.join(fn, '*.sk')) +
                                    glob.glob(os.path.join(fn, '*.sk.bin'))))
            else:
                files.append(fn)
    else:
        files = sorted(glob.glob('../../extracted/main/*.sk'),
                       key=lambda f: os.path.getsize(f))

    for fn in files:
        name = os.path.basename(fn)
        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}")
        try:
            result = decompile_sk(fn)
            print(result)
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
