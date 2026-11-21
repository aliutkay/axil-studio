# example_nopk

IP version: `1.0.0`

Example IP register wrapper

Addresses below are offsets from the slave base address set on the interconnect.  
Decoded address bits: `[4:2]` (block repeats every 0x20 bytes)

## Registers

| Offset | Register | Access | Reset | Description |
|---|---|---|---|---|
| 0x00 | ID | RO | 0xA5A50102 | IP identification |
| 0x04 | CTRL | RW | 0x01B20004 | Control |
| 0x08 | STATUS | RO | 0x00000000 | Status |
| 0x0C | IRQ | RW | 0x00000000 | Event flags |
| 0x14 | DATA | RW | 0x00000000 | Data FIFO |

### 0x00 ID

IP identification

| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [31:16] | MAGIC | CONST | 0xA5A5 | `—` | Constant pattern |
| [15:0] | VERSION | CONST | 0x102 | `—` | Version 1.2 |

### 0x04 CTRL

Control

| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [31:16] | DIVIDER | RW | 0x1B2 | `o_ctrl_divider` | Clock divider |
| [9] | SOFT_RESET | PULSE | 0 | `o_ctrl_soft_reset` | Reset the IP |
| [8] | START | PULSE | 0 | `o_ctrl_start` | Start a transfer |
| [3:1] | MODE | RW | 0x2 | `o_ctrl_mode` | Operating mode<br>Other codes are reserved. |
| [0] | ENABLE | RW | 0x0 | `o_ctrl_enable` | IP enable |

**START** [8] values:

| Value | When read | When written |
|---|---|---|
| 0x1 | — | Starts one transfer (ignored while BUSY=1) |

**MODE** [3:1] values:

| Value | When read | When written |
|---|---|---|
| 0x0 | Idle | Stop |
| 0x1 | Single shot | Run one transfer per START |
| 0x2 | Continuous | Run continuously |
| 0x3 | Loopback | Internal loopback test |

**ENABLE** [0] values:

| Value | When read | When written |
|---|---|---|
| 0x0 | IP disabled | Disable the IP (outputs idle) |
| 0x1 | IP enabled | Enable the IP |

Strobes: `o_ctrl_wr`

### 0x08 STATUS

Status

| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [17:16] | EXT_PIN | RO (sync) | 0 | `i_status_ext_pin` | Asynchronous inputs |
| [11:4] | LEVEL | RO | 0 | `i_status_level` | FIFO level |
| [0] | BUSY | RO | 0 | `i_status_busy` | IP busy |

**BUSY** [0] values:

| Value | When read | When written |
|---|---|---|
| 0x0 | Idle, a new START is accepted | — |
| 0x1 | Transfer in progress | — |

### 0x0C IRQ

Event flags

| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [7:4] | OVERRUN | W1C | 0 | `i_irq_overrun` | Overrun per channel |
| [1] | ERROR | W1C (irq) | 0 | `i_irq_error` | Transfer error |
| [0] | DONE | W1C (irq) | 0 | `i_irq_done` | Transfer finished |

**DONE** [0] values:

| Value | When read | When written |
|---|---|---|
| 0x0 | No transfer finished since last clear | No effect |
| 0x1 | A transfer finished | Clear the flag |

### 0x14 DATA

Data FIFO

| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [31:16] | TXDATA | RW | 0x0 | `o_data_txdata` | TX data (write pushes) |
| [15:0] | RXDATA | RO | 0 | `i_data_rxdata` | Head of RX FIFO (read pops) |

Strobes: `o_data_wr`, `o_data_rd`

## Field kinds

- **RW**: Kontrol: bus yazar, IP'ye çıkış (o_)
- **RO**: Durum: IP'den giriş (i_), bus okur
- **PULSE**: 1 yazınca 1 clock darbe (o_), 0 okunur
- **W1C**: IP olay bayrağı (i_ set), tutulur, 1 yazınca silinir
- **CONST**: Sabit değer (ID, versiyon), port yok
