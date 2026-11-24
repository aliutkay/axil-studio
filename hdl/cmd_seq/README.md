# cmd_seq — programmable command sequencer (AXI4-Lite)

`cmd_seq` sits between an upper and a lower AXI4-Lite interconnect. A PC (through the
UART-to-AXI master) loads a program once; the block then drives the IPs below it on its own
and keeps what it measures in its own memory, where the PC or a periodic reader picks it up
without traffic on the lower bus.

It has three jobs:

- **Pass-through bridge.** A request to an address that is not the block's own is forwarded
  unchanged (address, data, strobes, prot) and the lower bus response is returned unchanged.
- **Command sequencer.** Runs 64-bit instructions from I-mem: write, read, wait, loop,
  conditional branch, arithmetic, events and synchronisation.
- **Local data memory (D-mem).** Values read by the program are kept here; the upper master
  reads them from the block's own window.

## In AXIL Studio
cmd_seq is a **subsystem type**: Address Map Editor → *Add subsystem* → type *Command sequencer
(cmd_seq)* (other subsystems stay plain groups of slaves). Its first 64 KB are the registers below
(`SELF_ID` = base[23:16]); the slaves added to the subsystem are behind it. The Interconnect
Configurator writes `<subsystem>_cmd_seq_inst.v` with the parameters, the Debug Bridge shows the
registers under the subsystem's name and opens the **Command sequencer** interface
(`interfaces/cmd_seq`: editor, assembler with the project's address names, load with IMEM_CNT /
IMEM_SUM check, run / step / stop / run to line, registers, D-mem). The register map is also in
`examples/cmd_seq/cmd_seq.axlib.json`; `hdl/cmd_seq_pk.v` is the HDL Designer wrapper. It has no
parameters (a VHDL top would turn them into integer generics): it uses the defaults (`SELF_ID` = 0,
IMEM_AW 9, DMEM_AW 8, timeouts 1024). For another subsystem base or size generate it again, e.g.
`python -m axil_studio.core.axilgen --pack hdl/cmd_seq/rtl/cmd_seq.v --set SELF_ID=8'h10 -o out`.

## Files
| Path | Content |
|---|---|
| `rtl/cmd_seq.v` | Top level: address decoder, CSR, I-mem / D-mem, two master engines with arbiters |
| `rtl/cmd_seq_core.v` | Instruction engine (fetch / decode / execute, R0–R15, flags, call stack) |
| `rtl/cmd_seq_axi4.v` | Wrapper with full AXI4 signal sets (ID, LEN, SIZE, BURST, LAST …) for tools whose bus definition is AXI4 (Libero): single beats on M_AXI, request IDs returned on S_AXI, no bursts accepted |
| `sw/seqasm.py` | Assembler / disassembler (Python 3, standard library only) |
| `sw/examples/*.s` | The four example programs below |
| `verif/` | Reference model, directed and random testbenches, `run.py` |
| `formal/` | Formal properties and `run_formal.py` |

## Address structure
The address is the same everywhere in the system; the block does not translate it.

| Bits | Field | Decoded by |
|---|---|---|
| `[31:24]` | block select | upper interconnect (not looked at by the block) |
| `[23:16]` | IP select, `SELF_ID` (default `0x00`) = the block itself | block and lower interconnect |

The own window is always 64 KB (bits `[23:16]`, fixed in the RTL); AXIL Studio sets `SELF_ID` from
the subsystem base. The window of the block on the upper bus may be smaller than 16 MB; it must
not be larger (addresses would alias).
| `[15:0]` | register offset | target IP or the block |

Example (block 3): `0x0300_0008` CTRL, `0x0300_0400` D[0], `0x0301_0010` IP1 register 0x10
(pass-through). The lower interconnect decodes bits `[23:16]` (or full 32-bit IP bases such as
`0x0301_0000`).

## Own register map (offset `[15:0]`)
| Offset | Name | Access | Description |
|---|---|---|---|
| 0x0000 | ID | RO | `0x5EC0_0200` (sequencer, version 2.0) |
| 0x0004 | CAPS | RO | `[7:0]` IMEM_AW, `[15:8]` DMEM_AW, `[23:16]` registers (16), `[31:24]` SELF_ID |
| 0x0008 | CTRL | RW | b0 RUN, b1 STOP, b2 RESET, b3 STEP (pulses, read 0); b4 SNAP_EN, b5 PT_WR_RUN, b6 ERR_HALT (reset 1), b7 IRQ_EN |
| 0x000C | STATUS | RO | b0 BUSY, b1 DONE, b2 ERR, b3 PAUSED, b4 IMEM_PHASE, b5 WR_DRAIN, b6 RD_DRAIN, `[8]` Z, `[9]` N, `[10]` ERR flag, `[11]` V, `[12]` C, `[31:16]` PC |
| 0x0010 | ENTRY | RW | start address (instruction index) |
| 0x0014 | ERR_CODE | RO | see *Error codes* |
| 0x0018 | ERR_PC | RO | PC of the failing instruction |
| 0x001C | ERR_ADDR | RO | bus address of the last bus error / own-window access |
| 0x0020 | IMEM_PTR | RW | load pointer; a write also clears the phase, CNT and SUM |
| 0x0024 | IMEM_DATA | WO | 1st write `[31:0]`, 2nd write `[63:32]`: stored, PTR++ |
| 0x0028 | IMEM_CNT | RO | instructions stored since IMEM_PTR was written |
| 0x002C | IMEM_SUM | RO | 32-bit sum of the words accepted by IMEM_DATA |
| 0x0030 | DMEM_PTR | RW | D-mem load pointer |
| 0x0034 | DMEM_DATA | WO | D[PTR] ← data, PTR++ |
| 0x0038 | SNAP_CNT | RO | +1 on every SNAP |
| 0x003C | TIMER | RO | free-running 32-bit counter |
| 0x0040 | IRQ_STAT | W1C | b0 DONE, b1 ERR, b2 IRQ instruction (a new event wins over a simultaneous clear) |
| 0x0100–0x013C | R0–R15 | RO | working registers (debug) |
| 0x0400– | D[0]– | RW | D-mem window (published bank when SNAP_EN = 1) |

Writes to the own window must be full 32-bit writes, `WSTRB = 1111` (else SLVERR): a CPU must
not use 8- or 16-bit stores on these registers or on the D-mem window. Write to a read-only register: SLVERR. Unmapped
offset: DECERR. STATUS b5 / b6 show an abandoned transfer still in flight on the lower bus.

## Bus behaviour
- The slave port takes a write when AWVALID and WVALID are both high (allowed by AXI4-Lite and
  fine with axilxbar and the Debug Bridge). A non-compliant master that waits for AWREADY before
  raising WVALID would hang.
- The sequencer has priority on the lower bus; write and read have separate engines and
  arbiters, one transfer each at a time.
- While the program runs (BUSY) pass-through writes get SLVERR and are not sent, unless
  `CTRL.PT_WR_RUN = 1`. Pass-through reads are always allowed.
- A pass-through request answers within `PT_TIMEOUT` cycles. When it times out, SLVERR is
  returned. If the engine had not taken it yet it is never sent; if it was already on the lower
  bus it is finished there and its late response is dropped (STATUS WR_DRAIN / RD_DRAIN). The
  upper port never locks: the own window keeps answering even if a lower slave never responds
  (further pass-through requests then time out too).
- A sequencer transfer that gets no response within `BUS_TIMEOUT` cycles ends with error 5
  (`ERR_HALT = 1`) or sets the ERR flag and continues (`ERR_HALT = 0`).
- `CTRL.RESET` during a transfer abandons it in the same way; the next program never receives
  the old response.

## Instruction format
64 bits, loaded with two 32-bit writes (low word first). Unused fields are 0.

| Bits | Field | Meaning |
|---|---|---|
| `[63:58]` | OP | opcode |
| `[57:54]` | RA | destination register or source of the data written |
| `[53:50]` | RB | address base or second operand register |
| `[49:48]` | MOD | b0: second operand IMM (1) or R[RB] (0); b1: RD result to D-mem (1) or R[RA] (0) |
| `[47:40]` | D | D-mem index, condition code, event number or KILL code |
| `[39:32]` | DLY | cycles to wait after the instruction (0–255) |
| `[31:0]` | IMM | constant, address offset or jump target |

The bus address is always `R[RB] + IMM` (R0 = 0: IMM is the absolute address).

| Op | Assembler | Operation |
|---|---|---|
| 0x08 | `WR [Rb+imm], Ra` | write R[RA] |
| 0x09 | `RD Ra, [Rb+imm]` / `RD D[n], [Rb+imm]` / `RD D[n+Rx], [Rb+imm]` | read into R[RA] or D-mem; indexed: MOD b0 = 1, RA = index register |
| 0x10 | `LDI Ra, imm` | R[RA] ← IMM |
| 0x13 | `MOV Ra, b` | R[RA] ← b |
| 0x11 / 0x12 | `LDD Ra, D[n]` / `STD D[n], Ra` | D-mem ↔ register; indexed `D[n+Rb]` (MOD b0 = 1, index = n + R[RB]) |
| 0x18–0x1F | `ADD SUB AND OR XOR SHL SHR CMP Ra, b` | ALU; all update Z, N, C, V (logic and shifts clear C and V); CMP only the flags |
| 0x20 | `JMP label` | PC ← IMM |
| 0x21 | `BR cond, label` | branch if cond |
| 0x22 | `LOOP Ra, label` | R[RA]−−, branch if not 0 (RA ≠ R0) |
| 0x23 / 0x24 | `CALL label` / `RET` | 4-level call stack |
| 0x00 / 0x01 / 0x02 | `NOP` / `END` / `KILL code` | END: DONE, IRQ_STAT.b0; KILL: error with the code (0x10–0xFF) |
| 0x28 / 0x29 / 0x2A | `WAIT b` / `WAITT b` / `TMR Ra` | wait b cycles / until TIMER ≥ b (wrap-safe) / R[RA] ← TIMER |
| 0x30 | `SNAP` | swap the D-mem banks (SNAP_EN = 1), SNAP_CNT++ |
| 0x31 | `FENCE` | wait for pending transfers (a NOP: one transfer at a time) |
| 0x32 / 0x33 | `SEV n` / `WFE n` | pulse `o_ev[n]` / wait for `i_ev[n]` |
| 0x34 | `IRQ` | IRQ_STAT.b2 |

### Conditions (BR, D field)
| Code | Name | Branch when |
|---|---|---|
| 0 | AL | always |
| 1 | Z (EQ) | result zero |
| 2 | NZ (NE) | result not zero |
| 3 | LT | signed less than (N ≠ V) |
| 4 | GE | signed greater or equal (N = V) |
| 5 | ERR | a bus error happened (ERR_HALT = 0) |
| 6 | LO | unsigned lower (C = 0) |
| 7 | HS | unsigned higher or same (C = 1) |
| 8 | MI | result negative (N = 1): wrap-safe `now − deadline < 0` |
| 9 | PL | result not negative (N = 0) |

Other codes are illegal instructions.

### Error codes (ERR_CODE)
| Code | Error |
|---|---|
| 1 | illegal instruction: undefined opcode or condition, KILL below 0x10, LOOP R0, event number out of range |
| 2 | bus error response (SLVERR / DECERR) with ERR_HALT = 1 |
| 3 | access to the own window from the program (ERR_ADDR = address) |
| 4 | call stack overflow (5th CALL) or RET with an empty stack |
| 5 | bus timeout with ERR_HALT = 1 |
| 6 | PC leaves I-mem: jump target outside, or the next instruction after the last one |
| 7 | D-mem index (n, or n + R[x]) outside D-mem |
| 0x10–0xFF | KILL code |

### Timing
Fetch and decode take 2 cycles; LDD takes one more for the D-mem read; bus instructions add
the lower slave latency. For an exact period use `WAITT`, which does not depend on the bus
latency, and put each `WR` right after a `WAITT`.

## Loading and running
1. `CTRL ← STOP` (or `i_sys_halt`). IMEM_DATA is refused (SLVERR) while the program runs or is
   paused, and when I-mem is full.
2. `IMEM_PTR ← 0` (clears the phase, CNT and SUM).
3. For each instruction `IMEM_DATA ← low word, high word`.
4. Compare `IMEM_CNT` / `IMEM_SUM` with the assembler output; `STATUS.IMEM_PHASE = 1` means a
   lost word. On a difference start again at step 2.
5. Optionally D-mem parameters (`DMEM_PTR`, `DMEM_DATA`) with SNAP_EN = 0 (see below), then `ENTRY`, `CTRL ← RUN`
   (or `i_sys_run` for all blocks together).

STOP pauses at the next instruction boundary (a running transfer finishes first; WAIT, WAITT,
WFE and DLY are cut short); RUN continues. STEP runs one instruction from PAUSED. RESET
returns the core to IDLE and clears R0–R15; I-mem and D-mem are kept.

With SNAP_EN = 1 the program uses the working bank and the master the published bank, for reads
**and writes**. SNAP swaps them, so the new working bank holds the values from two SNAPs ago: a
program that publishes a set of values must write all of them before every SNAP. A value the
master writes into D-mem while SNAP_EN = 1 lands in the published bank: the program sees it only
after the next SNAP, and may then overwrite it. Write parameters for the program (e.g. the step of
`sweep.s`) with SNAP_EN = 0, or before setting SNAP_EN; live parameter updates need SNAP_EN = 0.

## Assembler
```
python3 sw/seqasm.py prog.s                          # prog.hex ($readmemh) + prog.lst
python3 sw/seqasm.py prog.s -f axi --base 0x0300 --run -o prog.axi
python3 sw/seqasm.py -d prog.hex                     # disassemble
```
D-mem operands: `D[n]`, indexed `D[n+Rx]`, `D[Rx]`, `D[Rx+n]` (n = 0–255; the index is checked
at run time, error 7). Syntax: `;` / `#` comments, `label:`, `.equ NAME value`, `.entry label`, `.word64 value`,
`.include "file"` (`.equ` lines, e.g. the Address Map Editor's *Assembler include*),
numbers in decimal / `0x` / `0b` with `_` separators, `+` / `-` expressions, `@n` for DLY.
The `axi` list has `W addr data` and `W2 addr lo hi` lines and starts with the expected
IMEM_CNT / IMEM_SUM.

## Examples (`sw/examples`)
- `pwm.s`: exact-period PWM with `TMR` / `WAITT`.
- `measure.s`: periodic ADC reading published with `SNAP` (run with `CTRL = 0x51` = ERR_HALT b6 + SNAP_EN b4 + RUN b0).
- `sweep.s`: duty sweep with a step written by the master into D[0].
- `poll.s`: polling with a timeout. The deadline test uses `BR MI` (sign of `now − deadline`);
  `BR LT` is a true signed compare and would fail when the timer crosses 0x8000_0000.

## Verification
```
python3 verif/run.py              # directed + random + formal (about 1 minute)
python3 verif/run.py random -n 2000
python3 formal/run_formal.py prove
```

| Layer | What it shows | Where |
|---|---|---|
| Formal, top level | Both AXI4-Lite ports follow the protocol; one transfer at a time per direction; the own window never reaches the lower bus; pass-through address / data / strobes / prot are the accepted request; every response goes to the request that caused it (core or pass-through, also after a timeout or RESET); a pass-through write refused before the engine took it is never sent; writes are not taken while closed; every upstream request is answered within `PT_TIMEOUT + 6` cycles whatever the lower slaves do; an IRQ event is never lost to a simultaneous W1C | `formal/cmd_seq_formal.vh`, `faxil_check.v` |
| Formal, core | For every instruction stream: one request at a time, stable address / data until done, a request ends only by done, timeout or RESET; never the own window; STOP / sys_halt pause within `BUS_TIMEOUT + 4` cycles; stack and state invariants; R0 = 0 | `rtl/cmd_seq_core.v` (`ifdef FORMAL`) |
| Random co-simulation | Instruction semantics: random terminating programs (loops, branches, calls, ALU edge values, D-mem, SNAP, events, bus errors, injected errors) run on the RTL with random slave stalls and random pass-through traffic, and on `verif/seqmodel.py` (written from this document). R0–R15, flags, PC, error registers, both D-mem banks, IRQ_STAT, SNAP_CNT, event counts, lower-bus RAM and the ordered list of lower-bus transfers must be equal | `verif/random_test.py`, `tb_random.v` |
| Directed | Timeouts, abandoned transfers, STOP / STEP / RESET timing, loader and CSR rules, pass-through integrity, SNAP banks, IRQ, sys_halt / sys_run, PC overflow | `verif/tb_directed.v` (105 checks) |

The formal proofs are unbounded (k-induction) and use small parameters (`IMEM_AW = 3`,
`DMEM_AW = 2`, timeouts 4); the logic does not depend on the sizes. The top-level proof
replaces the core by `formal/cmd_seq_core_abs.v`, which issues any request sequence allowed by
the core proof, so it covers every program. `cover` runs check that the properties are not
vacuous (completed transfers, abandoned transfers, timeouts, errors, pause are reachable).
Instruction semantics are checked by the reference model, not by formal.

The checks were also run against deliberately broken RTL (old one-cycle-late LDD, LT = N only,
C flag inverted, abandoned response delivered, timed-out pass-through write still taken, W1C
beating a new event, missing done gating, missing pass-through timeout, missing stack check):
each one is caught.

### Tools
- Python 3.8+ and Icarus Verilog (iverilog 11+) for the simulations.
- Yosys with `yosys-smtbmc` and a solver for formal: yices or bitwuzla, or the bitwuzla Python
  package (`pip install bitwuzla`, used through `formal/bitwuzla_smt2.py`). Old boolector 1.x /
  z3 4.8 packages are too slow or incompatible.
- macOS: [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) (iverilog, yosys,
  yices, bitwuzla; Apple silicon and Intel), or `brew install icarus-verilog yosys` and
  `pip3 install bitwuzla`. Linux: the same, or the distribution packages plus
  `pip install bitwuzla`. Windows: simulations with Icarus Verilog for Windows; formal under WSL.

## Synthesis
- I-mem (2^IMEM_AW × 64 bit, one write and one read port) and D-mem (2 × 2^DMEM_AW × 32 bit, two
  read/write ports, synchronous reads) are written for block RAM inference.
- Both carry `syn_ramstyle = "lsram"` (Synplify / Libero: LSRAM RAM1K20 instead of many RAM64x12
  uSRAMs) and `ram_style = "block"`. With the defaults (IMEM_AW = 9, DMEM_AW = 8) that is about
  2 LSRAMs for I-mem and 2 for D-mem. For very small memories `"uram"` may fit better; change the
  attribute, or override it in the Synplify constraints:
  `define_attribute {i:dmem} syn_ramstyle {uram}`.
- All files are Verilog-2001 (`cmd_seq_axi4.v`, `cmd_seq.v`, `cmd_seq_core.v`).

## D-mem size
The D field is 8 bits: `D[n]` reaches the first 256 words. With `DMEM_AW > 8` the rest is reached by
indexed access (`D[n+Rx]`) and by the master. `DMEM_AW` is at most 13 (the window must fit in the
64 KB own window).

## Changes from the first (VHDL) version
- Verilog; ports named `S_AXI_*` / `M_AXI_*` with `AWPROT` / `ARPROT` forwarded.
- LDD returned the D-mem word of the previous D-mem access (read one cycle early).
- CMP / BR LT used only N; now LT / GE are signed (N ≠ V), with new C / V flags and LO, HS, MI,
  PL conditions; undefined conditions are illegal instead of "not taken".
- A response to an abandoned transfer (after RESET) could be taken by the next request.
- A pass-through write that timed out could still be sent later; a lower slave that never
  answered locked the upstream write channel.
- Call stack overflow / underflow and PC leaving I-mem were not detected (error codes 4 and 6).
- KILL codes below 0x10 clashed with the system error codes.
- D-mem index wider than 8 bits (`DMEM_AW > 8`) selected bits past the D field (X in simulation);
  indexed D-mem addressing added (error 7 for an index outside D-mem); `SEL_LSB` removed (the own
  window decode only worked with 16).
- A new IRQ event was lost when it came together with a W1C of IRQ_STAT.
- Sequencer bus timeout (error 5); I-mem / D-mem loaders refuse writes past the end; I-mem
  loading is refused while paused; own-window writes need full strobes.

## License
Written for AXIL Studio; contains no third-party code.
