# w1c32

IP version: `1.0.0`

AXI4-Lite register wrapper.

Addresses below are offsets from the slave base address set on the interconnect.  
Decoded address bits: `[2:2]` (block repeats every 0x8 bytes)

## Registers

| Offset | Register | Access | Reset | Description |
|---|---|---|---|---|
| 0x00 | EV | RW | 0x00000000 |  |
| 0x04 | P | WO | 0x00000000 |  |

### 0x00 EV



| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [31:0] | E | W1C (irq) | 0 | `i_ev_e` |  |

### 0x04 P



| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [31:0] | GO | PULSE | 0 | `o_p_go` |  |

## Field kinds

- **RW**: Kontrol: bus yazar, IP'ye çıkış (o_)
- **RO**: Durum: IP'den giriş (i_), bus okur
- **PULSE**: 1 yazınca 1 clock darbe (o_), 0 okunur
- **W1C**: IP olay bayrağı (i_ set), tutulur, 1 yazınca silinir
- **CONST**: Sabit değer (ID, versiyon), port yok
