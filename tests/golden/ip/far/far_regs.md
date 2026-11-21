# far

IP version: `1.0.0`

AXI4-Lite register wrapper.

Addresses below are offsets from the slave base address set on the interconnect.  
Decoded address bits: `[5:2]` (block repeats every 0x40 bytes)

## Registers

| Offset | Register | Access | Reset | Description |
|---|---|---|---|---|
| 0x00 | A | RW | 0x00000001 |  |
| 0x3C | B | RW | 0x00000005 |  |

### 0x00 A



| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [0] | A | RW | 0x1 | `o_a` |  |

### 0x3C B



| Bits | Field | Kind | Reset | Port | Description |
|---|---|---|---|---|---|
| [7:0] | B | RW | 0x5 | `o_b` |  |

## Field kinds

- **RW**: Kontrol: bus yazar, IP'ye çıkış (o_)
- **RO**: Durum: IP'den giriş (i_), bus okur
- **PULSE**: 1 yazınca 1 clock darbe (o_), 0 okunur
- **W1C**: IP olay bayrağı (i_ set), tutulur, 1 yazınca silinir
- **CONST**: Sabit değer (ID, versiyon), port yok
