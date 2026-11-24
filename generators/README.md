# HDL generators

An HDL generator is a small application that belongs to one kind of IP. It asks for meaningful
settings (clock, baud rate, line type, number of pins …), computes the IP's parameter values from
them, and AXIL Studio writes **`<instance>_pk.v`**: the IP with packed AXI ports (`SLAVE_AXI_REQ/RSP`,
`MASTER_AXI_REQ/RSP`) and **no parameters**. The values are built into the port widths and into the
instance of the IP, one module per instance. Several instances can have different settings, and a
VHDL top never sees a generic (a 32-bit value would overflow its integer).

In AXIL Studio: **HDL Generator** tool (Tools menu, Ctrl+7). It lists every address-map instance
whose IP names a generator, plus blocks added by hand (a bus master such as the hexbus Debug Bridge).
- *Configure…*: opens the IP's generator window. The values are computed while you type.
- *Generate all*: runs every generator again without a window, with the settings kept in the project.

The Address Map Editor's context menu has *Configure HDL block* for such instances.

## Generators that come with AXIL Studio
| id | IP | Settings | Parameters |
|---|---|---|---|
| `axiluart_phy` | UART PHY (`hdl/v2005/uart_phy`) | clock, baud, data / parity / stop bits, line (TTL/RS-232, RS-422, RS-485 4-wire / 2-wire / echo), DE lead / tail in µs, FIFO depth, pins, RTS/CTS | `INITIAL_SETUP`, `DEF_CTRL`, `DEF_TIMING`, `LGFLEN`, `HAS_*`; shows the divider, the real baud rate and its error |
| `hexbus_axil_top` | Debug Bridge master (`hdl/v2005/hexbus`) | clock, baud, AXI address width | `CLK_FREQ_HZ`, `BAUD_RATE`, `C_AXI_ADDR_WIDTH`; clocks per bit, baud error |
| `axilgpio` | GPIO (`hdl/v2005/gpio`) | outputs, inputs, outputs after reset, skid buffer | `NOUT`, `NIN`, `DEFAULT_OUTPUT`, `OPT_SKIDBUFFER` |

An IP names its generator in its library entry: `"hdl_gen": "axiluart_phy"` next to its register
map (`examples/uart_phy/uart_phy.axlib.json`, `examples/cmd_seq_led_fade/axilgpio.axlib.json`).
Command sequencers are not listed here: the Interconnect Configurator writes their blocks
(`<subsystem>_cmd_seq_pk.v`, SELF_ID from the address map).

## Writing a generator
A folder with `axil-generator.json` and a script. AXIL Studio finds it in the IP interface folders
(settings), next to an open IP library, or in `<AXIL Studio>/generators`.

```json
{"format": "axil-generator", "protocol": 1, "id": "my_ip", "version": "1.0.0",
 "title": {"en": "My IP generator", "tr": "IP üreteci"}, "desc": "...",
 "module": "my_ip", "source": "rtl/my_ip.v",
 "command": ["{python}", "gen.py"]}
```

`module` is the Verilog module to wrap (ANSI header, `S_AXI_*` and / or `M_AXI_*` ports); `source`
is its file, relative to the manifest. The script uses `generators/sdk/axil_gen.py` (on PYTHONPATH
when AXIL Studio starts it):

```python
from axil_gen import Field, Result, run

FIELDS = [Field("clock_mhz", {"en": "Clock", "tr": "Saat"}, "float", 50.0, unit="MHz", min=0.001),
          Field("rate_hz", {"en": "Sample rate", "tr": "Örnekleme"}, "int", 1000, unit="Hz", min=1)]

def compute(s):
    div = round(s["clock_mhz"] * 1e6 / s["rate_hz"])
    r = Result(params={"DIVIDER": f"24'd{div}"}, summary=f"{s['rate_hz']} Hz")
    r.info.append(("Divider", str(div)))
    if div >= 1 << 24:
        r.errors.append("rate too low for a 24-bit divider")
    return r

if __name__ == "__main__":
    raise SystemExit(run({"en": "My IP generator"}, FIELDS, compute))
```

- **Field kinds:** `int`, `float`, `choice` (`choices=[(value, label), …]`), `bool`, `text`.
  Each field can set `unit`, `min`, `max`, `help`, and `enabled=lambda s: …` to grey it out.
- **Labels:** a string or `{"en": …, "tr": …}`.
- **`compute`:** gets the settings and returns the parameter values (ints or Verilog literals such as
  `"31'h400001B2"`), a one-line summary, rows to show (`info`), `warnings`, and `errors`. Errors block
  OK.

Protocol, if you write the application in another language:

```
<command> --in IN.json --out OUT.json [--batch] [--lang tr]
```

- **IN:** `{"name", "ip", "base", "settings"}`.
- **OUT:** `{"settings", "params", "summary"}`.
- **`--batch`:** no window; exit 1 with the errors on stderr.
- **Exit code 3:** Cancel.

Tests: `tests/test_hdl_generator.py`.
