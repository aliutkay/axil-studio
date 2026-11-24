"""HDL generator of axilgpio: outputs, inputs (with change interrupt) and the outputs after reset."""
from axil_gen import Field, Result, run

FIELDS = [
    Field("outputs", {"en": "Outputs (o_gpio)", "tr": "Çıkışlar (o_gpio)"}, "int", 8, min=1, max=32),
    Field("inputs", {"en": "Inputs (i_gpio)", "tr": "Girişler (i_gpio)"}, "int", 0, min=0, max=32,
          help={"en": "0 = no input registers (IN_DATA ... INT_ACTIVE repeat the outputs)",
                "tr": "0 = giriş register'ı yok (IN_DATA ... INT_ACTIVE çıkışları tekrarlar)"}),
    Field("default_hex", {"en": "Outputs after reset (hex)", "tr": "Reset sonrası çıkışlar (hex)"}, "text", "0"),
    Field("skid", {"en": "Skid buffer (full throughput)", "tr": "Skid buffer (tam hız)"}, "bool", True),
]


def compute(s):
    r = Result()
    n = s["outputs"]
    try:
        dflt = int(str(s["default_hex"]).replace("_", "").lower().removeprefix("0x") or "0", 16)
    except ValueError:
        dflt = 0
        r.errors.append({"en": "outputs after reset: not a hex number", "tr": "reset sonrası çıkışlar: hex sayı değil"})
    if dflt >> n:
        r.errors.append({"en": f"outputs after reset: 0x{dflt:X} has bits above o_gpio[{n - 1}]",
                         "tr": f"reset sonrası çıkışlar: 0x{dflt:X}, o_gpio[{n - 1}] üstünde bit içeriyor"})
    r.params = {"NOUT": n, "NIN": s["inputs"], "DEFAULT_OUTPUT": f"{n}'h{dflt:X}",
                "OPT_SKIDBUFFER": f"1'b{int(s['skid'])}", "C_AXI_ADDR_WIDTH": 5}
    r.summary = {"en": f"{n} outputs, {s['inputs']} inputs", "tr": f"{n} çıkış, {s['inputs']} giriş"}
    r.info = [("o_gpio", f"[{n - 1}:0]"),
              ("i_gpio", f"[{max(s['inputs'], 1) - 1}:0]" + ("" if s["inputs"] else " (-)")),
              ({"en": "Outputs after reset", "tr": "Reset sonrası çıkışlar"}, f"0x{dflt:X}"),
              ({"en": "Registers", "tr": "Register'lar"}, "LOAD 0x00, SET 0x04, CLEAR 0x08, TOGGLE 0x0C"
               + (", IN_DATA 0x10, CHANGED 0x14, INT_MASK 0x18, INT_ACTIVE 0x1C" if s["inputs"] else ""))]
    return r


if __name__ == "__main__":
    raise SystemExit(run({"en": "GPIO generator", "tr": "GPIO üreteci"}, FIELDS, compute))
