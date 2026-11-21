# axil_xbar

AXI4-Lite crossbar (ZipCPU axilxbar), 1 master port(s) x 3 slave port(s).

| Port | Name | Base | Mask | Window | Used |
|---|---|---|---|---|---|
| MASTER0 | hexbus | | | | |
| SLAVE0 | ip_0 | 0x00000000 | 0xFFFFF000 | 0x1000 | 0x8 |
| SLAVE1 | SBB_0 | 0x00004000 | 0xFFFFC000 | 0x4000 | 0x3000 |
| SLAVE2 | ip_4 | 0x00008000 | 0xFFFFF000 | 0x1000 | 0x8 |

`axil_xbar.v`: master i connects to `MASTER<i>_AXI_*`, slave j to `SLAVE<j>_AXI_*`.
`axil_xbar_pk.v` (HDL Designer): `MASTER<i>_AXI_REQ/RSP`, `SLAVE<j>_AXI_REQ/RSP`.
Move a slave by changing `SLAVE<j>_ADDR` / `SLAVE<j>_MASK`; unmapped addresses return DECERR.
Keep the project's address map (Address Map Editor) in sync.
