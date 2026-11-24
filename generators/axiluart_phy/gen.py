"""HDL generator of axiluart_phy: the reset values of the UART (baud rate, frame) and of the line
interface (RS-232 / RS-422 / RS-485, DE timing), the FIFO depth and the pins that exist."""
import math

from axil_gen import Field, Result, run

LINES = [  # (key, label, PHY_CTRL preset; see hdl/uart_phy/README.md)
    ("ttl", {"en": "TTL / RS-232", "tr": "TTL / RS-232"}, 0xC0),
    ("rs422", {"en": "RS-422 point-to-point (DE always on)", "tr": "RS-422 noktadan noktaya (DE hep açık)"}, 0xC1),
    ("rs485_4w", {"en": "RS-485 4-wire / RS-422 multidrop (DE while sending)",
                  "tr": "RS-485 4 telli / RS-422 çok noktalı (DE gönderirken)"}, 0xC2),
    ("rs485_2w", {"en": "RS-485 2-wire, half duplex", "tr": "RS-485 2 telli, yarı çift yönlü"}, 0xC6),
    ("rs485_2w_echo", {"en": "RS-485 2-wire with echo", "tr": "RS-485 2 telli, yankılı"}, 0xCE),
]
PARITY = [("none", {"en": "none", "tr": "yok"}), ("even", {"en": "even", "tr": "çift"}),
          ("odd", {"en": "odd", "tr": "tek"}), ("mark", {"en": "mark (1)", "tr": "mark (1)"}),
          ("space", {"en": "space (0)", "tr": "space (0)"})]
DRIVES_DE = ("rs422", "rs485_4w", "rs485_2w", "rs485_2w_echo")

FIELDS = [
    Field("clock_mhz", {"en": "Clock (S_AXI_ACLK)", "tr": "Saat (S_AXI_ACLK)"}, "float", 50.0, unit="MHz", min=0.001),
    Field("baud", {"en": "Baud rate", "tr": "Baud"}, "int", 115200, unit="baud", min=1, max=100_000_000),
    Field("data_bits", {"en": "Data bits", "tr": "Veri biti"}, "choice", 8,
          choices=[(8, "8"), (7, "7"), (6, "6"), (5, "5")]),
    Field("parity", {"en": "Parity", "tr": "Parite"}, "choice", "none", choices=PARITY),
    Field("stop_bits", {"en": "Stop bits", "tr": "Stop biti"}, "choice", 1, choices=[(1, "1"), (2, "2")]),
    Field("line", {"en": "Line", "tr": "Hat"}, "choice", "ttl", choices=[(k, l) for k, l, _ in LINES]),
    Field("de_lead_us", {"en": "DE on before the start bit", "tr": "Başlangıç bitinden önce DE"}, "float", 1.0,
          unit="µs", min=0, enabled=lambda s: s["line"] in DRIVES_DE),
    Field("de_tail_us", {"en": "DE on after the stop bit", "tr": "Stop bitinden sonra DE"}, "float", 5.0,
          unit="µs", min=0, enabled=lambda s: s["line"] in DRIVES_DE),
    Field("fifo", {"en": "FIFO depth (each direction)", "tr": "FIFO derinliği (her yön)"}, "choice", 16,
          choices=[(n, str(n)) for n in (4, 8, 16, 32, 64, 128, 256, 512, 1024)]),
    Field("has_tx", {"en": "Transmitter", "tr": "Verici"}, "bool", True),
    Field("has_rx", {"en": "Receiver", "tr": "Alıcı"}, "bool", True),
    Field("has_de", {"en": "DE pin (transceiver driver enable)", "tr": "DE pini (sürücü izni)"}, "bool", True),
    Field("has_re", {"en": "/RE pin (receiver enable)", "tr": "/RE pini (alıcı izni)"}, "bool", True),
    Field("flow", {"en": "RTS / CTS flow control", "tr": "RTS / CTS akış kontrolü"}, "bool", False),
]


def compute(s):
    r = Result()
    clk = s["clock_mhz"] * 1e6
    div = round(clk / s["baud"])
    real = clk / div if div else 0
    err = (real - s["baud"]) / s["baud"] * 100 if div else 100
    if div < 2:
        r.errors.append({"en": "baud rate too high for this clock (divider < 2)",
                         "tr": "bu saat için baud çok yüksek (bölen < 2)"})
    if div >= 1 << 24:
        r.errors.append({"en": "baud rate too low for this clock (divider >= 2^24)",
                         "tr": "bu saat için baud çok düşük (bölen >= 2^24)"})
    if abs(err) > 4.5:
        r.errors.append({"en": f"baud rate error {err:+.2f} % (more than 4.5 %)",
                         "tr": f"baud hatası {err:+.2f} % (%4,5'ten fazla)"})
    elif abs(err) > 2:
        r.warnings.append({"en": f"baud rate error {err:+.2f} %: keep the other side within 2 %",
                           "tr": f"baud hatası {err:+.2f} %: karşı tarafınki %2'yi geçmesin"})
    p = s["parity"]
    setup = (div & 0xFFFFFF) | ((8 - s["data_bits"]) << 28) | ((s["stop_bits"] == 2) << 27)
    setup |= (p != "none") << 26 | (p in ("mark", "space")) << 25 | (p in ("even", "mark")) << 24
    setup |= (not s["flow"]) << 30
    line = next(x for x in LINES if x[0] == s["line"])
    ctrl = line[2]
    if s["line"] in DRIVES_DE and not s["has_de"]:
        r.errors.append({"en": "this line needs the DE pin", "tr": "bu hat DE pinini gerektirir"})
    if not s["has_tx"]:
        ctrl &= ~0x40
    if not s["has_rx"]:
        ctrl &= ~0x80
    if not (s["has_tx"] or s["has_rx"]):
        r.errors.append({"en": "no transmitter and no receiver", "tr": "ne verici ne alıcı var"})
    lead = round(s["de_lead_us"] * s["clock_mhz"]) if s["line"] in DRIVES_DE else 0
    tail = round(s["de_tail_us"] * s["clock_mhz"]) if s["line"] in DRIVES_DE else 0
    for name, v in (("DE lead", lead), ("DE tail", tail)):
        if v > 0xFFFF:
            r.errors.append({"en": f"{name}: {v} cycles, at most 65535", "tr": f"{name}: {v} çevrim, en fazla 65535"})
    timing = (min(tail, 0xFFFF) << 16) | min(lead, 0xFFFF)
    frame = f"{s['data_bits']}{p[0].upper() if p != 'none' else 'N'}{s['stop_bits']}"
    r.params = {"INITIAL_SETUP": f"31'h{setup & 0x7FFFFFFF:08X}", "LGFLEN": f"4'd{int(math.log2(s['fifo']))}",
                "HAS_TX": f"1'b{int(s['has_tx'])}", "HAS_RX": f"1'b{int(s['has_rx'])}",
                "HAS_DE": f"1'b{int(s['has_de'])}", "HAS_RE": f"1'b{int(s['has_re'])}",
                "HAS_FLOW": f"1'b{int(s['flow'])}", "DEF_CTRL": f"32'h{ctrl:08X}", "DEF_TIMING": f"32'h{timing:08X}"}
    r.summary = {k: f"{s['baud']} {frame}, {line[1][k]}, {s['clock_mhz']:g} MHz" for k in ("en", "tr")}
    r.info = [({"en": "Baud divider (clocks per bit)", "tr": "Baud böleni (bit başına çevrim)"}, str(div)),
              ({"en": "Actual baud rate", "tr": "Gerçek baud"}, f"{real:.1f}"),
              ({"en": "Baud rate error", "tr": "Baud hatası"}, f"{err:+.3f} %"),
              ({"en": "Frame", "tr": "Çerçeve"}, frame),
              ({"en": "Byte time", "tr": "Bayt süresi"},
               f"{(1 + s['data_bits'] + (p != 'none') + s['stop_bits']) * 1e6 / real:.2f} µs" if real else "-"),
              ({"en": "PHY_CTRL after reset", "tr": "Reset sonrası PHY_CTRL"}, f"0x{ctrl:02X}"),
              ({"en": "DE lead / tail", "tr": "DE lead / tail"}, {"en": f"{lead} / {tail} cycles", "tr": f"{lead} / {tail} çevrim"})]
    return r


if __name__ == "__main__":
    raise SystemExit(run({"en": "UART PHY generator", "tr": "UART PHY üreteci"}, FIELDS, compute))
