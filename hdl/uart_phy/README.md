# axiluart_phy — AXI4-Lite UART with a configurable line interface

ZipCPU `axiluart` (patched as `axiluart_v2`) behind a line interface adapter: DE / /RE sequencing
for RS-422 / RS-485 transceivers, half duplex, echo, polarity and TX / RX enable.

In AXIL Studio the IP is in the `examples/uart_phy/uart_phy.axlib.json` library. Its register map
names the **UART interface** (`"panel": "axiluart_phy"`, the application in `interfaces/axiluart_phy`):
select the IP in the UART-to-AXI Debug Bridge and open it for line settings, a terminal and hardware
validation.

## Files
| Path | Content |
|---|---|
| `rtl/axiluart_phy.v` | Top level: AXI4-Lite split (UART 0x00–0x0C, PHY 0x10–0x18) |
| `rtl/uart_phy_adapter.v` | DE / RE sequencing, duplex, echo, polarity, TX/RX enable |
| `rtl/axiluart_v2.v` | Module `axiluart_v2`: ZipCPU axiluart with `patches/axiluart_v2.patch` applied |
| `rtl/txuart_v2.v` | Module `txuart_v2`: ZipCPU txuart with corrected parity polarity (`patches/txuart_v2.patch`) |
| `tb/tb_axiluart_phy.v` | Self-checking testbench (Icarus Verilog) |
| `tb/tb_formats_loopback.v` | Gapless 100-byte TX→RX bursts in 5E1, 6O2, 7N1, 8N2, 8E1, 7O1 |
| `tb/tb_tx_parity.v` | Decodes the TX waveform and checks the parity bit against the standard |
| `../axiluart_phy_pk.v` | HDL Designer wrapper with packed `S_AXI_REQ/RSP` ports |
| `../../examples/uart_phy/*.axcfg.json` | Line presets (RS-232, RS-422, RS-485 2/4-wire) for the Debug Bridge config list |

Also required from ZipCPU/wbuart32 `rtl/`: `rxuart.v`, `rxuartlite.v`, `txuartlite.v`, `ufifo.v`
and `skidbuffer.v` (`txuart.v` is replaced by `txuart_v2.v`).

## 4-wire link from two IPs
Two axiluart_phy instances, each with its own 2-wire RS-485 transceiver, make one 4-wire link: one
IP sends (PHY_CTRL: DE high or DE while transmitting, TX on, RX off) and the other receives (DE low,
TX off, RX on). The directions are swapped by writing PHY_CTRL (the receiver first). The UART
interface pairs two instances, swaps the directions and validates both directions.

## axiluart_v2 changes
- Module and file renamed to `axiluart_v2` so it cannot clash with the original
- Instantiates `txuart_v2`: the original txuart sends odd parity when even is selected and vice
  versa (SETUP bit 24 used as the parity seed), while rxuart and the spec use 1 = even. Mark/space
  parity was not affected.
- `i_tx_go` input gates the start of each byte (DE lead time without a delay line)
- `o_tx_active` output = TX FIFO not empty or transmitter busy
- RX FIFO is 10 bits wide: each byte carries its own parity / frame error bits (RXREG bits 13 and 14)
- RXREG bit 9 (parity), bit 10 (frame) and bit 12 (FIFO overflow) are sticky and captured whenever
  rxuart reports them (the original misses parity and frame errors with one stop bit)
- SETUP is reset to INITIAL_SETUP on S_AXI_ARESETN (original relies on `initial` only)

## Register map
| Offset | Name | Notes |
|---|---|---|
| 0x00 | SETUP | `[23:0]` ACLK/baud, `[26:24]` parity, `[27]` 2 stop, `[29:28]` data bits, `[30]` no flow |
| 0x04 | FIFO | RX status `[15:0]`, TX status `[31:16]` |
| 0x08 | RXREG | `[7:0]` data, `[8]` empty, `[9]` PERR (sticky), `[10]` FERR (sticky), `[11]` break, `[12]` OVFL (sticky), `[13]` parity error of this byte, `[14]` frame error of this byte; write 1 to bit 9/10 clears, bit 12 resets RX |
| 0x0C | TXREG | `[7:0]` data, `[9]` break, `[12]` TX reset |
| 0x10 | PHY_CTRL | `[1:0]` DE mode, `[2]` half, `[3]` echo, `[4]` TX inv, `[5]` RX inv, `[6]` TX en, `[7]` RX en, `[31]` apply now |
| 0x14 | PHY_TIMING | `[15:0]` lead, `[31:16]` tail (ACLK cycles) |
| 0x18 | PHY_STATUS | `[4:0]` capabilities, `[8]` driving, `[9]` DE, `[10]` /RE, `[11]` pending, `[12]` CFG_ERR (W1C) |

## Line presets (PHY_CTRL)
| Line | PHY_CTRL | PHY_TIMING |
|---|---|---|
| TTL / RS-232 | `0xC0` | `0` |
| RS-422 point-to-point | `0xC1` | `0` |
| RS-422 multidrop / RS-485 4-wire slave | `0xC2` | lead/tail |
| RS-485 2-wire | `0xC6` | lead/tail |
| RS-485 2-wire with echo | `0xCE` | lead/tail |
| LIN / tri-state buffer single-wire | `0xCE` (DE_MODE 0 if no enable pin) | tail ≥ 1 bit |

## Simulation
```
iverilog -g2012 -o sim tb/tb_axiluart_phy.v rtl/*.v <wbuart32>/rtl/{rxuart,rxuartlite,txuartlite,ufifo,skidbuffer}.v
vvp sim
```

## Hardware validation
The *Validation* tab of the UART interface runs the board tests (register access, data in every
baud × format, bursts, parity / frame / break / overflow injection, DE timing, 2-wire echo, DE
lead / tail sweep) through the Debug Bridge connection, with an optional PC adapter on the UART line.

## Libero
- Create the HDL+ core from `axiluart_phy`; bus interface AXI4 slave, prefix `S_AXI_`.
- Connect interconnect address bits `[4:0]` only.
- Add a pull-up on the transceiver RO pin (RO is tri-stated while /RE is high).

## License
`rtl/axiluart_v2.v`, `rtl/txuart_v2.v`, the patches and the wbuart32 files are GPLv3
(Gisselquist Technology, ZipCPU). A design that includes them is subject to the GPLv3.
