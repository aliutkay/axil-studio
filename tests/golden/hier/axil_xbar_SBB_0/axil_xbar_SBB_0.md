# axil_xbar_SBB_0

AXI4-Lite crossbar (ZipCPU axilxbar), 1 master port(s) x 3 slave port(s).

| Port | Name | Base | Mask | Window | Used |
|---|---|---|---|---|---|
| MASTER0 | SBB_0 | | | | |
| SLAVE0 | ip_1 | 0x00004000 | 0xFFFFF000 | 0x1000 | 0x8 |
| SLAVE1 | ip_2 | 0x00005000 | 0xFFFFF000 | 0x1000 | 0x8 |
| SLAVE2 | SBB_1 | 0x00006000 | 0xFFFFF000 | 0x1000 | 0x1000 |

`axil_xbar_SBB_0.v`: master i connects to `MASTER<i>_AXI_*`, slave j to `SLAVE<j>_AXI_*`.
`axil_xbar_SBB_0_pk.v` (HDL Designer): `MASTER<i>_AXI_REQ/RSP`, `SLAVE<j>_AXI_REQ/RSP`.
Move a slave by changing `SLAVE<j>_ADDR` / `SLAVE<j>_MASK`; unmapped addresses return DECERR.
Keep the project's address map (Address Map Editor) in sync.
