#!/usr/bin/env python3
"""Decompile Squirrel 3.x Nut bytecode to readable source."""
import struct
import sys
import os

OT_NULL = 0x01000001
OT_INTEGER = 0x02040001
OT_FLOAT = 0x04040001
OT_BOOL = 0x08000001
OT_STRING = 0x10080000

OPCODES = {
    0x00: '_OP_LINE', 0x01: '_OP_LOAD', 0x02: '_OP_LOADINT', 0x03: '_OP_LOADFLOAT',
    0x04: '_OP_DLOAD', 0x05: '_OP_TAILCALL', 0x06: '_OP_CALL', 0x07: '_OP_PREPCALL',
    0x08: '_OP_PREPCALLK', 0x09: '_OP_GETK', 0x0A: '_OP_MOVE', 0x0B: '_OP_NEWSLOT',
    0x0C: '_OP_DELETE', 0x0D: '_OP_SET', 0x0E: '_OP_GET', 0x0F: '_OP_EQ',
    0x10: '_OP_NE', 0x11: '_OP_ADD', 0x12: '_OP_SUB', 0x13: '_OP_MUL',
    0x14: '_OP_DIV', 0x15: '_OP_MOD', 0x16: '_OP_BITW', 0x17: '_OP_RETURN',
    0x18: '_OP_LOADNULLS', 0x19: '_OP_LOADROOT', 0x1A: '_OP_LOADBOOL',
    0x1B: '_OP_DMOVE', 0x1C: '_OP_JMP', 0x1D: '_OP_JCMP', 0x1E: '_OP_JZ',
    0x1F: '_OP_SETOUTER', 0x20: '_OP_GETOUTER', 0x21: '_OP_NEWOBJ',
    0x22: '_OP_APPENDARRAY', 0x23: '_OP_COMPARITH', 0x24: '_OP_INC',
    0x25: '_OP_INCL', 0x26: '_OP_PINC', 0x27: '_OP_PINCL', 0x28: '_OP_CMP',
    0x29: '_OP_EXISTS', 0x2A: '_OP_INSTANCEOF', 0x2B: '_OP_AND', 0x2C: '_OP_OR',
    0x2D: '_OP_NEG', 0x2E: '_OP_NOT', 0x2F: '_OP_BWNOT', 0x30: '_OP_CLOSURE',
    0x31: '_OP_YIELD', 0x32: '_OP_RESUME', 0x33: '_OP_FOREACH',
    0x34: '_OP_POSTFOREACH', 0x35: '_OP_CLONE', 0x36: '_OP_TYPEOF',
    0x37: '_OP_PUSHTRAP', 0x38: '_OP_POPTRAP', 0x39: '_OP_THROW',
    0x3A: '_OP_NEWSLOTA', 0x3B: '_OP_GETBASE', 0x3C: '_OP_CLOSE',
}

BITWISE_OPS = {0: '&', 2: '|', 3: '^', 4: '<<', 5: '>>', 6: '>>>'}
CMP_OPS = {0: '>', 2: '>=', 3: '<', 4: '<='}
ARITH_OPS = {ord('+'): '+', ord('-'): '-', ord('*'): '*', ord('/'): '/', ord('%'): '%'}


class NutReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_bytes(self, n):
        r = self.data[self.pos:self.pos+n]
        self.pos += n
        return r

    def read_u8(self):
        return self.read_bytes(1)[0]

    def read_i32(self):
        return struct.unpack('<i', self.read_bytes(4))[0]

    def read_u32(self):
        return struct.unpack('<I', self.read_bytes(4))[0]

    def read_f32(self):
        return struct.unpack('<f', self.read_bytes(4))[0]

    def read_object(self):
        tag = self.read_u32()
        if tag == OT_NULL:
            return ('null', None)
        elif tag == OT_INTEGER:
            return ('integer', self.read_i32())
        elif tag == OT_FLOAT:
            return ('float', self.read_f32())
        elif tag == OT_BOOL:
            return ('bool', self.read_u32() != 0)
        elif tag == OT_STRING:
            slen = self.read_i32()
            if slen < 0:
                slen = 0
            s = self.read_bytes(slen).decode('utf-8', errors='replace')
            return ('string', s)
        else:
            raise ValueError(f"Unknown object tag: 0x{tag:08x} at offset {self.pos-4}")

    def read_instruction(self):
        arg1 = self.read_i32()
        op = self.read_u8()
        arg0 = self.read_u8()
        arg2 = self.read_u8()
        arg3 = self.read_u8()
        return (op, arg0, arg1, arg2, arg3)


class NutFunction:
    def __init__(self):
        self.source_name = ''
        self.func_name = ''
        self.literals = []
        self.parameters = []
        self.outer_values = []
        self.local_vars = []
        self.line_infos = []
        self.default_params = []
        self.instructions = []
        self.sub_functions = []
        self.stack_size = 0
        self.is_generator = False
        self.var_params = -1


def read_marker(reader, expected=b'TRAP'):
    m = reader.read_bytes(4)
    if m != expected:
        raise ValueError(f"Expected marker {expected}, got {m} at offset {reader.pos-4}")


def read_function(reader):
    read_marker(reader, b'TRAP')
    func = NutFunction()

    tag, val = reader.read_object()
    func.source_name = val if tag == 'string' else ''

    tag, val = reader.read_object()
    func.func_name = val if tag == 'string' else ''

    read_marker(reader, b'TRAP')

    n_lits = reader.read_i32()
    n_params = reader.read_i32()
    n_outers = reader.read_i32()
    n_locals = reader.read_i32()
    n_lines = reader.read_i32()
    n_defaults = reader.read_i32()
    n_instrs = reader.read_i32()
    n_funcs = reader.read_i32()

    read_marker(reader, b'TRAP')
    func.literals = []
    for _ in range(n_lits):
        func.literals.append(reader.read_object())

    read_marker(reader, b'TRAP')
    func.parameters = []
    for _ in range(n_params):
        tag, val = reader.read_object()
        func.parameters.append(val if tag == 'string' else f'arg{_}')

    read_marker(reader, b'TRAP')
    func.outer_values = []
    for _ in range(n_outers):
        outer_type = reader.read_i32()
        tag, src = reader.read_object()
        tag, name = reader.read_object()
        func.outer_values.append((outer_type, src, name))

    read_marker(reader, b'TRAP')
    func.local_vars = []
    for _ in range(n_locals):
        tag, name = reader.read_object()
        pos = reader.read_i32()
        start_op = reader.read_i32()
        end_op = reader.read_i32()
        func.local_vars.append((name, pos, start_op, end_op))

    read_marker(reader, b'TRAP')
    func.line_infos = []
    for _ in range(n_lines):
        line = reader.read_i32()
        op_idx = reader.read_i32()
        func.line_infos.append((line, op_idx))

    read_marker(reader, b'TRAP')
    func.default_params = []
    for _ in range(n_defaults):
        func.default_params.append(reader.read_i32())

    read_marker(reader, b'TRAP')
    func.instructions = []
    for _ in range(n_instrs):
        func.instructions.append(reader.read_instruction())

    read_marker(reader, b'TRAP')
    func.sub_functions = []
    for _ in range(n_funcs):
        func.sub_functions.append(read_function(reader))

    func.stack_size = reader.read_i32()
    func.is_generator = reader.read_u8() != 0
    func.var_params = reader.read_i32()

    return func


def decompile_function(func, indent=0):
    lines = []
    pad = '    ' * indent

    if func.func_name and func.func_name != '_main_':
        params = ', '.join(func.parameters)
        lines.append(f"{pad}function {func.func_name}({params}) {{")

    sub_indent = indent + (1 if func.func_name and func.func_name != '_main_' else 0)

    local_names = {}
    for name, pos, start_op, end_op in func.local_vars:
        if name and name not in ('this',):
            local_names[pos] = name

    instrs = func.instructions
    i = 0
    labels = {}
    while i < len(instrs):
        op, a0, a1, a2, a3 = instrs[i]
        if op in (0x1C, 0x1E, 0x1D):
            target = i + 1 + a1
            if 0 <= target < len(instrs):
                labels[target] = target
        i += 1

    i = 0
    pending_line = None
    while i < len(instrs):
        op, a0, a1, a2, a3 = instrs[i]
        opname = OPCODES.get(op, f'OP_0x{op:02x}')

        for li, (line, op_idx) in enumerate(func.line_infos):
            if op_idx == i:
                pending_line = line
                break

        if i in labels:
            lines.append(f"{pad}// L{labels[i]}:")

        if pending_line is not None:
            lines.append(f"{pad}// line {pending_line}")
            pending_line = None

        def slot_name(slot):
            if slot in local_names:
                return local_names[slot]
            return f'_v{slot}'

        def get_literal(idx):
            if 0 <= idx < len(func.literals):
                t, v = func.literals[idx]
                if t == 'string':
                    return repr(v)
                elif t == 'integer':
                    return str(v)
                elif t == 'float':
                    return str(v)
                elif t == 'bool':
                    return 'true' if v else 'false'
                elif t == 'null':
                    return 'null'
                return f'lit{idx}'
            return f'lit{idx}'

        if op == 0x00:  # _OP_LINE
            pass
        elif op == 0x01:  # _OP_LOAD
            lines.append(f"{pad}{slot_name(a0)} = {get_literal(a1)};")
        elif op == 0x02:  # _OP_LOADINT
            lines.append(f"{pad}{slot_name(a0)} = {a1};")
        elif op == 0x03:  # _OP_LOADFLOAT
            fval = struct.pack('<i', a1)
            fval = struct.unpack('<f', fval)[0]
            lines.append(f"{pad}{slot_name(a0)} = {fval};")
        elif op == 0x1A:  # _OP_LOADBOOL
            lines.append(f"{pad}{slot_name(a0)} = {'true' if a1 else 'false'};")
        elif op == 0x18:  # _OP_LOADNULLS
            for j in range(a1 + 1):
                lines.append(f"{pad}{slot_name(a0 + j)} = null;")
        elif op == 0x19:  # _OP_LOADROOT
            lines.append(f"{pad}{slot_name(a0)} = root;")
        elif op == 0x0A:  # _OP_MOVE
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a1)};")
        elif op == 0x0E:  # _OP_GET
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a1)}[{slot_name(a2)}];")
        elif op == 0x09:  # _OP_GETK
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)}[{get_literal(a1)}];")
        elif op == 0x0D:  # _OP_SET
            lines.append(f"{pad}{slot_name(a1)}[{slot_name(a2)}] = {slot_name(a3)};")
        elif op == 0x0B:  # _OP_NEWSLOT
            lines.append(f"{pad}{slot_name(a1)}[{slot_name(a2)}] <- {slot_name(a3)};")
        elif op == 0x3A:  # _OP_NEWSLOTA
            lines.append(f"{pad}{slot_name(a1)}[{slot_name(a2)}] <- {slot_name(a3)};")
        elif op == 0x11:  # _OP_ADD
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)} + {slot_name(a1)};")
        elif op == 0x12:  # _OP_SUB
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)} - {slot_name(a1)};")
        elif op == 0x13:  # _OP_MUL
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)} * {slot_name(a1)};")
        elif op == 0x14:  # _OP_DIV
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)} / {slot_name(a1)};")
        elif op == 0x15:  # _OP_MOD
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)} % {slot_name(a1)};")
        elif op == 0x2D:  # _OP_NEG
            lines.append(f"{pad}{slot_name(a0)} = -{slot_name(a1)};")
        elif op == 0x2E:  # _OP_NOT
            lines.append(f"{pad}{slot_name(a0)} = !{slot_name(a1)};")
        elif op == 0x17:  # _OP_RETURN
            if a0 == 0xFF:
                lines.append(f"{pad}return;")
            else:
                lines.append(f"{pad}return {slot_name(a0)};")
        elif op == 0x1C:  # _OP_JMP
            target = i + 1 + a1
            lines.append(f"{pad}// jmp to L{target}")
        elif op == 0x1E:  # _OP_JZ
            target = i + 1 + a1
            lines.append(f"{pad}if ({slot_name(a0)}) {{")
            lines.append(f"{pad}    // jmp to L{target} if false")
        elif op == 0x0F:  # _OP_EQ
            lines.append(f"{pad}{slot_name(a0)} = ({slot_name(a2)} == {slot_name(a1)});")
        elif op == 0x10:  # _OP_NE
            lines.append(f"{pad}{slot_name(a0)} = ({slot_name(a2)} != {slot_name(a1)});")
        elif op == 0x28:  # _OP_CMP
            cmpop = CMP_OPS.get(a3, '?')
            lines.append(f"{pad}{slot_name(a0)} = ({slot_name(a2)} {cmpop} {slot_name(a1)});")
        elif op == 0x06:  # _OP_CALL
            base = a2
            nargs = a3
            args = ', '.join(slot_name(base + 1 + j) for j in range(nargs))
            if a0 == 0xFF:
                lines.append(f"{pad}{slot_name(base)}({args});")
            else:
                lines.append(f"{pad}{slot_name(a0)} = {slot_name(base)}({args});")
        elif op == 0x21:  # _OP_NEWOBJ
            objtype = {0: 'table', 1: 'array', 2: 'class'}.get(a3, '?')
            if a1 != 0xFF:
                lines.append(f"{pad}{slot_name(a0)} = new {objtype}();")
            else:
                lines.append(f"{pad}{slot_name(a0)} = new {objtype}();")
        elif op == 0x22:  # _OP_APPENDARRAY
            aat = {0: 'stack', 1: 'literal', 2: 'int', 3: 'float', 4: 'bool'}.get(a2, '?')
            if a2 == 1:
                lines.append(f"{pad}{slot_name(a0)}.append({get_literal(a1)});")
            elif a2 == 0:
                lines.append(f"{pad}{slot_name(a0)}.append({slot_name(a1)});")
            else:
                lines.append(f"{pad}{slot_name(a0)}.append({slot_name(a1)});")
        elif op == 0x30:  # _OP_CLOSURE
            if a1 < len(func.sub_functions):
                sub = func.sub_functions[a1]
                sub_name = sub.func_name or f'_closure_{a1}'
                lines.append(f"{pad}{slot_name(a0)} = function() {{ /* {sub_name} */ }};")
            else:
                lines.append(f"{pad}{slot_name(a0)} = closure(lit{a1});")
        elif op == 0x33:  # _OP_FOREACH
            lines.append(f"{pad}// foreach init: container={slot_name(a0)}, state={slot_name(a2)}")
        elif op == 0x34:  # _OP_POSTFOREACH
            lines.append(f"{pad}// foreach post")
        elif op == 0x39:  # _OP_THROW
            lines.append(f"{pad}throw {slot_name(a0)};")
        elif op == 0x37:  # _OP_PUSHTRAP
            lines.append(f"{pad}// pushtrap")
        elif op == 0x38:  # _OP_POPTRAP
            lines.append(f"{pad}// poptrap")
        elif op == 0x16:  # _OP_BITW
            bop = BITWISE_OPS.get(a3, '?')
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a2)} {bop} {slot_name(a1)};")
        elif op == 0x36:  # _OP_TYPEOF
            lines.append(f"{pad}{slot_name(a0)} = typeof {slot_name(a1)};")
        elif op == 0x2B:  # _OP_AND
            target = i + 1 + a1
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a0)} && /* short-circuit to L{target} */ {slot_name(a2)};")
        elif op == 0x2C:  # _OP_OR
            target = i + 1 + a1
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a0)} || /* short-circuit to L{target} */ {slot_name(a2)};")
        elif op == 0x23:  # _OP_COMPARITH
            arith_op = ARITH_OPS.get(a3, chr(a3) if a3 < 128 else '?')
            lines.append(f"{pad}{slot_name(a0)} {arith_op}= {slot_name(a1)};")
        elif op == 0x24:  # _OP_INC
            direction = '++' if a3 == 1 else '--'
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a1)}[{slot_name(a2)}];")
            lines.append(f"{pad}{slot_name(a1)}[{slot_name(a2)}] {direction};")
        elif op == 0x26:  # _OP_PINC
            direction = '++' if a3 == 1 else '--'
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a1)}[{slot_name(a2)}];")
            lines.append(f"{pad}{slot_name(a1)}[{slot_name(a2)}] {direction};")
        elif op == 0x1F:  # _OP_SETOUTER
            if a0 == 0xFF:
                lines.append(f"{pad}// setouter {a1} = {slot_name(a2)}")
            else:
                lines.append(f"{pad}{slot_name(a0)} = outer({a1});")
        elif op == 0x20:  # _OP_GETOUTER
            lines.append(f"{pad}{slot_name(a0)} = outer({a1});")
        elif op == 0x35:  # _OP_CLONE
            lines.append(f"{pad}{slot_name(a0)} = clone {slot_name(a1)};")
        elif op == 0x04:  # _OP_DLOAD
            lines.append(f"{pad}{slot_name(a0)} = {get_literal(a1)}; {slot_name(a2)} = {get_literal(a3)};")
        elif op == 0x1B:  # _OP_DMOVE
            lines.append(f"{pad}{slot_name(a0)} = {slot_name(a1)}; {slot_name(a2)} = {slot_name(a3)};")
        elif op == 0x07:  # _OP_PREPCALL
            lines.append(f"{pad}// prepcall {slot_name(a1)} on {slot_name(a2)}")
        elif op == 0x08:  # _OP_PREPCALLK
            lines.append(f"{pad}// prepcallk {get_literal(a1)} on {slot_name(a2)}")
        elif op == 0x3B:  # _OP_GETBASE
            lines.append(f"{pad}{slot_name(a0)} = this;")
        elif op == 0x3C:  # _OP_CLOSE
            pass
        elif op == 0x05:  # _OP_TAILCALL
            base = a2
            nargs = a3
            args = ', '.join(slot_name(base + 1 + j) for j in range(nargs))
            lines.append(f"{pad}return {slot_name(base)}({args});")
        else:
            lines.append(f"{pad}// {opname} a0={a0} a1={a1} a2={a2} a3={a3}")

        i += 1

    if func.func_name and func.func_name != '_main_':
        lines.append(f"{pad}}}")

    for sub in func.sub_functions:
        lines.append("")
        lines.extend(decompile_function(sub, indent))

    return lines


def decompile_nut(data):
    if data[:2] != b'\xfa\xfa':
        raise ValueError("Not a Nut bytecode file (missing 0xFAFA header)")

    reader = NutReader(data)
    reader.pos = 2

    sizeof_char = reader.read_i32()
    sizeof_int = reader.read_i32()
    sizeof_float = reader.read_i32()

    try:
        read_marker(reader, b'TRAP')
    except ValueError:
        pass

    func = read_function(reader)
    return func


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file.sk> [file2.sk ...]")
        print(f"       {sys.argv[0]} <directory>")
        sys.exit(1)

    targets = sys.argv[1:]
    files = []
    for t in targets:
        if os.path.isdir(t):
            for root, dirs, fnames in os.walk(t):
                for fn in fnames:
                    if fn.endswith('.sk') or fn.endswith('.sk.bin'):
                        files.append(os.path.join(root, fn))
        else:
            files.append(t)

    for filepath in files:
        with open(filepath, 'rb') as f:
            data = f.read()

        if data[:2] != b'\xfa\xfa':
            continue

        try:
            func = decompile_nut(data)
            lines = decompile_function(func)

            out_path = filepath.rsplit('.', 1)[0] + '.nut.txt'
            with open(out_path, 'w') as f:
                f.write('\n'.join(lines))

            print(f"OK: {filepath} -> {out_path} ({len(lines)} lines)")
        except Exception as e:
            print(f"FAIL: {filepath}: {e}")


if __name__ == '__main__':
    main()
