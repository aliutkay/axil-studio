# hexbus (ZipCPU dbgbus), Verilog-2001 / 2005

The hexbus debug bus RTL from [ZipCPU/dbgbus](https://github.com/ZipCPU/dbgbus), `hexbus/rtl`, commit
`a1043274` (2024-01-18), with one change: every `localparam` is written as `parameter`.

Why: upstream puts `localparam` in the module parameter list (`#( ... )`), which only SystemVerilog
(IEEE 1800-2009 and later) allows. HDL Designer and Libero / Synplify in Verilog 2001 / 2005 mode,
and HDL Designer even in its SystemVerilog mode, stop with "syntax error near localparam".

| File | Changed lines |
|---|---|
| `hbaxil.v` | 1 (`DW`, `AW` in the parameter list) |
| `hbbus.v`, `hbconsole.v` | 1 each (`DW` in the parameter list) |
| `hbexec.v` | 2 (`AW`, `CW` in the parameter list; `F_LGDEPTH` under `ifdef FORMAL`) |
| `hbexecaxi.v` | 6 (`AW`, `DW` in the parameter list; command / response codes in the body) |
| `hbints.v` | 2 (interrupt word constants) |
| `console.v` | 6 (FIFO length, register addresses, `F_LGDEPTH`) |
| other files | none |

Each changed file says so in its header. The values are unchanged: **do not override the former
localparams** (`DW`, `AW`, `CW` …) when instantiating; set only the real parameters
(`C_AXI_ADDR_WIDTH`, `ADDRESS_WIDTH`, …).

## Checks
- Every top (`hbaxil`, `hbbus`, `hbconsole`, `console`) compiles with Icarus Verilog `-g2001` and
  `-g2005`, and Yosys reads the files without `-sv`.
- Yosys equivalence check of each top against the unmodified upstream files (read as SystemVerilog):
  equivalent (`equiv_make` / `equiv_simple` / `equiv_induct`; `hbaxil`: 964 of 964 points proven).

## Which top
- `hbaxil.v`: hexbus with an AXI4-Lite master (`hbexecaxi`). It takes the serial byte stream
  (`i_rx_stb`, `i_rx_byte`, `o_tx_stb`, `o_tx_byte`, `i_tx_busy`); a UART (e.g. wbuart32
  `rxuart` / `txuart`) goes in front of it.
- `hbbus.v` / `hbconsole.v`: Wishbone master versions.

A board-specific top that adds the UART (such as the `hexbus_axil_top` wrapped by
`hdl/hexbus_axil_top_pk.v`) is not part of upstream; its own `localparam`s in a parameter list need
the same change.

## License
LGPL-3.0 or later (see the header of each file); dbgbus: "the simple HEXBUS interface is licensed
under the LGPL". Full text: https://www.gnu.org/licenses/lgpl-3.0.html
