# xb_min

AXI4-Lite crossbar (ZipCPU axilxbar), 1 master port(s) x 1 slave port(s).

| Port | Name | Base | Mask | Window | Used |
|---|---|---|---|---|---|
| MASTER0 | cpu | | | | |
| SLAVE0 | only | 0x00000000 | 0xFFFFF000 | 0x1000 | 0x10 |

`xb_min.v`: master i connects to `MASTER<i>_AXI_*`, slave j to `SLAVE<j>_AXI_*`.
Move a slave by changing `SLAVE<j>_ADDR` / `SLAVE<j>_MASK`; unmapped addresses return DECERR.
Keep the project's address map (Address Map Editor) in sync.
