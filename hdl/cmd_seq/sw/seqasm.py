#!/usr/bin/env python3
"""seqasm.py - assembler / disassembler for the cmd_seq command sequencer.

Instruction (64 bit):
  [63:58] OP  [57:54] RA  [53:50] RB  [49:48] MOD  [47:40] D  [39:32] DLY  [31:0] IMM

Usage:
  python3 seqasm.py prog.s                        # prog.hex + prog.lst
  python3 seqasm.py prog.s -f axi --base 0x0300 --run -o prog.axi
  python3 seqasm.py -d prog.hex                   # disassemble

Syntax:
  ; or # comment        label:              .equ NAME value     .entry label
  .word64 value         raw 64-bit instruction word
  WR   [Rb+imm], Ra     WR [imm], Ra        RD Ra, [Rb+imm]     RD D[n], [Rb+imm]
  LDI  Ra, imm          MOV Ra, Rb|imm      LDD Ra, D[n]        STD D[n], Ra
  ADD/SUB/AND/OR/XOR/SHL/SHR/CMP Ra, Rb|imm
  JMP label             BR cond, label      LOOP Ra, label      CALL label     RET
  WAIT Rb|imm           WAITT Rb|imm        TMR Ra
  SNAP  FENCE  SEV n  WFE n  IRQ  NOP  END  KILL code (0x10..0xFF)
  Append  @n  to set the DLY field (0..255).
  Conditions: AL Z NZ LT GE ERR LO HS MI PL  (EQ = Z, NE = NZ)

The hex file has one 64-bit instruction per line ($readmemh compatible).
"""
import argparse
import re
import sys

OPS = {
    "NOP": 0x00, "END": 0x01, "KILL": 0x02,
    "WR": 0x08, "RD": 0x09,
    "LDI": 0x10, "LDD": 0x11, "STD": 0x12, "MOV": 0x13,
    "ADD": 0x18, "SUB": 0x19, "AND": 0x1A, "OR": 0x1B,
    "XOR": 0x1C, "SHL": 0x1D, "SHR": 0x1E, "CMP": 0x1F,
    "JMP": 0x20, "BR": 0x21, "LOOP": 0x22, "CALL": 0x23, "RET": 0x24,
    "WAIT": 0x28, "WAITT": 0x29, "TMR": 0x2A,
    "SNAP": 0x30, "FENCE": 0x31, "SEV": 0x32, "WFE": 0x33, "IRQ": 0x34,
}
OPNAME = {v: k for k, v in OPS.items()}
CONDS = {"AL": 0, "Z": 1, "NZ": 2, "LT": 3, "GE": 4, "ERR": 5, "LO": 6, "HS": 7,
         "MI": 8, "PL": 9}
COND_ALIASES = {"EQ": "Z", "NE": "NZ"}
CONDNAME = {v: k for k, v in CONDS.items()}
ALU = {"ADD", "SUB", "AND", "OR", "XOR", "SHL", "SHR", "CMP"}
NO_OPERAND = ("NOP", "END", "RET", "SNAP", "FENCE", "IRQ")

# error codes (ERR_CODE)
E_ILLEGAL, E_BUS, E_SELF, E_STACK, E_TIMEOUT, E_PC = 1, 2, 3, 4, 5, 6
KILL_MIN = 0x10

# CSR offsets (from the block base)
A_CTRL, A_ENTRY, A_IMEM_PTR, A_IMEM_DATA = 0x0008, 0x0010, 0x0020, 0x0024
CTRL_RUN, CTRL_SNAP_EN, CTRL_PT_WR_RUN, CTRL_ERR_HALT, CTRL_IRQ_EN = 0x01, 0x10, 0x20, 0x40, 0x80


class AsmError(Exception):
    pass


def encode(op, ra=0, rb=0, mod=0, d=0, dly=0, imm=0):
    return ((op & 0x3F) << 58 | (ra & 0xF) << 54 | (rb & 0xF) << 50 |
            (mod & 0x3) << 48 | (d & 0xFF) << 40 | (dly & 0xFF) << 32 |
            (imm & 0xFFFFFFFF))


def fields(w):
    """64-bit word -> dict of the instruction fields."""
    return {"op": (w >> 58) & 0x3F, "ra": (w >> 54) & 0xF, "rb": (w >> 50) & 0xF,
            "mod": (w >> 48) & 3, "d": (w >> 40) & 0xFF, "dly": (w >> 32) & 0xFF,
            "imm": w & 0xFFFFFFFF}


def words32(prog):
    """Program [(pc, word, src)] -> the 32-bit words written to IMEM_DATA (low first)."""
    out = []
    for _, w, _ in prog:
        out += [w & 0xFFFFFFFF, w >> 32]
    return out


def imem_sum(prog):
    return sum(words32(prog)) & 0xFFFFFFFF


class Assembler:
    def __init__(self, imem_aw=9, dmem_aw=8, symbols=None):
        self.imem_size = 1 << imem_aw
        self.dmem_size = 1 << dmem_aw
        self.syms = dict(symbols or {})
        self.entry = None

    # ---- numbers and expressions ------------------------------------------------
    def value(self, tok, line):
        tok = tok.strip()
        if not tok:
            raise AsmError(f"{line}: empty expression")
        parts = re.split(r"(?<=[^+\-])([+\-])", tok)
        total, sign = 0, 1
        for p in parts:
            p = p.strip()
            if p == "+":
                sign = 1
                continue
            if p == "-":
                sign = -1
                continue
            total += sign * self.atom(p, line)
        return total

    def atom(self, t, line):
        if re.fullmatch(r"-?(0x[0-9a-fA-F_]+|0b[01_]+|\d[\d_]*)", t):
            t = t.replace("_", "")
        try:
            return int(t, 0)
        except ValueError:
            pass
        if t in self.syms:
            return self.syms[t]
        raise AsmError(f"{line}: undefined symbol '{t}'")

    def reg(self, t, line):
        m = re.fullmatch(r"[Rr](\d+)", t.strip())
        if not m or int(m.group(1)) > 15:
            raise AsmError(f"{line}: register expected (R0..R15), got '{t}'")
        return int(m.group(1))

    @staticmethod
    def is_reg(t):
        return re.fullmatch(r"[Rr](\d+)", t.strip()) is not None

    def dref(self, t, line):
        m = re.fullmatch(r"[Dd]\[(.+)\]", t.strip())
        if not m:
            raise AsmError(f"{line}: D[n] expected, got '{t}'")
        n = self.value(m.group(1), line)
        if not 0 <= n < min(self.dmem_size, 256):
            raise AsmError(f"{line}: D index out of range ({n})")
        return n

    def mem(self, t, line):
        """[Rb+imm] | [imm] | [Rb]  ->  (rb, imm)"""
        m = re.fullmatch(r"\[(.+)\]", t.strip())
        if not m:
            raise AsmError(f"{line}: [address] expected, got '{t}'")
        inner = m.group(1).strip()
        m2 = re.match(r"([Rr]\d+)\s*(?:([+\-])\s*(.+))?$", inner)
        if m2:
            rb = self.reg(m2.group(1), line)
            imm = 0
            if m2.group(2):
                imm = self.value(m2.group(3), line)
                if m2.group(2) == "-":
                    imm = -imm
            return rb, imm
        return 0, self.value(inner, line)

    def operand_b(self, t, line):
        """Rb | imm -> (rb, mod_b0, imm)"""
        if self.is_reg(t):
            return self.reg(t, line), 0, 0
        return 0, 1, self.value(t, line)

    def target(self, t, line):
        v = self.value(t, line)
        if not 0 <= v < self.imem_size:
            raise AsmError(f"{line}: jump target outside I-mem ({v})")
        return v

    # ---- parsing ------------------------------------------------------------------
    @staticmethod
    def split_ops(s):
        out, depth, cur = [], 0, ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
        return out

    def parse(self, src):
        """Pass 1: labels and .equ; pass 2: encoding. Returns ([(pc, word, src)], entry)."""
        stmts, pc = [], 0
        for no, raw in enumerate(src.splitlines(), 1):
            line = re.split(r"[;#]", raw, maxsplit=1)[0].strip()
            loc = f"line {no}"
            while True:
                m = re.match(r"([A-Za-z_]\w*)\s*:\s*(.*)$", line)
                if not m:
                    break
                if m.group(1) in self.syms:
                    raise AsmError(f"{loc}: '{m.group(1)}' defined twice")
                self.syms[m.group(1)] = pc
                line = m.group(2)
            if not line:
                continue
            low = line.lower()
            if low.startswith(".equ"):
                parts = line.split(None, 2)
                if len(parts) < 3:
                    raise AsmError(f"{loc}: .equ NAME value")
                name = parts[1].rstrip(",")
                if name in self.syms:
                    raise AsmError(f"{loc}: '{name}' defined twice")
                self.syms[name] = self.value(parts[2].lstrip(", "), loc)
                continue
            if low.startswith(".entry"):
                self.entry = (line.split(None, 1)[1].strip(), loc)
                continue
            stmts.append((pc, line, loc, raw.strip()))
            pc += 1
        if pc > self.imem_size:
            raise AsmError(f"program has {pc} instructions, I-mem holds {self.imem_size}")
        prog = [(p, self.encode_line(line, loc), raw) for p, line, loc, raw in stmts]
        entry = self.target(self.entry[0], self.entry[1]) if self.entry else 0
        return prog, entry

    def encode_line(self, line, loc):
        dly = 0
        m = re.search(r"@\s*(\S+)\s*$", line)
        if m:
            dly = self.value(m.group(1), loc)
            if not 0 <= dly <= 255:
                raise AsmError(f"{loc}: DLY must be 0..255 ({dly})")
            line = line[:m.start()].strip()
        parts = line.split(None, 1)
        mn = parts[0].upper()
        ops = self.split_ops(parts[1]) if len(parts) > 1 else []
        if mn == ".WORD64":
            if len(ops) != 1:
                raise AsmError(f"{loc}: .word64 value")
            return self.value(ops[0], loc) & (2**64 - 1)
        if mn not in OPS:
            raise AsmError(f"{loc}: unknown instruction '{parts[0]}'")
        op = OPS[mn]

        def need(n):
            if len(ops) != n:
                raise AsmError(f"{loc}: {mn} takes {n} operand(s), {len(ops)} given")

        if mn in NO_OPERAND:
            need(0)
            return encode(op, dly=dly)
        if mn == "KILL":
            need(1)
            code = self.value(ops[0], loc)
            if not KILL_MIN <= code <= 0xFF:
                raise AsmError(f"{loc}: KILL code must be 0x10..0xFF (lower codes are system errors)")
            return encode(op, d=code, dly=dly)
        if mn == "WR":
            need(2)
            rb, imm = self.mem(ops[0], loc)
            return encode(op, ra=self.reg(ops[1], loc), rb=rb, dly=dly, imm=imm)
        if mn == "RD":
            need(2)
            rb, imm = self.mem(ops[1], loc)
            if ops[0].strip().upper().startswith("D["):
                return encode(op, rb=rb, mod=2, d=self.dref(ops[0], loc), dly=dly, imm=imm)
            return encode(op, ra=self.reg(ops[0], loc), rb=rb, dly=dly, imm=imm)
        if mn == "LDI":
            need(2)
            return encode(op, ra=self.reg(ops[0], loc), mod=1, dly=dly, imm=self.value(ops[1], loc))
        if mn == "LDD":
            need(2)
            return encode(op, ra=self.reg(ops[0], loc), d=self.dref(ops[1], loc), dly=dly)
        if mn == "STD":
            need(2)
            return encode(op, ra=self.reg(ops[1], loc), d=self.dref(ops[0], loc), dly=dly)
        if mn == "MOV" or mn in ALU:
            need(2)
            rb, mb, imm = self.operand_b(ops[1], loc)
            return encode(op, ra=self.reg(ops[0], loc), rb=rb, mod=mb, dly=dly, imm=imm)
        if mn in ("JMP", "CALL"):
            need(1)
            return encode(op, dly=dly, imm=self.target(ops[0], loc))
        if mn == "BR":
            need(2)
            c = ops[0].strip().upper()
            c = COND_ALIASES.get(c, c)
            if c not in CONDS:
                raise AsmError(f"{loc}: unknown condition '{ops[0]}'")
            return encode(op, d=CONDS[c], dly=dly, imm=self.target(ops[1], loc))
        if mn == "LOOP":
            need(2)
            ra = self.reg(ops[0], loc)
            if ra == 0:
                raise AsmError(f"{loc}: LOOP needs a counter register other than R0")
            return encode(op, ra=ra, dly=dly, imm=self.target(ops[1], loc))
        if mn in ("WAIT", "WAITT"):
            need(1)
            rb, mb, imm = self.operand_b(ops[0], loc)
            return encode(op, rb=rb, mod=mb, dly=dly, imm=imm)
        if mn == "TMR":
            need(1)
            return encode(op, ra=self.reg(ops[0], loc), dly=dly)
        if mn in ("SEV", "WFE"):
            need(1)
            n = self.value(ops[0], loc)
            if not 0 <= n <= 7:
                raise AsmError(f"{loc}: event number must be 0..7")
            return encode(op, d=n, dly=dly)
        raise AsmError(f"{loc}: cannot encode '{mn}'")


def assemble(src, imem_aw=9, dmem_aw=8, symbols=None):
    """Assemble source text. Returns ([(pc, word, src_line)], entry)."""
    return Assembler(imem_aw, dmem_aw, symbols).parse(src)


def disasm(w):
    f = fields(w)
    op, ra, rb, mod, d, dly, imm = (f[k] for k in ("op", "ra", "rb", "mod", "d", "dly", "imm"))
    mn = OPNAME.get(op)
    b = f"0x{imm:X}" if mod & 1 else f"R{rb}"
    addr = f"[R{rb}+0x{imm:08X}]" if rb else f"[0x{imm:08X}]"
    if mn is None:
        return f".word64 0x{w:016X}"
    if mn in NO_OPERAND:
        s = mn
    elif mn == "KILL":
        s = f"KILL 0x{d:02X}"
    elif mn == "WR":
        s = f"WR {addr}, R{ra}"
    elif mn == "RD":
        s = f"RD {'D[%d]' % d if mod & 2 else 'R%d' % ra}, {addr}"
    elif mn == "LDI":
        s = f"LDI R{ra}, 0x{imm:X}"
    elif mn == "LDD":
        s = f"LDD R{ra}, D[{d}]"
    elif mn == "STD":
        s = f"STD D[{d}], R{ra}"
    elif mn == "MOV" or mn in ALU:
        s = f"{mn} R{ra}, {b}"
    elif mn in ("JMP", "CALL"):
        s = f"{mn} {imm}"
    elif mn == "BR":
        s = f"BR {CONDNAME.get(d, d)}, {imm}"
    elif mn == "LOOP":
        s = f"LOOP R{ra}, {imm}"
    elif mn in ("WAIT", "WAITT"):
        s = f"{mn} {b}"
    elif mn == "TMR":
        s = f"TMR R{ra}"
    else:
        s = f"{mn} {d}"
    return s + (f" @{dly}" if dly else "")


def hex_text(prog):
    return "".join(f"{w:016X}\n" for _, w, _ in prog)


def axi_text(prog, entry, base, run=False):
    """Write list for a UART-AXI master: 'W addr data' and 'W2 addr lo hi' lines."""
    out = f"# {len(prog)} instructions, expected IMEM_CNT={len(prog)} IMEM_SUM=0x{imem_sum(prog):08X}\n"
    out += f"W  0x{base | A_IMEM_PTR:08X} 0x00000000\n"
    for pc, w, raw in prog:
        out += (f"W2 0x{base | A_IMEM_DATA:08X} 0x{w & 0xFFFFFFFF:08X} "
                f"0x{w >> 32:08X}   # {pc}: {raw}\n")
    if run:
        out += f"W  0x{base | A_ENTRY:08X} 0x{entry:08X}\n"
        out += f"W  0x{base | A_CTRL:08X} 0x{CTRL_RUN | CTRL_ERR_HALT:08X}   # RUN + ERR_HALT\n"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="cmd_seq assembler")
    ap.add_argument("src")
    ap.add_argument("-f", "--format", choices=["hex", "axi"], default="hex")
    ap.add_argument("-o", "--out")
    ap.add_argument("--base", default="0x0300", help="block address bits [31:16], e.g. 0x0300")
    ap.add_argument("--imem-aw", type=int, default=9)
    ap.add_argument("--dmem-aw", type=int, default=8)
    ap.add_argument("--run", action="store_true", help="append ENTRY + RUN to the axi list")
    ap.add_argument("-d", "--disasm", action="store_true", help="disassemble a hex file")
    a = ap.parse_args(argv)

    if a.disasm:
        with open(a.src, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.split("//")[0].strip()
                if line:
                    print(f"{i:4d}: {disasm(int(line, 16))}")
        return 0

    try:
        with open(a.src, encoding="utf-8") as fh:
            prog, entry = assemble(fh.read(), a.imem_aw, a.dmem_aw)
    except AsmError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    stem = a.src.rsplit(".", 1)[0]
    if a.format == "hex":
        out = hex_text(prog)
        lst = [f"; {a.src}: {len(prog)} instructions, IMEM_SUM=0x{imem_sum(prog):08X}, ENTRY={entry}"]
        lst += [f"{pc:4d}  {w:016X}  {disasm(w):34s} ; {raw}" for pc, w, raw in prog]
        with open(stem + ".lst", "w", encoding="utf-8") as fh:
            fh.write("\n".join(lst) + "\n")
    else:
        out = axi_text(prog, entry, int(a.base, 0) << 16, a.run)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    elif a.format == "hex":
        with open(stem + ".hex", "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)
    print(f"{len(prog)} instructions, IMEM_SUM=0x{imem_sum(prog):08X}, ENTRY={entry}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
