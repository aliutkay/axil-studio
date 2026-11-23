"""cmd_seq, the programmable command sequencer (hdl/cmd_seq), as a subsystem type.

A subsystem of type "cmd_seq" is a cmd_seq block in front of the subsystem's contents:
- its own window is the first 64 KB of the subsystem window (cmd_seq decodes address bits
  [23:16]; SELF_ID = base[23:16]), with the register map below;
- everything else in the window is forwarded unchanged to the subsystem's contents, and the
  sequencer's AXI master is the MASTER0 of the subsystem's interconnect.

Settings (IPInstance.seq): IMEM_AW, DMEM_AW, PT_TIMEOUT, BUS_TIMEOUT (cmd_seq parameters).
"""

from .model import EnumValue, Field, IPBlock, Register, hex32

TYPE = "cmd_seq"                 # IPInstance.kind of a command sequencer subsystem
IP_NAME = "cmd_seq"              # register map / IP interface id
OWN_WINDOW = 0x10000             # 64 KB: one value of the IP select field [23:16]
MAX_WINDOW = 0x1000000           # 16 MB: 256 values of [23:16]
VERSION = "2.0.0"
ID_VALUE = 0x5EC00200
DEFAULTS = {"IMEM_AW": 9, "DMEM_AW": 8, "PT_TIMEOUT": 1024, "BUS_TIMEOUT": 1024}
LIMITS = {"IMEM_AW": (1, 16), "DMEM_AW": (1, 13), "PT_TIMEOUT": (2, 0xFFFFFF), "BUS_TIMEOUT": (0, 0xFFFFFF)}
D_REGS = 16                      # D-mem words shown in the register map (the interface shows all)

ERR_CODES = {1: "illegal instruction", 2: "bus error response", 3: "access to the own window",
             4: "call stack overflow / underflow", 5: "bus timeout", 6: "PC outside I-mem"}


def settings(inst):
    """cmd_seq parameters of a subsystem (defaults filled in)."""
    s = dict(DEFAULTS)
    s.update({k: int(v) for k, v in (inst.seq or {}).items() if k in DEFAULTS})
    return s


def self_id(inst):
    return (inst.base >> 16) & 0xFF


def check(project, inst, window):
    """Rule violations of a command sequencer subsystem (empty list = fine)."""
    from axil_studio.i18n import tr
    errs = []
    if inst.base % OWN_WINDOW:
        errs.append(tr("{0}: a command sequencer must start on a 64 KB boundary (base {1})").format(
            inst.name, hex32(inst.base)))
    if inst.self_window != OWN_WINDOW:
        errs.append(tr("{0}: a command sequencer's own window is 64 KB").format(inst.name))
    if window > MAX_WINDOW:
        errs.append(tr("{0}: a command sequencer's window is at most 16 MB").format(inst.name))
    for k, (lo, hi) in LIMITS.items():
        v = settings(inst)[k]
        if not lo <= v <= hi:
            errs.append(tr("{0}: {1} must be {2}..{3}").format(inst.name, k, lo, hi))
    return errs


def _f(name, lsb, width=1, desc="", kind="", values=None, fmt=""):
    return Field(name, lsb, width, desc, kind, [EnumValue(v, r) for v, r in (values or [])], fmt)


def register_map(dmem_aw=8, imem_aw=9):
    """The cmd_seq register map (offsets inside its 64 KB own window)."""
    R = []
    R.append(Register("ID", 0x00, "RO", ID_VALUE, "Sequencer signature and version (2.0)"))
    R.append(Register("CAPS", 0x04, "RO", (16 << 16) | (dmem_aw << 8) | imem_aw, "Build parameters", [
        _f("IMEM_AW", 0, 8, "I-mem address width (instructions = 2^IMEM_AW)", "RO", fmt="udec"),
        _f("DMEM_AW", 8, 8, "D-mem address width (words per bank = 2^DMEM_AW)", "RO", fmt="udec"),
        _f("NREGS", 16, 8, "Working registers", "RO", fmt="udec"),
        _f("SELF_ID", 24, 8, "Own value of address bits [23:16]", "RO")]))
    R.append(Register("CTRL", 0x08, "RW", 0x40, "Control (RUN, STOP, RESET, STEP are pulses)", [
        _f("RUN", 0, 1, "Start at ENTRY, or continue when paused", "PULSE"),
        _f("STOP", 1, 1, "Pause at the next instruction boundary", "PULSE"),
        _f("RESET", 2, 1, "Core to IDLE, R0-R15 cleared (I-mem and D-mem kept)", "PULSE"),
        _f("STEP", 3, 1, "Run one instruction", "PULSE"),
        _f("SNAP_EN", 4, 1, "Two D-mem banks, swapped by SNAP", "RW"),
        _f("PT_WR_RUN", 5, 1, "Allow pass-through writes while the program runs", "RW"),
        _f("ERR_HALT", 6, 1, "Stop on a bus error", "RW"),
        _f("IRQ_EN", 7, 1, "Interrupt output enable", "RW")]))
    R.append(Register("STATUS", 0x0C, "RO", 0, "Core state, flags and PC", [
        _f("BUSY", 0, 1, "Program running", "RO"),
        _f("DONE", 1, 1, "Ended with END", "RO"),
        _f("ERR", 2, 1, "Stopped with an error (ERR_CODE)", "RO"),
        _f("PAUSED", 3, 1, "Paused (STOP, STEP, sys_halt)", "RO"),
        _f("IMEM_PHASE", 4, 1, "Half an instruction written to IMEM_DATA", "RO"),
        _f("WR_DRAIN", 5, 1, "Abandoned write still on the lower bus", "RO"),
        _f("RD_DRAIN", 6, 1, "Abandoned read still on the lower bus", "RO"),
        _f("Z", 8, 1, "Zero flag", "RO"),
        _f("N", 9, 1, "Negative flag", "RO"),
        _f("ERR_FLAG", 10, 1, "A bus error happened (ERR_HALT = 0)", "RO"),
        _f("V", 11, 1, "Overflow flag", "RO"),
        _f("C", 12, 1, "Carry / no-borrow flag", "RO"),
        _f("PC", 16, 16, "Program counter", "RO", fmt="udec")]))
    R.append(Register("ENTRY", 0x10, "RW", 0, "Start address (instruction index)", [
        _f("ENTRY", 0, imem_aw, "Instruction index", "RW", fmt="udec")]))
    R.append(Register("ERR_CODE", 0x14, "RO", 0, "Error code (0x10-0xFF: KILL code)", [
        _f("CODE", 0, 8, "Error", "RO", sorted(ERR_CODES.items()))]))
    R.append(Register("ERR_PC", 0x18, "RO", 0, "PC of the failing instruction", [
        _f("PC", 0, imem_aw, "Instruction index", "RO", fmt="udec")]))
    R.append(Register("ERR_ADDR", 0x1C, "RO", 0, "Address of the last bus error / own-window access"))
    R.append(Register("IMEM_PTR", 0x20, "RW", 0, "I-mem load pointer (a write clears the phase, CNT and SUM)"))
    R.append(Register("IMEM_DATA", 0x24, "WO", 0, "Instruction words: low word, then high word"))
    R.append(Register("IMEM_CNT", 0x28, "RO", 0, "Instructions stored since IMEM_PTR was written"))
    R.append(Register("IMEM_SUM", 0x2C, "RO", 0, "32-bit sum of the accepted IMEM_DATA words"))
    R.append(Register("DMEM_PTR", 0x30, "RW", 0, "D-mem load pointer"))
    R.append(Register("DMEM_DATA", 0x34, "WO", 0, "D[DMEM_PTR] <= data, DMEM_PTR++"))
    R.append(Register("SNAP_CNT", 0x38, "RO", 0, "Number of SNAP instructions"))
    R.append(Register("TIMER", 0x3C, "RO", 0, "Free-running cycle counter"))
    R.append(Register("IRQ_STAT", 0x40, "RW", 0, "Interrupt causes (write 1 to clear)", [
        _f("DONE", 0, 1, "Program ended with END", "W1C"),
        _f("ERR", 1, 1, "Program stopped with an error", "W1C"),
        _f("IRQ", 2, 1, "IRQ instruction", "W1C")]))
    for i in range(16):
        R.append(Register(f"R{i}", 0x100 + 4 * i, "RO", 0, f"Working register R{i}" + (" (always 0)" if i == 0 else "")))
    for i in range(min(D_REGS, 1 << dmem_aw)):
        R.append(Register(f"D{i}", 0x400 + 4 * i, "RW", 0, f"D-mem word {i} (published bank when SNAP_EN = 1)"))
    ip = IPBlock(IP_NAME, 0, "Programmable command sequencer: own registers (hdl/cmd_seq)", R,
                 version=VERSION, panel=IP_NAME)
    return ip


def _has_xbar(project, inst):
    if project is None:
        return len(inst.children) > 1 or bool(inst.xbar_masters)
    from . import addrmap
    return addrmap.has_xbar(project, inst)


def instance_template(inst, project=None):
    """Verilog instance of cmd_seq for a command sequencer subsystem (parameters filled in)."""
    s = settings(inst)
    n = inst.name
    xb = inst.xbar_module
    lines = [
        f"// cmd_seq of subsystem {n}: window {hex32(inst.base)}, own registers {hex32(inst.base)} - "
        f"{hex32(inst.base + OWN_WINDOW - 1)}",
        f"// S_AXI_*: the {n} port of the upper interconnect; M_AXI_*: "
        + (f"MASTER0 of {xb}." if _has_xbar(project, inst) else "the subsystem's only slave (no interconnect)."),
        "// Files: hdl/cmd_seq/rtl/cmd_seq.v, cmd_seq_core.v (HDL Designer: hdl/cmd_seq_pk.v).",
        "cmd_seq #(",
        f"    .SELF_ID     (8'h{self_id(inst):02X}),",
        "    .SEL_LSB     (16),",
        f"    .IMEM_AW     ({s['IMEM_AW']}),",
        f"    .DMEM_AW     ({s['DMEM_AW']}),",
        f"    .PT_TIMEOUT  ({s['PT_TIMEOUT']}),",
        f"    .BUS_TIMEOUT ({s['BUS_TIMEOUT']})",
        f") u_{n.lower()}_seq (",
        "    .S_AXI_ACLK    (clk),",
        "    .S_AXI_ARESETN (rst_n),",
    ]
    for ch in ("AWVALID", "AWREADY", "AWADDR", "AWPROT", "WVALID", "WREADY", "WDATA", "WSTRB",
               "BVALID", "BREADY", "BRESP", "ARVALID", "ARREADY", "ARADDR", "ARPROT",
               "RVALID", "RREADY", "RDATA", "RRESP"):
        lines.append(f"    .S_AXI_{ch:<8}({n.lower()}_s_{ch.lower()}),")
    for ch in ("AWVALID", "AWREADY", "AWADDR", "AWPROT", "WVALID", "WREADY", "WDATA", "WSTRB",
               "BVALID", "BREADY", "BRESP", "ARVALID", "ARREADY", "ARADDR", "ARPROT",
               "RVALID", "RREADY", "RDATA", "RRESP"):
        lines.append(f"    .M_AXI_{ch:<8}({n.lower()}_m_{ch.lower()}),")
    lines += [
        f"    .i_sys_halt    (1'b0),       // level: pause all sequencers",
        f"    .i_sys_run     (1'b0),       // pulse: start all sequencers",
        f"    .i_ev          (8'h00),",
        f"    .o_ev          (),",
        f"    .o_irq         ({n.lower()}_irq)",
        ");",
    ]
    return "\n".join(lines) + "\n"


def template_name(inst):
    return f"{inst.name.lower()}_cmd_seq_inst.v"


def write_templates(project, outdir):
    """<subsystem>_cmd_seq_inst.v for every command sequencer subsystem; returns the paths."""
    import os
    paths = []
    for b in project.bridges():
        if b.sequencer:
            path = os.path.join(outdir, template_name(b))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(instance_template(b, project))
            paths.append(path)
    return paths
