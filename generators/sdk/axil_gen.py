"""SDK for AXIL Studio HDL generators (see axil_studio/core/generators.py for the protocol).

A generator describes its settings and computes the IP's parameter values from them:

    from axil_gen import Field, Result, run

    FIELDS = [Field("clock_mhz", {"en": "Clock", "tr": "Saat"}, "float", 50.0, unit="MHz", min=1),
              Field("baud", {"en": "Baud rate", "tr": "Baud"}, "int", 115200, min=1)]

    def compute(s):
        div = round(s["clock_mhz"] * 1e6 / s["baud"])
        r = Result(params={"DIV": div}, summary=f"{s['baud']} baud")
        r.info.append(("Divider", str(div)))
        if div < 2:
            r.errors.append("baud rate too high for this clock")
        return r

    if __name__ == "__main__":
        run({"en": "My IP generator"}, FIELDS, compute)

run() reads --in / --out / --batch / --lang. With --batch it computes from the given settings
(missing ones: the defaults) and writes OUT, or exits 1 with the errors. Without it, a window
shows the settings, and next to them the values computed while they are edited; OK writes OUT,
Cancel exits with 3. Parameter values may be ints (written as decimals) or Verilog literal text
("31'd434"); the block's port widths follow them.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional

CANCELLED = 3
LANG = "en"


def T(v):
    """Text in the current language from {"en": .., "tr": ..} or a plain string."""
    if isinstance(v, dict):
        return v.get(LANG) or v.get("en") or next(iter(v.values()), "")
    return str(v)


@dataclass
class Field:
    key: str
    label: object
    kind: str = "int"               # int | float | choice | bool | text
    default: object = 0
    choices: list = field(default_factory=list)    # choice: [(value, label), ...]
    unit: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    help: object = ""
    enabled: Optional[Callable] = None   # settings -> bool (greyed out when False)
    decimals: int = 3                    # float fields

    def coerce(self, v):
        if self.kind == "int":
            v = int(v)
        elif self.kind == "float":
            v = float(v)
        elif self.kind == "bool":
            v = bool(v)
        elif self.kind == "choice":
            keys = [c[0] for c in self.choices]
            if v not in keys:
                # JSON may give "8" for 8
                v = next((k for k in keys if str(k) == str(v)), None)
                if v is None:
                    raise ValueError(f"{self.key}: not one of {keys}")
        else:
            v = str(v)
        if self.kind in ("int", "float"):
            if self.min is not None and v < self.min:
                raise ValueError(f"{T(self.label)}: at least {self.min}")
            if self.max is not None and v > self.max:
                raise ValueError(f"{T(self.label)}: at most {self.max}")
        return v


@dataclass
class Result:
    params: dict = field(default_factory=dict)
    summary: str = ""
    info: list = field(default_factory=list)       # [(label, value)] shown next to the settings
    errors: list = field(default_factory=list)     # block OK / batch
    warnings: list = field(default_factory=list)   # labels and messages: str or {"en": .., "tr": ..}

    def out(self, settings):
        return {"settings": settings, "summary": T(self.summary),
                "params": {k: (v if isinstance(v, str) else int(v)) for k, v in self.params.items()},
                "info": [[T(a), T(b)] for a, b in self.info], "warnings": [T(w) for w in self.warnings]}


def settings_from(fields, given):
    """Defaults overlaid with the given settings (unknown keys are dropped)."""
    s, errors = {}, []
    for f in fields:
        try:
            s[f.key] = f.coerce(given[f.key]) if f.key in given else f.default
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            s[f.key] = f.default
    return s, errors


def _write(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def run(title, fields, compute, argv=None):
    global LANG
    ap = argparse.ArgumentParser(description=T(title))
    ap.add_argument("--in", dest="fin", required=True)
    ap.add_argument("--out", dest="fout", required=True)
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--lang", default="en")
    a = ap.parse_args(argv)
    LANG = a.lang
    with open(a.fin, encoding="utf-8") as fh:
        req = json.load(fh)
    settings, errors = settings_from(fields, req.get("settings") or {})
    if a.batch:
        if not errors:
            res = compute(settings)
            errors = [T(e) for e in res.errors]
        if errors:
            sys.stderr.write("; ".join(errors) + "\n")
            return 1
        _write(a.fout, res.out(settings))
        return 0
    return _window(title, fields, compute, settings, req, a.fout)


# ----------------------------------------------------------------------------- window
def _window(title, fields, compute, settings, req, fout):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                                   QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                   QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView)

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = QDialog()
    name = req.get("name", "")
    dlg.setWindowTitle(f"{T(title)} — {name}" if name else T(title))
    dlg.resize(980, 560)
    top = QHBoxLayout(dlg)
    left = QVBoxLayout()
    grp = QGroupBox({"en": "Settings", "tr": "Ayarlar"}[LANG if LANG in ("en", "tr") else "en"])
    form = QFormLayout(grp)
    left.addWidget(grp)
    left.addStretch(1)
    top.addLayout(left, 5)
    right = QVBoxLayout()
    top.addLayout(right, 6)
    hdr = QLabel()
    hdr.setWordWrap(True)
    hdr.setStyleSheet("font-weight: 600;")
    right.addWidget(hdr)
    table = QTableWidget(0, 2)
    table.horizontalHeader().setVisible(False)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.setMinimumWidth(420)
    table.setWordWrap(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    right.addWidget(table, 1)
    msg = QLabel()
    msg.setWordWrap(True)
    right.addWidget(msg)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    right.addWidget(bb)

    widgets = {}
    for f in fields:
        v = settings[f.key]
        if f.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(v))
            w.toggled.connect(lambda *_: update())
        elif f.kind == "choice":
            w = QComboBox()
            for val, lab in f.choices:
                w.addItem(T(lab), val)
            w.setCurrentIndex(max(0, [c[0] for c in f.choices].index(v) if v in [c[0] for c in f.choices] else 0))
            w.currentIndexChanged.connect(lambda *_: update())
        elif f.kind == "int":
            w = QSpinBox()
            w.setRange(int(f.min if f.min is not None else -2**31), int(f.max if f.max is not None else 2**31 - 1))
            w.setValue(int(v))
            if f.unit:
                w.setSuffix(" " + f.unit)
            w.valueChanged.connect(lambda *_: update())
        elif f.kind == "float":
            w = QDoubleSpinBox()
            w.setDecimals(f.decimals)
            w.setRange(f.min if f.min is not None else -1e12, f.max if f.max is not None else 1e12)
            w.setValue(float(v))
            if f.unit:
                w.setSuffix(" " + f.unit)
            w.valueChanged.connect(lambda *_: update())
        else:
            w = QLineEdit(str(v))
            w.textChanged.connect(lambda *_: update())
        if f.help:
            w.setToolTip(T(f.help))
        widgets[f.key] = w
        form.addRow(T(f.label), w)

    def read():
        s = {}
        for f in fields:
            w = widgets[f.key]
            if f.kind == "bool":
                s[f.key] = w.isChecked()
            elif f.kind == "choice":
                s[f.key] = w.currentData()
            elif f.kind in ("int", "float"):
                s[f.key] = w.value()
            else:
                s[f.key] = w.text()
        return s

    state = {}

    def update():
        s = read()
        for f in fields:
            if f.enabled is not None:
                widgets[f.key].setEnabled(bool(f.enabled(s)))
        try:
            r = compute(s)
        except Exception as exc:          # noqa: BLE001 - show it, keep the window
            r = Result(errors=[f"{type(exc).__name__}: {exc}"])
        state["s"], state["r"] = s, r
        hdr.setText(T(r.summary))
        rows = [(T(a), T(b)) for a, b in r.info] + [("", "")] + [(k, str(v)) for k, v in r.params.items()]
        table.setRowCount(len(rows))
        for i, (a, b) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(str(a)))
            it = QTableWidgetItem(str(b))
            if i >= len(r.info) + 1:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            table.setItem(i, 1, it)
        lines = [f"<span style='color:#c62828'>✖ {T(e)}</span>" for e in r.errors]
        lines += [f"<span style='color:#b26a00'>⚠ {T(w)}</span>" for w in r.warnings]
        msg.setText("<br>".join(lines))
        bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not r.errors)

    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    update()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return CANCELLED
    _write(fout, state["r"].out(state["s"]))
    return 0
