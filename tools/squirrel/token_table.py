"""Token table for SakanaScript (.sk) decompiler.

Maps byte values to their names/purposes, reverse-engineered from
libsakanagl.so SSLexer (CyberStep modified Squirrel 3.x where
byte = Squirrel TK_* - 130).

Token categories:
- 0x21-0x7E: ASCII punctuation/operators (direct mapping)
- 0x80-0x83: Value tokens (IDENTIFIER, STRING, INTEGER, FLOAT)
- 0x84-0xCB: Keywords and multi-char operators
- 0xFD-0xFF: Line-number delta markers (+1, +u8, +u16_be)
"""

# Punctuation and single-char operators (ASCII range)
PUNCTUATION = {
    0x21: '!',
    0x23: '#',
    0x24: '$',
    0x25: '%',
    0x26: '&',
    0x28: '(',
    0x29: ')',
    0x2A: '*',
    0x2B: '+',
    0x2C: ',',
    0x2D: '-',
    0x2E: '.',
    0x2F: '/',
    0x3A: ':',
    0x3B: ';',
    0x3C: '<',
    0x3D: '=',
    0x3E: '>',
    0x3F: '?',
    0x5B: '[',
    0x5D: ']',
    0x5E: '^',
    0x7B: '{',
    0x7C: '|',
    0x7D: '}',
    0x7E: '~',
}

# Special value tokens (followed by payload bytes in the .sk stream)
SPECIAL = {
    0x80: 'IDENTIFIER',  # + u16_be string table index
    0x81: 'STRING',      # + u16_be string table index (quoted string literal)
    0x82: 'INTEGER',     # + u32_be integer literal
    0x83: 'FLOAT',       # + f32_be IEEE-754 float literal
}

# Keywords (reverse-engineered from keyword hash table & verified against .sk corpus)
KEYWORDS = {
    0x84: 'delegate',
    0x85: 'delete',
    0x8A: 'switch',
    0x8E: 'if',
    0x8F: 'else',
    0x90: 'while',
    0x91: 'break',
    0x92: 'for',
    0x93: 'do',
    0x94: 'null',
    0x95: 'foreach',
    0x96: 'in',
    0x99: 'local',
    0x9A: 'clone',
    0x9B: 'function',
    0x9C: 'return',
    0xA1: 'continue',
    0xA2: 'yield',
    0xA3: 'try',
    0xA4: 'catch',
    0xA5: 'throw',
    0xA8: 'resume',
    0xAA: 'case',
    0xAB: 'default',
    0xAC: 'this',
    0xB0: 'class',
    0xB1: 'extends',
    0xB2: 'constructor',
    0xB3: 'instanceof',
    0xB5: 'vargc',
    0xB6: 'vargv',
    0xB7: 'true',
    0xB8: 'false',
    0xBC: 'static',
    0xBD: 'enum',
    0xBE: 'const',
    0xBF: 'super',
    0xC2: 'removei',
    0xC3: 'undefined',
    0xC4: 'assert',
    0xC5: 'synchronized',
    0xC6: 'private',
    0xC7: 'with',
    0xC8: 'def',
    0xC9: 'dumpfunc',
    0xCA: 'import',
    0xCB: 'goto',
}

# Multi-char operators
OPERATORS = {
    0x86: '==',
    0x87: '!=',
    0x88: '<=',
    0x89: '>=',
    0x8C: '&&',
    0x8D: '||',
    0x97: '<-',
    0x9D: '<+',   # SakanaScript array-append operator (distinct from ASCII 0x2B '+')
    0x9F: '+=',
    0xA0: '-=',
    0xA6: '<<',
    0xA7: '>>',
    0xA9: '::',
    0xAD: '++',
    0xAE: '--',
    0xAF: '>>>',
    0xB4: '...',
    0xB9: '*=',
    0xBA: '/=',
    0xBB: '%=',
    0xC0: '|=',
    0xC1: '&=',
}

# Combined lookup table
TOKENS = {}
TOKENS.update(PUNCTUATION)
TOKENS.update(SPECIAL)
TOKENS.update(KEYWORDS)
TOKENS.update(OPERATORS)

# Reserved/unused tokens (appear in jump tables but have no handler)
RESERVED = {0x8B, 0x98, 0x9E}


def token_name(byte_val):
    """Return the name/representation of a token byte."""
    if byte_val in TOKENS:
        return TOKENS[byte_val]
    if byte_val in RESERVED:
        return f'RESERVED_{byte_val:#04x}'
    return f'UNKNOWN_{byte_val:#04x}'


def is_keyword(byte_val):
    """Check if a token byte is a keyword."""
    return byte_val in KEYWORDS


def is_operator(byte_val):
    """Check if a token byte is an operator."""
    return byte_val in OPERATORS or byte_val in PUNCTUATION
