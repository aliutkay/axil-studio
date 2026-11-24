"""HDL generator of hexbus_axil_top: the Debug Bridge's UART to AXI4-Lite master. The RTL divides
CLK_FREQ_HZ by BAUD_RATE (rounding down) to get the clocks per bit."""
from axil_gen import Field, Result, run

FIELDS = [
    Field("clock_mhz", {"en": "Clock (S_AXI_ACLK)", "tr": "Saat (S_AXI_ACLK)"}, "float", 50.0, unit="MHz", min=0.001),
    Field("baud", {"en": "Baud rate (the PC's serial port)", "tr": "Baud (PC'nin seri portu)"}, "int", 115200,
          unit="baud", min=1, max=100_000_000),
    Field("addr_width", {"en": "AXI address width", "tr": "AXI adres genişliği"}, "int", 32, unit="bit", min=8, max=32),
]


def compute(s):
    r = Result()
    clk = round(s["clock_mhz"] * 1e6)
    cpb = clk // s["baud"]
    real = clk / cpb if cpb else 0
    err = (real - s["baud"]) / s["baud"] * 100 if cpb else 100
    if cpb < 4:
        r.errors.append({"en": "baud rate too high for this clock (fewer than 4 clocks per bit)",
                         "tr": "bu saat için baud çok yüksek (bit başına 4 çevrimden az)"})
    if cpb >= 1 << 24:
        r.errors.append({"en": "baud rate too low for this clock (2^24 clocks per bit or more)",
                         "tr": "bu saat için baud çok düşük (bit başına 2^24 çevrim ya da fazlası)"})
    if abs(err) > 4.5:
        r.errors.append({"en": f"baud rate error {err:+.2f} % (more than 4.5 %)",
                         "tr": f"baud hatası {err:+.2f} % (%4,5'ten fazla)"})
    elif abs(err) > 2:
        r.warnings.append({"en": f"baud rate error {err:+.2f} %: the PC's port may not follow it",
                           "tr": f"baud hatası {err:+.2f} %: PC'nin portu takip edemeyebilir"})
    r.params = {"CLK_FREQ_HZ": clk, "BAUD_RATE": s["baud"], "C_AXI_ADDR_WIDTH": s["addr_width"]}
    r.summary = f"{s['baud']} baud, {s['clock_mhz']:g} MHz"
    r.info = [({"en": "Clocks per bit (rounded down)", "tr": "Bit başına çevrim (aşağı yuvarlanır)"}, str(cpb)),
              ({"en": "Actual baud rate", "tr": "Gerçek baud"}, f"{real:.1f}"),
              ({"en": "Baud rate error", "tr": "Baud hatası"}, f"{err:+.3f} %"),
              ({"en": "Debug Bridge serial port", "tr": "Debug Bridge seri portu"}, f"{s['baud']} baud, 8N1")]
    return r


if __name__ == "__main__":
    raise SystemExit(run({"en": "hexbus debug bridge generator", "tr": "hexbus debug köprüsü üreteci"}, FIELDS, compute))
