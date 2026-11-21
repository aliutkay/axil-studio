# xb_3x4

AXI4-Lite crossbar (ZipCPU axilxbar), 3 master port(s) x 4 slave port(s).

| Port | Name | Base | Mask | Window | Used |
|---|---|---|---|---|---|
| MASTER0 | hexbus | | | | |
| MASTER1 | cpu | | | | |
| MASTER2 | dma | | | | |
| SLAVE0 | uart | 0x40000000 | 0xFFFFF000 | 0x1000 | 0x20 |
| SLAVE1 | gpio | 0x40001000 | 0xFFFFF000 | 0x1000 | 0x20 |
| SLAVE2 | ram | 0x80000000 | 0xFFFF0000 | 0x10000 |  |
| SLAVE3 | SBB_0 | 0x40100000 | 0xFFF00000 | 0x100000 | 0x3000 |

`xb_3x4.v`: master i connects to `MASTER<i>_AXI_*`, slave j to `SLAVE<j>_AXI_*`.
`xb_3x4_pk.v` (HDL Designer): `MASTER<i>_AXI_REQ/RSP`, `SLAVE<j>_AXI_REQ/RSP`.
Move a slave by changing `SLAVE<j>_ADDR` / `SLAVE<j>_MASK`; unmapped addresses return DECERR.
Keep the project's address map (Address Map Editor) in sync.
