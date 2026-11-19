#!/usr/bin/env python3
"""
AXI4-Lite Wrapper Generator - Tkinter front end for axilgen.py

Define registers and typed bit fields for an IP, then generate an
easyaxil-based AXI4-Lite wrapper (Verilog), a self-checking testbench,
instantiation templates, documentation and a register map that the
AXI4-Lite UART debug application (app.py) can open directly.
"""

import os
import re
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import axilgen as G
import regmap_xlsx
import xbargen as X
import axilpack
from model import Project
from model import single_line
from widgets import TextField, ValuesEditor, return_is_ok

APP_TITLE = "AXI4-Lite Wrapper Generator"
KIND_SHORT = {"RW": "kontrol (o_)", "RO": "durum (i_)", "PULSE": "darbe (o_)",
              "W1C": "olay bayrağı (i_)", "CONST": "sabit"}
KIND_COLORS = {"RW": "#4c8bf5", "RO": "#34a853", "PULSE": "#fbbc04", "W1C": "#ea4335",
               "CONST": "#9e9e9e"}


def hexs(v, digits=8):
    return f"0x{v:0{digits}X}"


# =============================================================================
# Dialogs
# =============================================================================
class Dialog(tk.Toplevel):
    """Base modal dialog: subclasses fill self.body and implement apply()."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)
        self.build()
        bar = ttk.Frame(self.body)
        bar.grid(row=99, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="İptal", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Tamam", command=self._ok).pack(side="right", padx=5)
        self.bind("<Return>", lambda e: self._ok() if return_is_ok(e) else None)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def row(self, r, label, widget, hint=None):
        ttk.Label(self.body, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=3)
        widget.grid(row=r, column=1, sticky="ew", pady=3)
        if hint:
            ttk.Label(self.body, text=hint, foreground="#777").grid(row=r, column=2, sticky="w", padx=6)

    def _ok(self):
        try:
            self.result = self.apply()
        except ValueError as exc:
            messagebox.showerror("Geçersiz değer", str(exc), parent=self)
            return
        self.destroy()


class RegDialog(Dialog):
    def __init__(self, parent, reg: G.RegSpec, title):
        self.reg = reg
        super().__init__(parent, title)

    def build(self):
        r = self.reg
        self.v_name = tk.StringVar(value=r.name)
        self.v_off = tk.StringVar(value=f"0x{r.offset:02X}")
        self.t_desc = TextField(self.body, r.desc, height=3, width=40)
        self.v_wr = tk.BooleanVar(value=r.wr_strobe)
        self.v_rd = tk.BooleanVar(value=r.rd_strobe)
        e = ttk.Entry(self.body, textvariable=self.v_name, width=28)
        self.row(0, "Ad", e)
        e.focus_set()
        self.row(1, "Offset", ttk.Entry(self.body, textvariable=self.v_off, width=28), "4'ün katı")
        ttk.Label(self.body, text="Açıklama").grid(row=2, column=0, sticky="nw", pady=3)
        self.t_desc.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Checkbutton(self.body, text="Yazma strobe'u (o_<reg>_wr, ör. FIFO push)",
                        variable=self.v_wr).grid(row=3, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(self.body, text="Okuma strobe'u (o_<reg>_rd, ör. FIFO pop)",
                        variable=self.v_rd).grid(row=4, column=0, columnspan=3, sticky="w", pady=2)

    def apply(self):
        off = G.parse_int(self.v_off.get())
        if off % 4 or off < 0:
            raise ValueError("Offset 4'ün katı olmalı")
        return G.RegSpec(self.v_name.get().strip(), off, self.t_desc.get(),
                         self.v_wr.get(), self.v_rd.get(), self.reg.fields)


class FieldDialog(Dialog):
    def __init__(self, parent, f: G.FieldSpec, title):
        self.f = f
        super().__init__(parent, title)

    def build(self):
        f = self.f
        self.v_name = tk.StringVar(value=f.name)
        self.v_lsb = tk.StringVar(value=str(f.lsb))
        self.v_width = tk.StringVar(value=str(f.width))
        self.v_kind = tk.StringVar(value=f.kind)
        self.v_reset = tk.StringVar(value=f"0x{f.reset:X}")
        self.v_irq = tk.BooleanVar(value=f.irq)
        self.v_sync = tk.BooleanVar(value=f.sync)
        e = ttk.Entry(self.body, textvariable=self.v_name, width=24)
        self.row(0, "Ad", e, "register adıyla aynı → port o_<reg>")
        e.focus_set()
        self.row(1, "LSB (bit no)", ttk.Entry(self.body, textvariable=self.v_lsb, width=24))
        self.row(2, "Genişlik (bit)", ttk.Entry(self.body, textvariable=self.v_width, width=24))
        kind = ttk.Combobox(self.body, textvariable=self.v_kind, values=G.KINDS, state="readonly", width=22)
        self.row(3, "Tip", kind)
        self.kind_help = ttk.Label(self.body, text="", foreground="#555", wraplength=360)
        self.kind_help.grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.e_reset = ttk.Entry(self.body, textvariable=self.v_reset, width=24)
        self.row(5, "Reset / sabit değer", self.e_reset, "RW ve CONST")
        ttk.Label(self.body, text="Açıklama").grid(row=6, column=0, sticky="nw", pady=3)
        self.t_desc = TextField(self.body, f.desc, height=3, width=48)
        self.t_desc.grid(row=6, column=1, columnspan=2, sticky="ew", pady=3)
        self.cb_irq = ttk.Checkbutton(self.body, text="Interrupt (o_irq'ya bağla)", variable=self.v_irq)
        self.cb_irq.grid(row=7, column=0, columnspan=3, sticky="w")
        self.cb_sync = ttk.Checkbutton(self.body, text="2-FF senkronizör (asenkron giriş)", variable=self.v_sync)
        self.cb_sync.grid(row=8, column=0, columnspan=3, sticky="w")
        self.values = ValuesEditor(self.body, f.values, f.kind, self._cur_width)
        self.values.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        kind.bind("<<ComboboxSelected>>", lambda e: self._kind_changed())
        self._kind_changed()

    def _cur_width(self):
        try:
            return max(1, min(32, G.parse_int(self.v_width.get())))
        except ValueError:
            return 32

    def _kind_changed(self):
        k = self.v_kind.get()
        self.kind_help.config(text=G.KIND_HELP.get(k, ""))
        self.e_reset.state(["!disabled"] if k in ("RW", "CONST") else ["disabled"])
        self.cb_irq.state(["!disabled"] if k == "W1C" else ["disabled"])
        self.cb_sync.state(["!disabled"] if k == "RO" else ["disabled"])
        if k not in ("RW", "CONST"):
            self.v_reset.set("0x0")
        if k != "W1C":
            self.v_irq.set(False)
        if k != "RO":
            self.v_sync.set(False)
        if hasattr(self, "values"):
            self.values.set_kind(k)

    def apply(self):
        lsb, width = G.parse_int(self.v_lsb.get()), G.parse_int(self.v_width.get())
        if not (0 <= lsb <= 31 and width >= 1 and lsb + width <= 32):
            raise ValueError("Alan 32 bitin içinde kalmalı")
        reset = G.parse_int(self.v_reset.get())
        if reset >> width:
            raise ValueError(f"Değer {width} bite sığmıyor")
        values = self.values.get()
        bad = [f"0x{e.value:X}" for e in values if e.value >> width]
        if bad:
            raise ValueError(f"Değer tablosundaki {', '.join(bad)} {width} bite sığmıyor")
        return G.FieldSpec(self.v_name.get().strip(), lsb, width, self.v_kind.get(), reset,
                           self.t_desc.get(), self.v_irq.get(), self.v_sync.get(), values)


class PortDialog(Dialog):
    """Edit one crossbar port (MASTER<i> or SLAVE<j>)."""

    def __init__(self, parent, port, title):
        self.port = port
        self.is_slave = isinstance(port, X.XbarSlave)
        super().__init__(parent, title)

    def build(self):
        p = self.port
        self.v_name = tk.StringVar(value=p.name)
        self.v_en = tk.BooleanVar(value=p.enabled)
        e = ttk.Entry(self.body, textvariable=self.v_name, width=24)
        self.row(0, "Etiket", e)
        e.focus_set()
        ttk.Checkbutton(self.body, text="Kullanımda (EN = 1)", variable=self.v_en).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=3)
        if self.is_slave:
            self.v_base = tk.StringVar(value=hexs(p.base))
            self.v_mask = tk.StringVar(value=hexs(p.mask))
            self.v_span = tk.StringVar(value=f"0x{p.span:X}")
            self.row(2, "Base adres", ttk.Entry(self.body, textvariable=self.v_base, width=24))
            self.row(3, "Maske", ttk.Entry(self.body, textvariable=self.v_mask, width=24), "0xFFFFF000 = 4 KB")
            self.row(4, "Kullanılan (byte)", ttk.Entry(self.body, textvariable=self.v_span, width=24),
                     "pencereye sığma kontrolü")

    def apply(self):
        name = self.v_name.get().strip() or "port"
        if not self.is_slave:
            return X.XbarMaster(name, self.v_en.get())
        return X.XbarSlave(name, G.parse_int(self.v_base.get()), G.parse_int(self.v_mask.get()) & 0xFFFFFFFF,
                           G.parse_int(self.v_span.get()), self.v_en.get())


class XbarWindow(tk.Toplevel):
    """axilxbar wrapper with named MASTER<i>_AXI_* / SLAVE<j>_AXI_* ports and per-port generics."""

    def __init__(self, parent, current_spec):
        super().__init__(parent)
        self.title("axilxbar wrapper üret")
        self.geometry("900x600")
        self._current = current_spec
        self.spec = X.XbarSpec()
        self.path = None
        self._busy = False

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.v_module = tk.StringVar(value=self.spec.module)
        self.v_nm = tk.StringVar(value="1")
        self.v_ns = tk.StringVar(value="1")
        self.v_win = tk.StringVar(value="0x1000")
        self.v_style = tk.StringVar(value=X.STYLES["packed"])
        ttk.Label(top, text="Modül").pack(side="left")
        ttk.Entry(top, textvariable=self.v_module, width=18).pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Master portu").pack(side="left")
        ttk.Spinbox(top, from_=1, to=16, textvariable=self.v_nm, width=4,
                    command=self._capacity).pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Slave portu").pack(side="left")
        ttk.Spinbox(top, from_=1, to=64, textvariable=self.v_ns, width=4,
                    command=self._capacity).pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Min. pencere").pack(side="left")
        ttk.Combobox(top, textvariable=self.v_win, width=9, state="readonly",
                     values=("0x100", "0x400", "0x1000", "0x10000", "0x100000")).pack(side="left", padx=4)
        top2 = ttk.Frame(self, padding=(10, 0))
        top2.pack(fill="x")
        ttk.Label(top2, text="Port stili").pack(side="left")
        st = ttk.Combobox(top2, textvariable=self.v_style, values=list(X.STYLES.values()), state="readonly", width=58)
        st.pack(side="left", padx=4)
        st.bind("<<ComboboxSelected>>", lambda e: self._sync())
        self.v_module.trace_add("write", lambda *a: self._sync())
        for v in (self.v_nm, self.v_ns):
            v.trace_add("write", lambda *a: self._capacity())
        ttk.Label(self, text="Port sayıları projeye göre seçilir. Kullanılmayan portlar EN=0 ile gelir; "
                             "HDL Designer'da generic ile açılıp adreslenebilir, yeniden üretim gerekmez.",
                  foreground="#555", padding=(10, 0), wraplength=860).pack(anchor="w")

        mid = ttk.Frame(self, padding=(10, 4))
        mid.pack(fill="both", expand=True)
        cols = ("en", "ip", "base", "mask", "win", "used")
        self.tree = ttk.Treeview(mid, columns=cols, selectmode="browse")
        for c, h, w in (("#0", "Port", 90), ("en", "EN", 40), ("ip", "Etiket", 170), ("base", "Base", 110),
                        ("mask", "Maske", 110), ("win", "Pencere", 90), ("used", "Kullanılan", 90)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c in ("#0", "ip") else "center")
        self.tree.tag_configure("off", foreground="#999")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._edit())
        bar = ttk.Frame(mid)
        bar.pack(fill="x", pady=4)
        for text, cmd in (("Debug projesinden yükle…", self._load_project), ("Bu IP'yi boş porta ekle", self._add_current),
                          ("Düzenle", self._edit), ("EN aç/kapa", self._toggle),
                          ("▲", lambda: self._move(-1)), ("▼", lambda: self._move(1))):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 3))

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        self.msg = tk.Text(bottom, height=4, font=("Consolas", 9), relief="flat", background="#fff8f8")
        self.msg.pack(fill="x")
        act = ttk.Frame(bottom)
        act.pack(fill="x", pady=(6, 0))
        ttk.Button(act, text="Üret…", command=self._generate).pack(side="left")
        ttk.Button(act, text="Aç…", command=self._open).pack(side="left", padx=4)
        ttk.Button(act, text="Kaydet…", command=self._save).pack(side="left")
        ttk.Label(act, text="master MASTER_AXI_REQ/RSP ↔ MASTER<i>_AXI_REQ/RSP,  slave SLAVE_AXI_REQ/RSP ↔ SLAVE<j>_AXI_REQ/RSP",
                  foreground="#555").pack(side="left", padx=10)
        self._refresh()

    # ------------------------------------------------------------------ helpers
    def _min_window(self):
        return G.parse_int(self.v_win.get())

    def _sync(self):
        self.spec.module = self.v_module.get().strip()
        self.spec.style = next(k for k, v in X.STYLES.items() if v == self.v_style.get())
        self._validate()

    def _capacity(self):
        if self._busy:
            return
        try:
            nm, ns = int(self.v_nm.get()), int(self.v_ns.get())
        except ValueError:
            return
        self.spec.resize(nm=max(1, nm), ns=max(1, ns))
        self._refresh()

    def _validate(self):
        errs = self.spec.validate()
        self.msg.delete("1.0", "end")
        if errs:
            self.msg.configure(foreground="#b00020")
            self.msg.insert("end", "\n".join("• " + e for e in errs))
        else:
            sp = self.spec
            used_m = sum(m.enabled for m in sp.masters)
            used_s = sum(s.enabled for s in sp.slaves)
            self.msg.configure(foreground="#1a7f37")
            if sp.style == "flat":
                self.msg.insert("end", f"✓ Düz vektör: NM={used_m}, NS={used_s} (EN=0 portlar atlanır). "
                                       "Port sayısı ve adresler generic; haritada olmayan adresler DECERR döner.")
            else:
                self.msg.insert("end", f"✓ {sp.nm} master portu ({used_m} kullanımda), {sp.ns} slave portu "
                                       f"({used_s} kullanımda). Kapalı ve haritada olmayan adresler DECERR döner.")
        return errs

    def _refresh(self, select=None):
        self._busy = True
        self.v_nm.set(str(self.spec.nm))
        self.v_ns.set(str(self.spec.ns))
        self._busy = False
        keep = select or (self.tree.selection()[0] if self.tree.selection() else None)
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.spec.masters):
            self.tree.insert("", "end", iid=f"S{i}", text=f"MASTER{i}", values=(int(m.enabled), m.name, "", "", "", ""),
                             tags=() if m.enabled else ("off",))
        for j, sl in enumerate(self.spec.slaves):
            self.tree.insert("", "end", iid=f"M{j}", text=f"SLAVE{j}",
                             values=(int(sl.enabled), sl.name, hexs(sl.base), hexs(sl.mask), f"0x{sl.window:X}",
                                     f"0x{sl.span:X}" if sl.span else "—"),
                             tags=() if sl.enabled else ("off",))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        self._validate()

    def _sel(self):
        s = self.tree.selection()
        if not s:
            return None, None
        lst = self.spec.masters if s[0][0] == "S" else self.spec.slaves
        return lst, int(s[0][1:])

    # ------------------------------------------------------------------ actions
    def _load_project(self):
        p = filedialog.askopenfilename(parent=self, title="Debug projesi",
                                       filetypes=[("Debug projesi", "*.axproj.json"), ("Tümü", "*.*")])
        if not p:
            return
        try:
            proj = Project.load(p)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        used = X.slaves_from_project(proj, self._min_window())
        spare = max(0, self.spec.ns - len(used))
        self.spec.slaves = used + [X.XbarSlave(f"spare{len(used) + k}", 0, 0xFFFFF000, 0, False) for k in range(spare)]
        self._refresh("M0")

    def _add_current(self):
        sp = self._current()
        span = max((r.offset for r in sp.registers), default=0) + 4
        new = X.XbarSlave(sp.module, sp.base, X.window_mask(span, self._min_window()), span, True)
        free = next((j for j, s in enumerate(self.spec.slaves) if not s.enabled), None)
        if free is None:
            self.spec.slaves.append(new)
            free = self.spec.ns - 1
        else:
            self.spec.slaves[free] = new
        self._refresh(f"M{free}")

    def _edit(self):
        lst, k = self._sel()
        if lst is None:
            return
        tag = ("S" if lst is self.spec.masters else "M") + str(k)
        new = PortDialog(self, lst[k], ("MASTER" if tag[0] == "S" else "SLAVE") + str(k)).result
        if new:
            lst[k] = new
            self._refresh(tag)

    def _toggle(self):
        lst, k = self._sel()
        if lst is not None:
            lst[k].enabled = not lst[k].enabled
            self._refresh(("S" if lst is self.spec.masters else "M") + str(k))

    def _move(self, d):
        lst, k = self._sel()
        if lst is not None and 0 <= k + d < len(lst):
            lst[k], lst[k + d] = lst[k + d], lst[k]
            self._refresh(("S" if lst is self.spec.masters else "M") + str(k + d))

    def _open(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Xbar", "*.xbar.json"), ("Tümü", "*.*")])
        if not p:
            return
        try:
            self.spec = X.XbarSpec.load(p)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        self.path = p
        self.v_module.set(self.spec.module)
        self.v_style.set(X.STYLES.get(self.spec.style, X.STYLES["packed"]))
        self._refresh()

    def _save(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".xbar.json",
                                         initialfile=f"{self.spec.module}.xbar.json",
                                         filetypes=[("Xbar", "*.xbar.json")])
        if p:
            self.spec.save(p)
            self.path = p

    def _generate(self):
        if self._validate():
            messagebox.showerror(APP_TITLE, "Önce hataları düzeltin.", parent=self)
            return
        d = filedialog.askdirectory(parent=self, title="Çıkış klasörü")
        if not d:
            return
        try:
            paths = X.write_xbar(self.spec, d)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        messagebox.showinfo(APP_TITLE, "Üretildi:\n" + "\n".join(os.path.basename(p) for p in paths)
                            + "\n\nProjeye axilxbar.v (hdl/, 2005 yamalı), wb2axip'ten addrdecode.v ve skidbuffer.v "
                              "dosyalarını da ekleyin."
                            + ("\nSlave ve master'lar için Araçlar → 'Hazır IP'yi paketle' ile _pk wrapper üretin."
                               if self.spec.style != "signals" else ""), parent=self)


# =============================================================================
# Main window
# =============================================================================
class App:
    def __init__(self, root):
        self.root = root
        self.spec = G.ModuleSpec()
        self.path = None
        self.dirty = False
        self.outdir = None
        self._build()
        self._load_spec_into_ui()

    # ------------------------------------------------------------------ UI
    def _build(self):
        r = self.root
        r.title(APP_TITLE)
        r.geometry("1200x780")
        r.minsize(980, 620)
        self._menu()

        top = ttk.Frame(r, padding=(10, 8))
        top.pack(fill="x")
        self.v_module = tk.StringVar()
        self.v_base = tk.StringVar()
        ttk.Label(top, text="Modül").pack(side="left")
        ttk.Entry(top, textvariable=self.v_module, width=22).pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Base adres").pack(side="left")
        ttk.Entry(top, textvariable=self.v_base, width=12).pack(side="left", padx=(4, 12))
        self.v_packed = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Paketli AXI portu (SLAVE_AXI_REQ/RSP)", variable=self.v_packed,
                        command=self._packed_changed).pack(side="left", padx=4)
        for v in (self.v_module, self.v_base):
            v.trace_add("write", lambda *a: self._header_changed())

        paned = ttk.PanedWindow(r, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10)

        # registers
        left = ttk.Frame(paned)
        paned.add(left, weight=2)
        ttk.Label(left, text="Registerlar", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.reg_tree = ttk.Treeview(left, columns=("off", "acc", "opt"), selectmode="browse", height=14)
        for c, h, w in (("#0", "Ad", 130), ("off", "Offset", 60), ("acc", "Erişim", 55), ("opt", "Strobe", 60)):
            self.reg_tree.heading(c, text=h)
            self.reg_tree.column(c, width=w, anchor="w" if c == "#0" else "center")
        self.reg_tree.pack(fill="both", expand=True, pady=(4, 0))
        self.reg_tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_fields())
        self.reg_tree.bind("<Double-1>", lambda e: self._edit_reg())
        rb = ttk.Frame(left)
        rb.pack(fill="x", pady=4)
        for text, cmd in (("+ Register", self._add_reg), ("Düzenle", self._edit_reg), ("Sil", self._del_reg)):
            ttk.Button(rb, text=text, command=cmd).pack(side="left", padx=(0, 3))
        rb2 = ttk.Frame(left)
        rb2.pack(fill="x")
        ttk.Button(rb2, text="▲", width=3, command=lambda: self._move_reg(-1)).pack(side="left")
        ttk.Button(rb2, text="▼", width=3, command=lambda: self._move_reg(1)).pack(side="left", padx=3)
        ttk.Button(rb2, text="Offset'leri sırala", command=self._repack).pack(side="left")

        # fields / preview
        right = ttk.Frame(paned)
        paned.add(right, weight=5)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        ftab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(ftab, text="Alanlar")
        self.reg_title = ttk.Label(ftab, text="", font=("TkDefaultFont", 11, "bold"))
        self.reg_title.pack(anchor="w")
        self.bitmap = tk.Canvas(ftab, height=54, highlightthickness=0, background="#fafafa")
        self.bitmap.pack(fill="x", pady=(4, 6))
        self.bitmap.bind("<Configure>", lambda e: self._draw_bitmap())
        cols = ("bits", "kind", "reset", "opt", "port", "vals", "desc")
        self.field_tree = ttk.Treeview(ftab, columns=cols, selectmode="browse", height=10)
        for c, h, w in (("#0", "Alan", 120), ("bits", "Bitler", 65), ("kind", "Tip", 60), ("reset", "Reset", 70),
                        ("opt", "Seçenek", 70), ("port", "Port", 170), ("vals", "Değer", 50),
                        ("desc", "Açıklama", 220)):
            self.field_tree.heading(c, text=h)
            self.field_tree.column(c, width=w, anchor="w" if c in ("#0", "port", "desc") else "center")
        self.field_tree.pack(fill="both", expand=True)
        self.field_tree.bind("<Double-1>", lambda e: self._edit_field())
        fb = ttk.Frame(ftab)
        fb.pack(fill="x", pady=4)
        for text, cmd in (("+ Alan", self._add_field), ("Düzenle", self._edit_field), ("Sil", self._del_field)):
            ttk.Button(fb, text=text, command=cmd).pack(side="left", padx=(0, 3))
        legend = ttk.Frame(ftab)
        legend.pack(anchor="w", pady=(4, 0))
        for k in G.KINDS:
            tk.Label(legend, text=f" {k} ", background=KIND_COLORS[k], foreground="white",
                     font=("TkDefaultFont", 8, "bold")).pack(side="left", padx=2)
            ttk.Label(legend, text=KIND_SHORT[k] + "  ", foreground="#555",
                      font=("TkDefaultFont", 8)).pack(side="left")

        dtab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(dtab, text="IP açıklaması")
        ttk.Label(dtab, text="IP'nin ne yaptığı, kullanım sırası, dikkat edilecekler… "
                             "(ilk satır HDL başlığına da yazılır)", foreground="#555").pack(anchor="w")
        self.ip_desc = TextField(dtab, "", height=20)
        self.ip_desc.pack(fill="both", expand=True, pady=(4, 0))
        self.ip_desc.text.bind("<<Modified>>", self._ip_desc_changed)

        ptab = ttk.Frame(self.tabs, padding=4)
        self.tabs.add(ptab, text="Verilog önizleme")
        self.preview = tk.Text(ptab, font=("Consolas", 9), wrap="none")
        sb = ttk.Scrollbar(ptab, command=self.preview.yview)
        self.preview.configure(yscrollcommand=sb.set)
        self.preview.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._refresh_preview())

        # validation + actions
        bottom = ttk.Frame(r, padding=(10, 6))
        bottom.pack(fill="x")
        self.msg = tk.Text(bottom, height=4, font=("Consolas", 9), foreground="#b00020",
                           background="#fff8f8", relief="flat")
        self.msg.pack(fill="x")
        act = ttk.Frame(bottom)
        act.pack(fill="x", pady=(6, 0))
        ttk.Button(act, text="Üret…", command=self._generate).pack(side="left")
        ttk.Button(act, text="Debug projesine ekle…", command=self._add_to_project).pack(side="left", padx=6)
        ttk.Button(act, text="axilxbar üret…",
                   command=lambda: XbarWindow(self.root, lambda: self.spec)).pack(side="left", padx=(0, 6))
        self.xlsx_btn = ttk.Button(act, text="Excel'e aktar…", command=self._export_xlsx)
        self.xlsx_btn.pack(side="left", padx=(0, 6))
        if not regmap_xlsx.available():
            self.xlsx_btn.state(["disabled"])
        self.sim_btn = ttk.Button(act, text="Testbench'i çalıştır (iverilog)", command=self._simulate)
        self.sim_btn.pack(side="left")
        if not shutil.which("iverilog"):
            self.sim_btn.state(["disabled"])
        self.status = tk.StringVar(value="Hazır")
        ttk.Label(act, textvariable=self.status, foreground="#555").pack(side="right")

    def _menu(self):
        m = tk.Menu(self.root)
        fm = tk.Menu(m, tearoff=False)
        fm.add_command(label="Yeni", command=self._new)
        fm.add_command(label="Aç…", command=self._open, accelerator="Ctrl+O")
        fm.add_command(label="Kaydet", command=self._save, accelerator="Ctrl+S")
        fm.add_command(label="Farklı kaydet…", command=lambda: self._save(True))
        fm.add_separator()
        fm.add_command(label="Örnek spesifikasyon yükle", command=self._example)
        fm.add_separator()
        fm.add_command(label="Çıkış", command=self._quit)
        m.add_cascade(label="Dosya", menu=fm)
        tm = tk.Menu(m, tearoff=False)
        tm.add_command(label="axilxbar wrapper üret…",
                       command=lambda: XbarWindow(self.root, lambda: self.spec))
        tm.add_command(label="Hazır IP'yi paketle (SLAVE/MASTER_AXI_REQ/RSP)…", command=self._pack_existing)
        m.add_cascade(label="Araçlar", menu=tm)
        self.root.config(menu=m)
        self.root.bind("<Control-s>", lambda e: self._save())
        self.root.bind("<Control-o>", lambda e: self._open())
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------------ state
    def _mark(self):
        self.dirty = True
        self._refresh_title()
        self._validate()

    def _refresh_title(self):
        name = os.path.basename(self.path) if self.path else "adsız"
        self.root.title(f"{APP_TITLE} — {name}{' *' if self.dirty else ''}")

    def _header_changed(self):
        if getattr(self, "_loading", False):
            return
        self.spec.module = self.v_module.get().strip()
        try:
            self.spec.base = G.parse_int(self.v_base.get())
        except ValueError:
            pass
        self._refresh_fields()
        self._mark()

    def _packed_changed(self):
        if getattr(self, "_loading", False):
            return
        self.spec.packed = self.v_packed.get()
        self._mark()

    def _ip_desc_changed(self, _event=None):
        self.ip_desc.text.edit_modified(False)
        if getattr(self, "_loading", False):
            return
        text = self.ip_desc.get()
        if text != self.spec.desc:
            self.spec.desc = text
            self.dirty = True
            self._refresh_title()

    def _load_spec_into_ui(self):
        self._loading = True
        self.v_module.set(self.spec.module)
        self.v_base.set(hexs(self.spec.base))
        self.v_packed.set(self.spec.packed)
        self.ip_desc.set(self.spec.desc)
        self.root.update_idletasks()
        self.ip_desc.text.edit_modified(False)
        self._loading = False
        self._refresh_regs()
        self._validate()
        self._refresh_title()

    def _validate(self):
        errs = self.spec.validate()
        try:
            G.parse_int(self.v_base.get())
        except ValueError:
            errs.insert(0, "Base adres geçerli bir sayı değil")
        self.msg.delete("1.0", "end")
        if errs:
            self.msg.configure(foreground="#b00020")
            self.msg.insert("end", "\n".join("• " + e for e in errs))
        else:
            n = sum(len(r.fields) for r in self.spec.registers)
            self.msg.configure(foreground="#1a7f37")
            self.msg.insert("end", f"✓ Geçerli: {len(self.spec.registers)} register, {n} alan. "
                                   f"Adres çözümü [{self.spec.reg_aw + 1}:2], blok 0x{self.spec.span:X} byte. "
                                   f"Interconnect adresinin tamamı bağlanabilir.")
        return errs

    # ------------------------------------------------------------------ registers
    def _sel_reg(self):
        sel = self.reg_tree.selection()
        return next((r for r in self.spec.registers if sel and r.name == sel[0]), None)

    def _refresh_regs(self, select=None):
        keep = select or (self.reg_tree.selection()[0] if self.reg_tree.selection() else None)
        self.reg_tree.delete(*self.reg_tree.get_children())
        for r in self.spec.regs:
            opt = "/".join(s for s, on in (("wr", r.wr_strobe), ("rd", r.rd_strobe)) if on)
            self.reg_tree.insert("", "end", iid=r.name, text=r.name,
                                 values=(f"0x{r.offset:02X}", r.access, opt))
        if keep and self.reg_tree.exists(keep):
            self.reg_tree.selection_set(keep)
        elif self.reg_tree.get_children():
            self.reg_tree.selection_set(self.reg_tree.get_children()[0])
        self._refresh_fields()

    def _add_reg(self):
        off = max((r.offset for r in self.spec.registers), default=-4) + 4
        new = RegDialog(self.root, G.RegSpec(f"REG{off // 4}", off), "Yeni register").result
        if new:
            if any(r.name == new.name for r in self.spec.registers):
                messagebox.showerror(APP_TITLE, "Bu isimde bir register var")
                return
            new.fields = []
            self.spec.registers.append(new)
            self._refresh_regs(new.name)
            self._mark()
            self._add_field()

    def _edit_reg(self):
        r = self._sel_reg()
        if not r:
            return
        new = RegDialog(self.root, r, "Register düzenle").result
        if new:
            if new.name != r.name and any(x.name == new.name for x in self.spec.registers):
                messagebox.showerror(APP_TITLE, "Bu isimde bir register var")
                return
            self.spec.registers[self.spec.registers.index(r)] = new
            self._refresh_regs(new.name)
            self._mark()

    def _del_reg(self):
        r = self._sel_reg()
        if r and messagebox.askyesno(APP_TITLE, f"{r.name} silinsin mi?"):
            self.spec.registers.remove(r)
            self._refresh_regs()
            self._mark()

    def _move_reg(self, delta):
        r = self._sel_reg()
        regs = self.spec.regs
        if not r:
            return
        i = regs.index(r)
        j = i + delta
        if 0 <= j < len(regs):
            regs[i].offset, regs[j].offset = regs[j].offset, regs[i].offset
            self._refresh_regs(r.name)
            self._mark()

    def _repack(self):
        for i, r in enumerate(self.spec.regs):
            r.offset = 4 * i
        self._refresh_regs()
        self._mark()

    # ------------------------------------------------------------------ fields
    def _sel_field(self):
        r = self._sel_reg()
        sel = self.field_tree.selection()
        return r, next((f for f in (r.fields if r else []) if sel and f.name == sel[0]), None)

    def _refresh_fields(self):
        self.field_tree.delete(*self.field_tree.get_children())
        r = self._sel_reg()
        if not r:
            self.reg_title.config(text="Soldan bir register seçin ya da ekleyin")
            self._draw_bitmap()
            return
        self.reg_title.config(text=f"{r.name}  @ 0x{r.offset:02X}  ({r.access})"
                                   f"   adres {hexs(self.spec.base + r.offset)}   {single_line(r.desc)}")
        for f in sorted(r.fields, key=lambda f: -f.lsb):
            opt = " ".join(s for s, on in (("irq", f.irq), ("sync", f.sync)) if on)
            reset = f"0x{f.reset:X}" if f.kind in ("RW", "CONST") else ""
            ports = ", ".join(G.port_names(r, f)) or "—"
            self.field_tree.insert("", "end", iid=f.name, text=f.name,
                                   values=(f.bits, f.kind, reset, opt, ports, len(f.values) or "",
                                           single_line(f.desc)), tags=(f.kind,))
        for k, c in KIND_COLORS.items():
            self.field_tree.tag_configure(k, foreground=c)
        self._draw_bitmap()

    def _draw_bitmap(self):
        c = self.bitmap
        c.delete("all")
        r = self._sel_reg()
        w = max(c.winfo_width(), 320)
        cell = (w - 4) / 32
        owner = {}
        for f in (r.fields if r else []):
            for b in range(f.lsb, f.msb + 1):
                owner[b] = f
        for i in range(32):
            b = 31 - i
            x0 = 2 + i * cell
            f = owner.get(b)
            fill = KIND_COLORS[f.kind] if f else "#eeeeee"
            c.create_rectangle(x0, 18, x0 + cell - 1, 40, fill=fill, outline="#ffffff")
            if b % 4 == 0 or b == 31:
                c.create_text(x0 + cell / 2, 9, text=str(b), font=("TkDefaultFont", 7), fill="#555")
        for f in (r.fields if r else []):
            x0 = 2 + (31 - f.msb) * cell
            x1 = 2 + (32 - f.lsb) * cell
            if x1 - x0 > 24:
                c.create_text((x0 + x1) / 2, 29, text=f.name[: int((x1 - x0) / 7)],
                              font=("TkDefaultFont", 7, "bold"), fill="white")

    def _add_field(self):
        r = self._sel_reg()
        if not r:
            return
        used = 0
        for f in r.fields:
            used |= f.mask
        lsb = next((b for b in range(32) if not used >> b & 1), 0)
        proto = G.FieldSpec(r.name, 0, 32) if not r.fields else G.FieldSpec(f"BIT{lsb}", lsb)
        new = FieldDialog(self.root, proto, f"{r.name}: yeni alan").result
        if new:
            if any(f.name == new.name for f in r.fields):
                messagebox.showerror(APP_TITLE, "Bu isimde bir alan var")
                return
            r.fields.append(new)
            self._refresh_regs(r.name)
            self._mark()

    def _edit_field(self):
        r, f = self._sel_field()
        if not f:
            return
        new = FieldDialog(self.root, f, f"{r.name}.{f.name}").result
        if new:
            if new.name != f.name and any(x.name == new.name for x in r.fields):
                messagebox.showerror(APP_TITLE, "Bu isimde bir alan var")
                return
            r.fields[r.fields.index(f)] = new
            self._refresh_regs(r.name)
            self._mark()

    def _del_field(self):
        r, f = self._sel_field()
        if f and messagebox.askyesno(APP_TITLE, f"{r.name}.{f.name} silinsin mi?"):
            r.fields.remove(f)
            self._refresh_regs(r.name)
            self._mark()

    # ------------------------------------------------------------------ preview / output
    def _refresh_preview(self):
        if self.tabs.tab("current", "text") != "Verilog önizleme":
            return
        self.preview.delete("1.0", "end")
        try:
            self.preview.insert("end", G.gen_verilog(self.spec))
        except G.SpecError as exc:
            self.preview.insert("end", "// Spesifikasyon geçerli değil:\n// " + str(exc).replace("\n", "\n// "))

    def _ready(self):
        if self._validate():
            messagebox.showerror(APP_TITLE, "Önce alttaki hataları düzeltin.")
            return False
        return True

    def _generate(self):
        if not self._ready():
            return
        d = filedialog.askdirectory(title="Çıkış klasörü", initialdir=self.outdir or os.getcwd())
        if not d:
            return
        self.outdir = d
        try:
            paths = G.generate_all(self.spec, d)
        except (OSError, G.SpecError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.status.set(f"{len(paths)} dosya üretildi: {d}")
        messagebox.showinfo(APP_TITLE, "Üretilen dosyalar:\n\n" + "\n".join(os.path.basename(p) for p in paths)
                            + f"\n\nDebug uygulamasında {self.spec.module}.axproj.json dosyasını açabilir "
                              "ya da 'Debug projesine ekle' ile mevcut projene ekleyebilirsin.")

    def _add_to_project(self):
        if not self._ready():
            return
        path = filedialog.asksaveasfilename(title="Debug projesi (.axproj.json) seç ya da oluştur",
                                            defaultextension=".axproj.json", confirmoverwrite=False,
                                            filetypes=[("Debug projesi", "*.axproj.json"), ("Tümü", "*.*")])
        if not path:
            return
        try:
            proj = G.add_to_project(self.spec, path, replace=True)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.status.set(f"{self.spec.module} eklendi → {os.path.basename(path)} "
                        f"({len(proj.ips)} IP)")

    def _pack_existing(self):
        src = filedialog.askopenfilename(title="AXI4-Lite modülü (.v)", filetypes=[("Verilog", "*.v *.sv"), ("Tümü", "*.*")])
        if not src:
            return
        try:
            with open(src, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            names = [n for n in re.findall(r"^\s*module\s+([A-Za-z_]\w*)", text, re.M)]
            infos = []
            for n in names:
                try:
                    info = axilpack.parse_module(text, n)
                except axilpack.ParseError:
                    continue
                if axilpack.axi_groups(info):
                    infos.append(info)
            if not infos:
                raise axilpack.ParseError("Dosyada S_AXI_* / M_AXI_* portlu ANSI başlıklı modül bulunamadı")
        except (OSError, axilpack.ParseError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        d = filedialog.askdirectory(title="Çıkış klasörü", initialdir=os.path.dirname(src))
        if not d:
            return
        out = []
        for info in infos:
            path = os.path.join(d, f"{info['name']}_pk.v")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(axilpack.gen_pack_wrapper(info))
            out.append(f"{os.path.basename(path)}  ({', '.join(axilpack.PACKED_NAME[g] + '_REQ/RSP' for g in axilpack.axi_groups(info))})")
        messagebox.showinfo(APP_TITLE, "Üretildi:\n" + "\n".join(out))

    def _export_xlsx(self):
        if not self._ready():
            return
        path = filedialog.asksaveasfilename(title="Kütük haritası (Excel)", defaultextension=".xlsx",
                                            initialfile=f"{self.spec.module}_regmap.xlsx",
                                            initialdir=self.outdir or os.getcwd(),
                                            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            G.export_xlsx(self.spec, path)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.status.set(f"Excel'e aktarıldı: {path}")

    def _simulate(self):
        if not self._ready():
            return
        d = self.outdir or os.path.join(os.getcwd(), "axilgen_out")
        try:
            G.generate_all(self.spec, d)
            m = self.spec.module
            exe = os.path.join(d, f"{m}_tb.vvp")
            srcs = [os.path.join(d, f) for f in [f"{m}_tb.v"] + G.hdl_files(self.spec)]
            subprocess.run(["iverilog", "-g2012", "-o", exe] + srcs, check=True, capture_output=True, text=True)
            out = subprocess.run(["vvp", "-n", exe], capture_output=True, text=True, timeout=120).stdout
        except subprocess.CalledProcessError as exc:
            messagebox.showerror(APP_TITLE, "iverilog hatası:\n" + exc.stderr[-2000:])
            return
        except (OSError, subprocess.TimeoutExpired) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        lines = [l for l in out.splitlines() if l.startswith(("FAIL", "TIMEOUT")) or "checks" in l]
        summary = lines[-1] if lines else out[-500:]
        self.status.set(summary)
        (messagebox.showinfo if "PASSED" in summary else messagebox.showerror)(
            APP_TITLE, "\n".join(lines[-20:]) or out[-2000:])

    # ------------------------------------------------------------------ files
    def _confirm_discard(self):
        return not self.dirty or messagebox.askyesno(APP_TITLE, "Kaydedilmemiş değişiklikler silinsin mi?")

    def _new(self):
        if self._confirm_discard():
            self.spec, self.path, self.dirty = G.ModuleSpec(), None, False
            self._load_spec_into_ui()

    def _example(self):
        if self._confirm_discard():
            self.spec, self.path, self.dirty = G.example_spec(), None, True
            self._load_spec_into_ui()

    def _open(self):
        if not self._confirm_discard():
            return
        p = filedialog.askopenfilename(filetypes=[("Register spesifikasyonu", "*.regspec.json *.json"),
                                                  ("Tümü", "*.*")])
        if not p:
            return
        try:
            self.spec = G.ModuleSpec.load(p)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, f"Açılamadı: {exc}")
            return
        self.path, self.dirty = p, False
        self.outdir = os.path.dirname(p)
        self._load_spec_into_ui()

    def _save(self, as_new=False):
        p = self.path
        if as_new or not p:
            p = filedialog.asksaveasfilename(defaultextension=".regspec.json",
                                             initialfile=f"{self.spec.module}.regspec.json",
                                             filetypes=[("Register spesifikasyonu", "*.regspec.json")])
            if not p:
                return
        try:
            self.spec.save(p)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.path, self.dirty = p, False
        self._refresh_title()
        self.status.set(f"Kaydedildi: {p}")

    def _quit(self):
        if self._confirm_discard():
            self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if os.name == "nt" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
