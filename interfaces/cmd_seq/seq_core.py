"""cmd_seq interface: everything that is not GUI (register offsets, status decoding, program
loading steps, symbols from the address map). See hdl/cmd_seq/README.md."""

import re

import seqasm as A

# own registers (offsets from the subsystem base)
ID, CAPS, CTRL, STATUS, ENTRY = 0x00, 0x04, 0x08, 0x0C, 0x10
ERR_CODE, ERR_PC, ERR_ADDR = 0x14, 0x18, 0x1C
IMEM_PTR, IMEM_DATA, IMEM_CNT, IMEM_SUM = 0x20, 0x24, 0x28, 0x2C
DMEM_PTR, DMEM_DATA, SNAP_CNT, TIMER, IRQ_STAT = 0x30, 0x34, 0x38, 0x3C, 0x40
REG_BASE, DMEM_BASE = 0x100, 0x400
ID_SIGNATURE = 0x5EC00000          # ID[31:16]: sequencer, [15:0] version

RUN, STOP, RESET, STEP = 0x01, 0x02, 0x04, 0x08
SNAP_EN, PT_WR_RUN, ERR_HALT, IRQ_EN = 0x10, 0x20, 0x40, 0x80
SETTING_BITS = SNAP_EN | PT_WR_RUN | ERR_HALT | IRQ_EN

LOAD_CHUNK = 32                    # instructions per request while loading

ERR_TEXT = {1: "illegal instruction", 2: "bus error response", 3: "access to the own window",
            4: "call stack overflow / underflow", 5: "bus timeout", 6: "PC outside I-mem",
            7: "D-mem index out of range"}


def c_name(text):
    """The names of AXIL Studio's C header / assembler include (core/cheader.py)."""
    s = re.sub(r"[^0-9A-Za-z_]", "_", str(text)).upper()
    return s if s and not s[0].isdigit() else "_" + s


class Status:
    """STATUS register decoded."""

    def __init__(self, v):
        self.value = v
        self.busy, self.done, self.err, self.paused = (bool(v >> b & 1) for b in range(4))
        self.imem_phase, self.wr_drain, self.rd_drain = (bool(v >> b & 1) for b in (4, 5, 6))
        self.z, self.n, self.ferr, self.v, self.c = (v >> b & 1 for b in range(8, 13))
        self.pc = v >> 16

    @property
    def state(self):
        if self.err:
            return "ERROR"
        if self.done:
            return "DONE"
        if self.paused:
            return "PAUSED"
        if self.busy:
            return "RUNNING"
        return "IDLE"

    def flags_text(self):
        return " ".join(n if f else n.lower() for n, f in
                        (("Z", self.z), ("N", self.n), ("C", self.c), ("V", self.v), ("ERR", self.ferr)))


def caps(v):
    """CAPS -> (imem_aw, dmem_aw, nregs, self_id)."""
    return v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF


def err_text(code):
    if code >= A.KILL_MIN:
        return f"KILL 0x{code:02X}"
    return ERR_TEXT.get(code, f"code {code}")


def symbols(entries):
    """Assembler symbols from AXIL Studio's address map (client.address_map()): the same names
    as the assembler include file: <INST>_BASE, <INST>_<REG>, <INST>_<REG>_<FIELD>_SHIFT/_MASK."""
    out = {}
    for e in entries or []:
        n = c_name(e["name"])
        base = int(e["base"])
        out[f"{n}_BASE"] = base
        for r in sorted(e.get("registers", []), key=lambda r: int(str(r["offset"]), 0)):
            rn = f"{n}_{c_name(r['name'])}"
            if rn in out:                                   # as in the include file: <INST>_REG_<REG>
                rn = f"{n}_REG_{c_name(r['name'])}"
            out[rn] = (base + int(str(r["offset"]), 0)) & 0xFFFFFFFF
            for f in r.get("fields", []):
                lsb, width = int(f["lsb"]), int(f.get("width", 1))
                if f["name"].upper() == r["name"].upper() and lsb == 0 and width == 32:
                    continue
                fn = f"{rn}_{c_name(f['name'])}"
                out[f"{fn}_SHIFT"] = lsb
                out[f"{fn}_MASK"] = ((1 << width) - 1) << lsb
    return out


def assemble(src, imem_aw, dmem_aw, syms=None, base_dir="."):
    """Program text -> (prog, entry, {pc: source line}); raises seqasm.AsmError."""
    asm = A.Assembler(imem_aw, dmem_aw, dict(syms or {}), base_dir)
    prog, entry = asm.parse(src)
    return prog, entry, asm.pc_line


def load_batches(prog, ctrl_settings):
    """Requests (lists of batch ops, offsets from the base) that load a program: STOP and
    RESET (keeping the setting bits), IMEM_PTR = 0, the instruction words in chunks, then read
    back IMEM_CNT, IMEM_SUM and STATUS (check with verify_load)."""
    keep = ctrl_settings & SETTING_BITS
    out = [[("write", CTRL, keep | STOP), ("write", CTRL, keep | RESET), ("write", IMEM_PTR, 0)]]
    words = A.words32(prog)
    step = 2 * LOAD_CHUNK
    for i in range(0, len(words), step):
        out.append([("write", IMEM_DATA, w) for w in words[i:i + step]])
    out.append([("read", IMEM_CNT), ("read", IMEM_SUM), ("read", STATUS)])
    return out


def verify_load(prog, cnt, total, status):
    """Problems of a load (empty = the card holds the program)."""
    errs = []
    if cnt != len(prog):
        errs.append(f"IMEM_CNT = {cnt}, expected {len(prog)}")
    if total != A.imem_sum(prog):
        errs.append(f"IMEM_SUM = 0x{total:08X}, expected 0x{A.imem_sum(prog):08X}")
    if Status(status).imem_phase:
        errs.append("IMEM_PHASE = 1: a word was lost")
    return errs

