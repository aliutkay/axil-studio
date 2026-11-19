#!/usr/bin/env python3
"""
AXI4-Lite UART Console

Desktop tool for driving AXI4-Lite IP blocks through the ZipCPU hexbus
UART-to-AXI4-Lite bridge.

  * Project tree of IP blocks, registers and bit fields (JSON project file)
  * Register read/write and per-field controls (1-bit fields as checkboxes)
  * Register value-set configs: build, save, load and apply to the device
  * Live view with periodic polling
  * Raw hexbus traffic log
  * Built-in simulator port for offline use

Requires: Python 3.8+, pyserial (pip install pyserial)
"""

import copy
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from hexbus import HexbusError, HexbusLink, SERIAL_IMPORT_ERROR, SIM_PORT, list_ports
from terminal import TerminalTab
from model import (ACCESS_TYPES, FIELD_KINDS, Field, IPBlock, Project, Register, gpio_example_ip, uart_phy_example_ip,
                   hex32, load_config, parse_int, save_config, single_line)
from widgets import TextField, ValuesEditor, field_details, return_is_ok
import regmap_xlsx

APP_TITLE = "AXI4-Lite UART Console"
BAUD_RATES = ("9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600")


# =============================================================================
# Generic form dialog
# =============================================================================
class FormDialog(tk.Toplevel):
    """Modal dialog with labelled entries/comboboxes.

    spec: list of (key, label, initial, options) where options is None for an
    Entry, "text" for a multi-line box or a tuple of choices for a read-only Combobox.
    validate: callable(dict) -> dict (raise ValueError with a message on error)
    """

    def __init__(self, parent, title, spec, validate):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self._validate = validate
        self._vars = {}

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        for row, (key, label, initial, options) in enumerate(spec):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
            var = tk.StringVar(value=str(initial))
            if options == "text":
                widget = TextField(body, str(initial), height=5, width=46)
                var = widget
            elif options:
                widget = ttk.Combobox(body, textvariable=var, values=options,
                                      state="readonly", width=28)
            else:
                widget = ttk.Entry(body, textvariable=var, width=30)
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            if row == 0:
                widget.focus_set()
            self._vars[key] = var

        buttons = ttk.Frame(body)
        buttons.grid(row=len(spec), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="İptal", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Tamam", command=self._ok).pack(side="right", padx=5)
        self.bind("<Return>", lambda e: self._ok() if return_is_ok(e) else None)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _ok(self):
        values = {k: v.get().strip() for k, v in self._vars.items()}
        try:
            self.result = self._validate(values)
        except ValueError as exc:
            messagebox.showerror("Geçersiz değer", str(exc), parent=self)
            return
        self.destroy()


def _require_name(text, what):
    if not text or not text.replace("_", "").isalnum():
        raise ValueError(f"{what} adı boş olamaz ve sadece harf, rakam ve _ içerebilir.")
    return text


def _int_or_error(text, what):
    try:
        return parse_int(text)
    except ValueError:
        raise ValueError(f"{what} geçerli bir sayı değil (ör. 0x60000000).")


# =============================================================================
# Register editor (register properties + bit-field list)
# =============================================================================
class FieldDialog(tk.Toplevel):
    """Bit field editor: position, type, multi-line description and value meanings."""

    def __init__(self, parent, f: Field, title, others):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self._others = others
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self.v_name = tk.StringVar(value=f.name)
        self.v_lsb = tk.StringVar(value=str(f.lsb))
        self.v_width = tk.StringVar(value=str(f.width))
        self.v_kind = tk.StringVar(value=f.kind)
        rows = (("Ad", ttk.Entry(body, textvariable=self.v_name, width=30)),
                ("LSB (bit no)", ttk.Entry(body, textvariable=self.v_lsb, width=30)),
                ("Genişlik (bit)", ttk.Entry(body, textvariable=self.v_width, width=30)),
                ("Tip (W1C/PULSE için önemli)", ttk.Combobox(body, textvariable=self.v_kind, values=FIELD_KINDS,
                                                             state="readonly", width=28)))
        for r, (label, w) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=3)
            w.grid(row=r, column=1, sticky="ew", pady=3)
        rows[0][1].focus_set()
        rows[3][1].bind("<<ComboboxSelected>>", lambda e: self.values.set_kind(self.v_kind.get()))
        ttk.Label(body, text="Açıklama").grid(row=4, column=0, sticky="nw", pady=3)
        self.t_desc = TextField(body, f.desc, height=3, width=46)
        self.t_desc.grid(row=4, column=1, sticky="ew", pady=3)
        self.values = ValuesEditor(body, f.values, f.kind, self._cur_width)
        self.values.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        bar = ttk.Frame(body)
        bar.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="İptal", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Tamam", command=self._ok).pack(side="right", padx=5)
        self.bind("<Return>", lambda e: self._ok() if return_is_ok(e) else None)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _cur_width(self):
        try:
            return max(1, min(32, parse_int(self.v_width.get())))
        except ValueError:
            return 32

    def _ok(self):
        try:
            name = _require_name(self.v_name.get().strip(), "Alan")
            if name in {o.name for o in self._others}:
                raise ValueError("Bu isimde bir alan zaten var.")
            lsb, width = _int_or_error(self.v_lsb.get(), "LSB"), _int_or_error(self.v_width.get(), "Genişlik")
            if not (0 <= lsb <= 31 and 1 <= width and lsb + width <= 32):
                raise ValueError("Alan 32 bitlik registerın içinde kalmalı.")
            new_mask = ((1 << width) - 1) << lsb
            if any(o.mask & new_mask for o in self._others):
                raise ValueError("Bu alan başka bir alanla çakışıyor.")
            values = self.values.get()
            bad = [f"0x{e.value:X}" for e in values if e.value >> width]
            if bad:
                raise ValueError(f"Değer tablosundaki {', '.join(bad)} {width} bite sığmıyor.")
        except ValueError as exc:
            messagebox.showerror("Geçersiz değer", str(exc), parent=self)
            return
        self.result = Field(name, lsb, width, self.t_desc.get(), self.v_kind.get(), values)
        self.destroy()


class RegisterDialog(tk.Toplevel):
    def __init__(self, parent, reg: Register, existing_names):
        super().__init__(parent)
        self.title("Register")
        self.transient(parent)
        self.result = None
        self._existing = existing_names
        self._fields = copy.deepcopy(reg.fields)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self._v_name = tk.StringVar(value=reg.name)
        self._v_offset = tk.StringVar(value=hex32(reg.offset))
        self._v_access = tk.StringVar(value=reg.access)
        self._v_reset = tk.StringVar(value=hex32(reg.reset))
        self._t_desc = TextField(body, reg.desc, height=3, width=40)

        rows = (("Ad", ttk.Entry(body, textvariable=self._v_name, width=30)),
                ("Offset", ttk.Entry(body, textvariable=self._v_offset, width=30)),
                ("Erişim", ttk.Combobox(body, textvariable=self._v_access, values=ACCESS_TYPES,
                                        state="readonly", width=28)),
                ("Reset değeri", ttk.Entry(body, textvariable=self._v_reset, width=30)),
                ("Açıklama", self._t_desc))
        for r, (label, widget) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="nw", padx=(0, 8), pady=3)
            widget.grid(row=r, column=1, sticky="ew", pady=3)

        box = ttk.LabelFrame(body, text="Bit alanları", padding=6)
        box.grid(row=len(rows), column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self._tree = ttk.Treeview(box, columns=("bits", "kind", "vals", "desc"), height=6)
        self._tree.heading("#0", text="Alan")
        self._tree.heading("bits", text="Bitler")
        self._tree.heading("kind", text="Tip")
        self._tree.heading("vals", text="Değer")
        self._tree.column("vals", width=50, anchor="center")
        self._tree.heading("desc", text="Açıklama")
        self._tree.column("#0", width=110)
        self._tree.column("bits", width=70, anchor="center")
        self._tree.column("kind", width=55, anchor="center")
        self._tree.column("desc", width=200)
        self._tree.pack(fill="both", expand=True)
        self._tree.bind("<Double-1>", lambda e: self._edit_field())
        fb = ttk.Frame(box)
        fb.pack(fill="x", pady=(4, 0))
        ttk.Button(fb, text="Ekle", command=self._add_field).pack(side="left")
        ttk.Button(fb, text="Düzenle", command=self._edit_field).pack(side="left", padx=4)
        ttk.Button(fb, text="Sil", command=self._delete_field).pack(side="left")

        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="İptal", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Tamam", command=self._ok).pack(side="right", padx=5)
        self._refresh()
        self.grab_set()
        self.wait_window()

    def _refresh(self):
        self._tree.delete(*self._tree.get_children())
        for i, f in enumerate(sorted(self._fields, key=lambda f: f.lsb)):
            self._tree.insert("", "end", iid=f.name, text=f.name, values=(f.bits, f.kind or "—", len(f.values) or "",
                                                                       single_line(f.desc)))

    def _field_form(self, f: Field, title):
        return FieldDialog(self, f, title, [x for x in self._fields if x is not f]).result

    def _add_field(self):
        used = 0
        for f in self._fields:
            used |= f.mask
        lsb = next((b for b in range(32) if not used >> b & 1), 0)
        new = self._field_form(Field(f"BIT{lsb}", lsb, 1, ""), "Yeni alan")
        if new:
            self._fields.append(new)
            self._refresh()

    def _selected_field(self):
        sel = self._tree.selection()
        return next((f for f in self._fields if sel and f.name == sel[0]), None)

    def _edit_field(self):
        f = self._selected_field()
        if f:
            new = self._field_form(f, "Alanı düzenle")
            if new:
                self._fields[self._fields.index(f)] = new
                self._refresh()

    def _delete_field(self):
        f = self._selected_field()
        if f:
            self._fields.remove(f)
            self._refresh()

    def _ok(self):
        try:
            name = _require_name(self._v_name.get().strip(), "Register")
            if name in self._existing:
                raise ValueError("Bu IP'de aynı isimde bir register var.")
            offset = _int_or_error(self._v_offset.get(), "Offset")
            if offset % 4:
                raise ValueError("Offset 4'ün katı olmalı (32-bit erişim).")
            reset = _int_or_error(self._v_reset.get(), "Reset değeri")
        except ValueError as exc:
            messagebox.showerror("Geçersiz değer", str(exc), parent=self)
            return
        self.result = Register(name, offset, self._v_access.get(), reset & 0xFFFFFFFF,
                               self._t_desc.get(),
                               sorted(self._fields, key=lambda f: f.lsb))
        self.destroy()


# =============================================================================
# Main application
# =============================================================================
class App:
    def __init__(self, root):
        self.root = root
        self.project = Project("Yeni proje", [gpio_example_ip(), uart_phy_example_ip()])
        self.dirty = False
        self.values = {}            # (ip, reg) -> last known register value
        self.watch = []             # list of (ip, reg) being polled
        self.polling = False
        self.config_rows = []       # list of [ip, reg, value]
        self._ui_queue = queue.Queue()
        self._jobs = queue.Queue()
        self._field_vars = {}

        self.link = HexbusLink(log_cb=self._log_from_thread,
                               interrupt_cb=lambda: self._log_from_thread("INT", "I"))
        threading.Thread(target=self._worker, daemon=True).start()

        self._build_ui()
        self._refresh_tree()
        self.terminal.refresh_ips()
        self._refresh_title()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(50, self._pump_ui)

    # ---------------------------------------------------------------- threading
    def _worker(self):
        """Executes bus jobs sequentially so the GUI never blocks."""
        while True:
            func, callback = self._jobs.get()
            try:
                result, error = func(), None
            except Exception as exc:
                result, error = None, exc
            if callback:
                self._ui_queue.put(("CALL", (callback, result, error)))
            elif error:
                self._ui_queue.put(("ERROR", error))

    def _submit(self, func, callback=None):
        if not self.link.connected:
            self._status("Bağlı değil")
            return False
        self._jobs.put((func, callback))
        return True

    def _log_from_thread(self, kind, text):
        self._ui_queue.put(("LOG", (kind, text)))

    def _pump_ui(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "LOG":
                    self._log(*payload)
                elif kind == "CALL":
                    callback, result, error = payload
                    callback(result, error)
                elif kind == "ERROR":
                    self._report_error(payload)
        except queue.Empty:
            pass
        self.root.after(30, self._pump_ui)

    def _report_error(self, error):
        self._status(f"Hata: {error}")
        self._log("ERR", str(error))

    # ---------------------------------------------------------------- UI layout
    def _build_ui(self):
        self.status_var = tk.StringVar(value="Hazır")
        self.root.title(APP_TITLE)
        self.root.geometry("1150x760")
        self.root.minsize(900, 600)
        self._build_menu()

        # Connection bar
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Port").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=18)
        self.port_box.pack(side="left", padx=4)
        ttk.Button(bar, text="↻", width=3, command=self._refresh_ports).pack(side="left")
        ttk.Label(bar, text="Baud").pack(side="left", padx=(10, 0))
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(bar, textvariable=self.baud_var, values=BAUD_RATES, width=9).pack(side="left", padx=4)
        self.connect_btn = ttk.Button(bar, text="Bağlan", command=self._toggle_connection)
        self.connect_btn.pack(side="left", padx=(10, 4))
        ttk.Button(bar, text="Hexbus Reset (T)", command=self._bridge_reset).pack(side="left")
        self.conn_label = ttk.Label(bar, text="● Bağlı değil", foreground="gray")
        self.conn_label.pack(side="left", padx=12)
        self._refresh_ports()

        # Main split: tree | tabs ; log at bottom
        vpaned = ttk.PanedWindow(self.root, orient="vertical")
        vpaned.pack(fill="both", expand=True, padx=8)
        hpaned = ttk.PanedWindow(vpaned, orient="horizontal")
        vpaned.add(hpaned, weight=4)

        left = ttk.Frame(hpaned)
        hpaned.add(left, weight=1)
        self.tree = ttk.Treeview(left, columns=("addr", "access"), selectmode="browse")
        self.tree.heading("#0", text="IP / Register")
        self.tree.heading("addr", text="Adres")
        self.tree.heading("access", text="Erişim")
        self.tree.column("#0", width=170)
        self.tree.column("addr", width=100, anchor="center")
        self.tree.column("access", width=55, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_selection())
        self.tree.bind("<Double-1>", lambda e: self._edit_selected())
        tb = ttk.Frame(left)
        tb.pack(fill="x", pady=4)
        ttk.Button(tb, text="+ IP", command=self._add_ip).pack(side="left")
        ttk.Button(tb, text="+ Register", command=self._add_register).pack(side="left", padx=4)
        ttk.Button(tb, text="Düzenle", command=self._edit_selected).pack(side="left")
        ttk.Button(tb, text="Sil", command=self._delete_selected).pack(side="left", padx=4)

        self.tabs = ttk.Notebook(hpaned)
        hpaned.add(self.tabs, weight=3)
        self.detail_tab = ttk.Frame(self.tabs, padding=10)
        self.live_tab = ttk.Frame(self.tabs, padding=10)
        self.config_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.detail_tab, text="Register")
        self.tabs.add(self.live_tab, text="Canlı İzleme")
        self.tabs.add(self.config_tab, text="Config")
        self.terminal = TerminalTab(self.tabs, self)
        self.tabs.add(self.terminal, text="Terminal")
        self._build_live_tab()
        self._build_config_tab()

        logf = ttk.LabelFrame(vpaned, text="Hexbus trafiği", padding=4)
        vpaned.add(logf, weight=1)
        lb = ttk.Frame(logf)
        lb.pack(fill="x")
        self.hide_idle = tk.BooleanVar(value=True)
        ttk.Checkbutton(lb, text="Idle (Z) gizle", variable=self.hide_idle).pack(side="left")
        ttk.Button(lb, text="Temizle", command=lambda: self.log_text.delete("1.0", "end")).pack(side="right")
        self.log_text = tk.Text(logf, height=8, font=("Consolas", 9), wrap="none")
        self.log_text.pack(fill="both", expand=True)
        for tag, color in (("TX", "#1f5fbf"), ("RX", "#1a7f37"), ("ERR", "#c62828"),
                           ("INT", "#b35c00"), ("INFO", "#555555"), ("IDLE", "#999999")):
            self.log_text.tag_configure(tag, foreground=color)

        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(6, 2)).pack(fill="x", side="bottom")

    def _build_menu(self):
        menu = tk.Menu(self.root)
        pm = tk.Menu(menu, tearoff=False)
        pm.add_command(label="Yeni proje", command=self._new_project)
        pm.add_command(label="Proje aç…", command=self._open_project, accelerator="Ctrl+O")
        pm.add_command(label="Kaydet", command=self._save_project, accelerator="Ctrl+S")
        pm.add_command(label="Farklı kaydet…", command=lambda: self._save_project(as_new=True))
        pm.add_separator()
        pm.add_command(label="Örnek GPIO IP ekle", command=self._add_example_gpio)
        pm.add_command(label="Örnek UART IP ekle", command=self._add_example_uart)
        pm.add_separator()
        pm.add_command(label="Seçili IP'nin kütük haritasını Excel'e aktar…",
                       command=lambda: self._export_xlsx(selected=True))
        pm.add_command(label="Tüm IP'leri Excel'e aktar…", command=lambda: self._export_xlsx(selected=False))
        pm.add_separator()
        pm.add_command(label="Çıkış", command=self._on_close)
        menu.add_cascade(label="Proje", menu=pm)
        self.root.config(menu=menu)
        self.root.bind("<Control-s>", lambda e: self._save_project())
        self.root.bind("<Control-o>", lambda e: self._open_project())

    # ---------------------------------------------------------------- helpers
    def _status(self, text):
        self.status_var.set(text)

    def _log(self, kind, text):
        if kind == "IDLE" and self.hide_idle.get():
            return
        stamp = time.strftime("%H:%M:%S")
        prefix = {"TX": "→", "RX": "←", "ERR": "!", "INT": "⚡", "INFO": "·", "IDLE": "←"}.get(kind, " ")
        self.log_text.insert("end", f"{stamp} {prefix} {text}\n", kind)
        if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")

    def _mark_dirty(self):
        self.dirty = True
        self._refresh_title()

    def _refresh_title(self):
        name = os.path.basename(self.project.path) if self.project.path else self.project.name
        self.root.title(f"{APP_TITLE} — {name}{' *' if self.dirty else ''}")

    def _selection(self):
        """Return (ip, reg) for the tree selection; reg may be None."""
        sel = self.tree.selection()
        if not sel:
            return None, None
        parts = sel[0].split("/")
        ip = self.project.get_ip(parts[0])
        reg = ip.get_register(parts[1]) if ip and len(parts) > 1 else None
        return ip, reg

    def _value(self, ip, reg):
        return self.values.get((ip.name, reg.name))

    # ---------------------------------------------------------------- connection
    def _refresh_ports(self):
        ports = list_ports()
        if SERIAL_IMPORT_ERROR:
            self._status("pyserial yüklenemedi: " + SERIAL_IMPORT_ERROR +
                         "  →  pip uninstall serial  &&  pip install pyserial")
        elif ports == [SIM_PORT]:
            self._status("Seri port bulunamadı: kart takılı mı, USB-UART sürücüsü kurulu mu?")
        self.port_box["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else SIM_PORT)

    def _toggle_connection(self):
        if self.link.connected:
            self._stop_polling()
            self.terminal.on_disconnect()
            self.link.close()
            self.connect_btn.config(text="Bağlan")
            self.conn_label.config(text="● Bağlı değil", foreground="gray")
            self._log("INFO", "Bağlantı kapatıldı")
            return
        try:
            baud = int(self.baud_var.get())
            self.link.open(self.port_var.get(), baud)
        except (ValueError, HexbusError, OSError) as exc:
            messagebox.showerror("Bağlantı hatası", str(exc))
            return
        self.connect_btn.config(text="Bağlantıyı kes")
        self.conn_label.config(text=f"● {self.port_var.get()} @ {baud}", foreground="#1a7f37")
        self._log("INFO", f"{self.port_var.get()} açıldı ({baud} 8N1)")
        self._bridge_reset()

    def _bridge_reset(self):
        def done(_, error):
            if error:
                self._report_error(error)
            else:
                self._status("Hexbus reset tamam")
        self._submit(self.link.reset, done)

    def _on_close(self):
        if self.dirty and not messagebox.askyesno(APP_TITLE, "Kaydedilmemiş değişiklikler var. Yine de çıkılsın mı?"):
            return
        self.polling = False
        self.terminal.on_disconnect()
        self.link.close()
        self.root.destroy()

    # ---------------------------------------------------------------- project
    def _new_project(self):
        if self.dirty and not messagebox.askyesno(APP_TITLE, "Kaydedilmemiş değişiklikler silinsin mi?"):
            return
        self.project = Project("Yeni proje", [])
        self.values.clear()
        self.watch.clear()
        self.dirty = False
        self._refresh_all()

    def _open_project(self):
        path = filedialog.askopenfilename(filetypes=[("Proje", "*.axproj.json *.json"), ("Tümü", "*.*")])
        if not path:
            return
        try:
            self.project = Project.load(path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Proje açılamadı", str(exc))
            return
        self.values.clear()
        self.watch.clear()
        self.dirty = False
        self._refresh_all()
        self._status(f"Proje açıldı: {path}")

    def _save_project(self, as_new=False):
        path = self.project.path
        if as_new or not path:
            path = filedialog.asksaveasfilename(defaultextension=".axproj.json",
                                                filetypes=[("Proje", "*.axproj.json")])
            if not path:
                return
        try:
            self.project.save(path)
        except OSError as exc:
            messagebox.showerror("Kaydedilemedi", str(exc))
            return
        self.dirty = False
        self._refresh_title()
        self._status(f"Kaydedildi: {path}")

    def _export_xlsx(self, selected=True):
        if not regmap_xlsx.available():
            messagebox.showerror(APP_TITLE, "Excel çıktısı için openpyxl gerekli:\n\npip install openpyxl")
            return
        ip, _ = self._selection()
        ips = [ip] if selected and ip else list(self.project.ips)
        if selected and not ip:
            messagebox.showinfo(APP_TITLE, "Önce bir IP seçin.")
            return
        if not ips:
            return
        name = ips[0].name if len(ips) == 1 else self.project.name
        path = filedialog.asksaveasfilename(title="Kütük haritası (Excel)", defaultextension=".xlsx",
                                            initialfile=f"{name}_regmap.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            regmap_xlsx.export_xlsx(ips, path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._status(f"Kütük haritası kaydedildi: {path}")

    def _add_example_uart(self):
        name, n = "UART0", 0
        while self.project.get_ip(name):
            n += 1
            name = f"UART{n}"
        ip = uart_phy_example_ip(0x60001000 + 0x1000 * n)
        ip.name = name
        self.project.ips.append(ip)
        self._mark_dirty()
        self._refresh_all()

    def _add_example_gpio(self):
        name, n = "GPIO0", 0
        while self.project.get_ip(name):
            n += 1
            name = f"GPIO{n}"
        ip = gpio_example_ip()
        ip.name = name
        self.project.ips.append(ip)
        self._mark_dirty()
        self._refresh_all()

    def _refresh_all(self):
        self._refresh_tree()
        self._refresh_live()
        self._refresh_config_choices()
        self._refresh_config_table()
        self.terminal.refresh_ips()
        self._refresh_title()

    # ---------------------------------------------------------------- tree editing
    def _refresh_tree(self):
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for ip in self.project.ips:
            self.tree.insert("", "end", iid=ip.name, text=ip.name, values=(hex32(ip.base), ""), open=True)
            for reg in sorted(ip.registers, key=lambda r: r.offset):
                self.tree.insert(ip.name, "end", iid=f"{ip.name}/{reg.name}", text=reg.name,
                                 values=(hex32(ip.address(reg)), reg.access))
        if keep and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])
        else:
            self._show_selection()

    def _ip_form(self, ip, title):
        others = {i.name for i in self.project.ips if i is not ip}

        def validate(v):
            name = _require_name(v["name"], "IP")
            if name in others:
                raise ValueError("Bu isimde bir IP zaten var.")
            base = _int_or_error(v["base"], "Base adres")
            if base % 4:
                raise ValueError("Base adres 4'ün katı olmalı.")
            return name, base, v["desc"]

        return FormDialog(self.root, title, [("name", "Ad", ip.name, None),
                                             ("base", "Base adres", hex32(ip.base), None),
                                             ("desc", "Açıklama", ip.desc, "text")], validate).result

    def _add_ip(self):
        res = self._ip_form(IPBlock(f"IP{len(self.project.ips)}", 0x60000000), "Yeni IP")
        if res:
            self.project.ips.append(IPBlock(*res))
            self._mark_dirty()
            self._refresh_all()
            self.tree.selection_set(res[0])

    def _add_register(self):
        ip, _ = self._selection()
        if not ip:
            messagebox.showinfo(APP_TITLE, "Önce bir IP seçin.")
            return
        offset = max((r.offset for r in ip.registers), default=-4) + 4
        dlg = RegisterDialog(self.root, Register(f"REG{offset // 4}", offset),
                             {r.name for r in ip.registers})
        if dlg.result:
            ip.registers.append(dlg.result)
            self._mark_dirty()
            self._refresh_all()
            self.tree.selection_set(f"{ip.name}/{dlg.result.name}")

    def _edit_selected(self):
        ip, reg = self._selection()
        if not ip:
            return
        if reg is None:
            old = ip.name
            res = self._ip_form(ip, "IP düzenle")
            if res:
                ip.name, ip.base, ip.desc = res
                self._rename_ip_refs(old, ip.name)
                self._mark_dirty()
                self._refresh_all()
                self.tree.selection_set(ip.name)
        else:
            dlg = RegisterDialog(self.root, reg, {r.name for r in ip.registers if r is not reg})
            if dlg.result:
                old = reg.name
                ip.registers[ip.registers.index(reg)] = dlg.result
                self._rename_reg_refs(ip.name, old, dlg.result.name)
                self._mark_dirty()
                self._refresh_all()
                self.tree.selection_set(f"{ip.name}/{dlg.result.name}")

    def _rename_ip_refs(self, old, new):
        self.values = {((new if i == old else i), r): v for (i, r), v in self.values.items()}
        self.watch = [((new if i == old else i), r) for i, r in self.watch]
        for row in self.config_rows:
            if row[0] == old:
                row[0] = new

    def _rename_reg_refs(self, ip_name, old, new):
        def ren(i, r):
            return (i, new) if (i == ip_name and r == old) else (i, r)
        self.values = {ren(*k): v for k, v in self.values.items()}
        self.watch = [ren(*k) for k in self.watch]
        for row in self.config_rows:
            if row[0] == ip_name and row[1] == old:
                row[1] = new

    def _delete_selected(self):
        ip, reg = self._selection()
        if not ip:
            return
        what = f"{ip.name}.{reg.name}" if reg else f"{ip.name} (tüm registerları ile)"
        if not messagebox.askyesno(APP_TITLE, f"{what} silinsin mi?"):
            return
        if reg:
            ip.registers.remove(reg)
            self.watch = [w for w in self.watch if w != (ip.name, reg.name)]
        else:
            self.project.ips.remove(ip)
            self.watch = [w for w in self.watch if w[0] != ip.name]
        self._mark_dirty()
        self._refresh_all()

    # ---------------------------------------------------------------- detail tab
    def _show_selection(self):
        for child in self.detail_tab.winfo_children():
            child.destroy()
        self._field_vars = {}
        ip, reg = self._selection()
        if ip is None:
            ttk.Label(self.detail_tab, text="Soldan bir IP veya register seçin.").pack(anchor="w")
        elif reg is None:
            self._show_ip(ip)
        else:
            self._show_register(ip, reg)

    def _show_ip(self, ip):
        t = self.detail_tab
        ttk.Label(t, text=ip.name, font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(t, text=f"Base: {hex32(ip.base)}").pack(anchor="w", pady=(2, 0))
        if ip.desc:
            ttk.Label(t, text=ip.desc, foreground="#444", wraplength=760, justify="left").pack(anchor="w", pady=(4, 0))
        ttk.Frame(t, height=8).pack()
        bar = ttk.Frame(t)
        bar.pack(anchor="w")
        ttk.Button(bar, text="Tüm okunabilir registerları oku",
                   command=lambda: self._read_many([(ip, r) for r in ip.registers if r.readable])
                   ).pack(side="left")
        ttk.Button(bar, text="Tümünü izlemeye ekle",
                   command=lambda: self._add_watch([(ip.name, r.name) for r in ip.registers if r.readable])
                   ).pack(side="left", padx=6)
        xb = ttk.Button(bar, text="Excel'e aktar…", command=lambda: self._export_xlsx(selected=True))
        xb.pack(side="left")
        if not regmap_xlsx.available():
            xb.state(["disabled"])
        cols = ("offset", "addr", "access", "value", "desc")
        tv = ttk.Treeview(t, columns=cols, show="headings", height=12)
        for c, h, w in zip(cols, ("Offset", "Adres", "Erişim", "Son değer", "Açıklama"),
                           (70, 100, 60, 100, 280)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="w" if c == "desc" else "center")
        for reg in sorted(ip.registers, key=lambda r: r.offset):
            v = self._value(ip, reg)
            tv.insert("", "end", values=(f"0x{reg.offset:02X}", hex32(ip.address(reg)), reg.access,
                                         hex32(v) if v is not None else "—", single_line(reg.desc)))
        tv.pack(fill="both", expand=True, pady=(10, 0))

    def _show_register(self, ip, reg):
        t = self.detail_tab
        ttk.Label(t, text=f"{ip.name}.{reg.name}", font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(t, text=f"Adres: {hex32(ip.address(reg))}   Erişim: {reg.access}   "
                          f"Reset: {hex32(reg.reset)}").pack(anchor="w", pady=(2, 0))
        if reg.desc:
            ttk.Label(t, text=reg.desc, foreground="#555", wraplength=760, justify="left").pack(anchor="w")

        row = ttk.Frame(t)
        row.pack(anchor="w", pady=10)
        ttk.Label(row, text="Değer").pack(side="left")
        current = self._value(ip, reg)
        self.reg_value = tk.StringVar(value=hex32(current if current is not None else reg.reset))
        entry = ttk.Entry(row, textvariable=self.reg_value, width=14, font=("Consolas", 11))
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self._write_register(ip, reg))
        rd = ttk.Button(row, text="Oku", command=lambda: self._read_many([(ip, reg)]))
        wr = ttk.Button(row, text="Yaz", command=lambda: self._write_register(ip, reg))
        rd.pack(side="left")
        wr.pack(side="left", padx=4)
        rd.state(["!disabled"] if reg.readable else ["disabled"])
        wr.state(["!disabled"] if reg.writable else ["disabled"])
        if reg.readable:
            ttk.Button(row, text="İzlemeye ekle",
                       command=lambda: self._add_watch([(ip.name, reg.name)])).pack(side="left", padx=(12, 0))
        self.reg_value_label = ttk.Label(row, text="", foreground="#555")
        self.reg_value_label.pack(side="left", padx=12)

        if not reg.fields:
            ttk.Label(t, text="Bu registerda tanımlı bit alanı yok. Düzenle ile ekleyebilirsiniz.",
                      foreground="#777").pack(anchor="w")
            return
        box = ttk.LabelFrame(t, text="Bit alanları", padding=8)
        box.pack(fill="x", anchor="w")
        value = current if current is not None else reg.reset
        self._meaning_labels = {}
        for c, h in ((0, "Alan"), (1, "Bitler / tip"), (2, "Değer"), (4, "Anlamı (okunan)"),
                     (5, "Hazır değer yaz")):
            ttk.Label(box, text=h, font=("TkDefaultFont", 9, "bold")).grid(row=0, column=c, sticky="w", padx=(0, 8))
        for r, f in enumerate(reg.fields, start=1):
            ttk.Label(box, text=f.name, width=14).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(box, text=f.bits + (f"  {f.kind}" if f.kind else ""), width=14,
                      foreground="#555").grid(row=r, column=1, sticky="w")
            field_writable = reg.writable and f.kind not in ("RO", "CONST")
            if f.width == 1:
                var = tk.BooleanVar(value=bool(f.extract(value)))
                if f.kind in ("W1C", "PULSE"):
                    # W1C: any click writes 1 (clear); PULSE: any click writes 1 (fire)
                    cmd = lambda f=f: self._write_field(ip, reg, f, 1)
                else:
                    cmd = lambda f=f, var=var: self._write_field(ip, reg, f, int(var.get()))
                cb = ttk.Checkbutton(box, variable=var, command=cmd)
                cb.grid(row=r, column=2, sticky="w")
                if not field_writable:
                    cb.state(["disabled"])
            else:
                var = tk.StringVar(value=hex(f.extract(value)))
                e = ttk.Entry(box, textvariable=var, width=12, font=("Consolas", 10))
                e.grid(row=r, column=2, sticky="w")
                if field_writable:
                    b = ttk.Button(box, text="Yaz", width=5,
                                   command=lambda f=f, var=var: self._write_field_text(ip, reg, f, var.get()))
                    b.grid(row=r, column=3, padx=4)
                    e.bind("<Return>", lambda ev, f=f, var=var: self._write_field_text(ip, reg, f, var.get()))
                else:
                    e.state(["readonly"])
            meaning = ttk.Label(box, text="", foreground="#1a5fb4", width=18)
            meaning.grid(row=r, column=4, sticky="w", padx=(8, 4))
            self._meaning_labels[f.name] = meaning
            choices = f.write_choices() if field_writable else []
            if choices:
                labels = [f"0x{e.value:X} → {single_line(e.write)}" for e in choices]
                cb_var = tk.StringVar()
                combo = ttk.Combobox(box, textvariable=cb_var, values=labels, state="readonly", width=20)
                combo.grid(row=r, column=5, sticky="w", padx=4)
                combo.bind("<<ComboboxSelected>>",
                           lambda ev, f=f, choices=choices, combo=combo: self._write_choice(ip, reg, f, choices, combo))
            self._field_vars[f.name] = var

        details = [(f, field_details(f, reg.access)) for f in reg.fields]
        details = [(f, d) for f, d in details if d]
        if details:
            dbox = ttk.LabelFrame(t, text="Alan açıklamaları ve değer anlamları", padding=6)
            dbox.pack(fill="both", expand=True, pady=(8, 0))
            txt = tk.Text(dbox, wrap="word", height=10, font=("TkDefaultFont", 9), relief="flat",
                          background=t.winfo_toplevel().cget("background"))
            sb = ttk.Scrollbar(dbox, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            txt.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            txt.tag_configure("head", font=("TkDefaultFont", 9, "bold"))
            txt.tag_configure("kind", foreground="#777")
            for f, d in details:
                txt.insert("end", f"{f.name} {f.bits}", "head")
                txt.insert("end", f"  {f.kind or reg.access}\n", "kind")
                txt.insert("end", "    " + d.replace("\n", "\n    ") + "\n\n")
            txt.configure(state="disabled")
        self._update_register_view(ip, reg)

    def _write_choice(self, ip, reg, f, choices, combo):
        value = choices[combo.current()].value
        combo.set("")
        self._write_field(ip, reg, f, value)

    def _update_register_view(self, ip, reg):
        """Refresh the register tab after a new value without rebuilding it."""
        sel_ip, sel_reg = self._selection()
        if sel_reg is not reg or sel_ip is not ip:
            return
        v = self._value(ip, reg)
        if v is None:
            return
        self.reg_value.set(hex32(v))
        self.reg_value_label.config(text=f"dec {v}   bin {v:032b}")
        for f in reg.fields:
            lbl = getattr(self, "_meaning_labels", {}).get(f.name)
            if lbl is not None and lbl.winfo_exists():
                fv = f.extract(v)
                text = single_line(f.read_meaning(fv)) if reg.readable else ""
                lbl.config(text=text or ("tanımsız değer" if any(e.read for e in f.values) else ""),
                           foreground="#1a5fb4" if text else "#999")
            var = self._field_vars.get(f.name)
            if isinstance(var, tk.BooleanVar):
                var.set(bool(f.extract(v)))
            elif var is not None:
                var.set(hex(f.extract(v)))

    def _set_value(self, ip, reg, value):
        self.values[(ip.name, reg.name)] = value & 0xFFFFFFFF
        self._update_register_view(ip, reg)
        self._update_live_row(ip.name, reg.name)

    # ---------------------------------------------------------------- bus actions
    def _read_many(self, items):
        items = [(ip, reg) for ip, reg in items if reg.readable]

        def job():
            return [(ip, reg, self.link.read(ip.address(reg))) for ip, reg in items]

        def done(result, error):
            if error:
                self._report_error(error)
                return
            for ip, reg, v in result:
                self._set_value(ip, reg, v)
            self._status(f"{len(result)} register okundu")
            ip_sel, reg_sel = self._selection()
            if ip_sel and reg_sel is None:
                self._show_selection()
        self._submit(job, done)

    def _write_register(self, ip, reg):
        try:
            value = parse_int(self.reg_value.get()) & 0xFFFFFFFF
        except ValueError:
            messagebox.showerror(APP_TITLE, "Geçersiz değer")
            return
        self._write_value(ip, reg, value)

    def _write_value(self, ip, reg, value, readback=True):
        def job():
            self.link.write(ip.address(reg), value)
            if readback and reg.readable:
                return self.link.read(ip.address(reg))
            return value

        def done(result, error):
            if error:
                self._report_error(error)
                return
            self._set_value(ip, reg, result)
            self._status(f"{ip.name}.{reg.name} ← {hex32(value)}")
        self._submit(job, done)

    def _write_field(self, ip, reg, f: Field, field_value):
        """Read-modify-write a field (RW) or update the shadow value (WO)."""
        def job():
            addr = ip.address(reg)
            if reg.access == "RW":
                base = self.link.read(addr)
            else:
                base = self.values.get((ip.name, reg.name), reg.reset)
            # Writing back a set W1C flag would clear it, a 1 in a PULSE field
            # would fire it again: other fields of these kinds are written as 0
            base &= ~reg.mask_of("W1C", "PULSE")
            new = f.insert(base, field_value)
            self.link.write(addr, new)
            return self.link.read(addr) if reg.readable else new

        def done(result, error):
            if error:
                self._report_error(error)
                self._update_register_view(ip, reg)
                return
            self._set_value(ip, reg, result)
            self._status(f"{ip.name}.{reg.name}.{f.name} ← {field_value}")
        if not self._submit(job, done):
            self._update_register_view(ip, reg)

    def _write_field_text(self, ip, reg, f, text):
        try:
            fv = parse_int(text)
        except ValueError:
            messagebox.showerror(APP_TITLE, "Geçersiz alan değeri")
            return
        if not 0 <= fv < (1 << f.width):
            messagebox.showerror(APP_TITLE, f"{f.name} {f.width} bit; değer 0..{(1 << f.width) - 1} olmalı")
            return
        self._write_field(ip, reg, f, fv)

    # ---------------------------------------------------------------- live tab
    def _build_live_tab(self):
        t = self.live_tab
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        self.poll_btn = ttk.Button(bar, text="▶ Başlat", command=self._toggle_polling)
        self.poll_btn.pack(side="left")
        ttk.Label(bar, text="Periyot (ms)").pack(side="left", padx=(12, 4))
        self.poll_ms = tk.StringVar(value="500")
        ttk.Spinbox(bar, from_=50, to=10000, increment=50, textvariable=self.poll_ms, width=7).pack(side="left")
        ttk.Button(bar, text="Seçiliyi kaldır", command=self._remove_watch).pack(side="right")
        ttk.Button(bar, text="Bir kez oku", command=self._poll_once).pack(side="right", padx=4)

        cols = ("ip", "reg", "addr", "value", "fields", "time")
        self.live = ttk.Treeview(t, columns=cols, show="headings")
        for c, h, w in zip(cols, ("IP", "Register", "Adres", "Değer", "Alanlar", "Zaman"),
                           (80, 100, 100, 100, 300, 70)):
            self.live.heading(c, text=h)
            self.live.column(c, width=w, anchor="w" if c == "fields" else "center")
        self.live.pack(fill="both", expand=True, pady=(8, 0))
        self.live.bind("<Double-1>", self._goto_watch)
        ttk.Label(t, foreground="#777",
                  text="Register sekmesinden ya da IP görünümünden izlemeye ekleyin. "
                       "Çift tıklama ile registera gidilir.").pack(anchor="w", pady=(4, 0))

    def _add_watch(self, items):
        for item in items:
            if item not in self.watch:
                self.watch.append(item)
        self._refresh_live()
        self.tabs.select(self.live_tab)

    def _remove_watch(self):
        for iid in self.live.selection():
            ip, reg = iid.split("/")
            self.watch = [w for w in self.watch if w != (ip, reg)]
        self._refresh_live()

    def _goto_watch(self, _event):
        sel = self.live.selection()
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
            self.tree.see(sel[0])
            self.tabs.select(self.detail_tab)

    def _refresh_live(self):
        self.watch = [(i, r) for i, r in self.watch
                      if self.project.get_ip(i) and self.project.get_ip(i).get_register(r)]
        self.live.delete(*self.live.get_children())
        for i, r in self.watch:
            ip = self.project.get_ip(i)
            reg = ip.get_register(r)
            self.live.insert("", "end", iid=f"{i}/{r}", values=(i, r, hex32(ip.address(reg)), "—", "", ""))
            self._update_live_row(i, r, stamp=False)

    def _update_live_row(self, ip_name, reg_name, stamp=True):
        iid = f"{ip_name}/{reg_name}"
        if not self.live.exists(iid):
            return
        ip = self.project.get_ip(ip_name)
        reg = ip.get_register(reg_name)
        v = self.values.get((ip_name, reg_name))
        if v is None:
            return
        fields = "  ".join(f"{f.name}={f.extract(v):X}" for f in reg.fields)
        self.live.item(iid, values=(ip_name, reg_name, hex32(ip.address(reg)), hex32(v), fields,
                                    time.strftime("%H:%M:%S") if stamp else self.live.set(iid, "time")))

    def _watch_items(self):
        out = []
        for i, r in self.watch:
            ip = self.project.get_ip(i)
            reg = ip.get_register(r) if ip else None
            if reg and reg.readable:
                out.append((ip, reg))
        return out

    def _poll_once(self):
        self._read_many(self._watch_items())

    def _toggle_polling(self):
        if self.polling:
            self._stop_polling()
        else:
            if not self.link.connected:
                self._status("Bağlı değil")
                return
            self.polling = True
            self.poll_btn.config(text="■ Durdur")
            self._poll_cycle()

    def _stop_polling(self):
        self.polling = False
        self.poll_btn.config(text="▶ Başlat")

    def _poll_cycle(self):
        if not self.polling:
            return
        items = self._watch_items()
        try:
            period = max(50, int(self.poll_ms.get()))
        except ValueError:
            period = 500

        def job():
            return [(ip, reg, self.link.read(ip.address(reg))) for ip, reg in items]

        def done(result, error):
            if error:
                self._report_error(error)
                self._stop_polling()
                return
            for ip, reg, v in result:
                self._set_value(ip, reg, v)
            if self.polling:
                self.root.after(period, self._poll_cycle)

        if not items:
            self.root.after(period, self._poll_cycle)
        elif not self._submit(job, done):
            self._stop_polling()

    # ---------------------------------------------------------------- config tab
    def _build_config_tab(self):
        t = self.config_tab
        ttk.Label(t, foreground="#555",
                  text="Config: cihaza sırayla yazılacak register değerleri. "
                       "Kaydedip daha sonra tekrar yükleyebilirsiniz.").pack(anchor="w")

        add = ttk.Frame(t)
        add.pack(fill="x", pady=8)
        self.cfg_ip = tk.StringVar()
        self.cfg_reg = tk.StringVar()
        self.cfg_val = tk.StringVar(value="0x00000000")
        ttk.Label(add, text="IP").pack(side="left")
        self.cfg_ip_box = ttk.Combobox(add, textvariable=self.cfg_ip, state="readonly", width=12)
        self.cfg_ip_box.pack(side="left", padx=4)
        self.cfg_ip_box.bind("<<ComboboxSelected>>", lambda e: self._refresh_config_regs())
        ttk.Label(add, text="Register").pack(side="left")
        self.cfg_reg_box = ttk.Combobox(add, textvariable=self.cfg_reg, state="readonly", width=14)
        self.cfg_reg_box.pack(side="left", padx=4)
        ttk.Label(add, text="Değer").pack(side="left")
        ttk.Entry(add, textvariable=self.cfg_val, width=12, font=("Consolas", 10)).pack(side="left", padx=4)
        ttk.Button(add, text="Ekle", command=self._config_add).pack(side="left")
        ttk.Button(add, text="Seçili IP'nin son değerlerini ekle",
                   command=self._config_from_values).pack(side="left", padx=8)

        cols = ("n", "ip", "reg", "addr", "value")
        self.cfg_tree = ttk.Treeview(t, columns=cols, show="headings", height=10)
        for c, h, w in zip(cols, ("#", "IP", "Register", "Adres", "Değer"), (40, 100, 120, 110, 110)):
            self.cfg_tree.heading(c, text=h)
            self.cfg_tree.column(c, width=w, anchor="center")
        self.cfg_tree.pack(fill="both", expand=True)
        self.cfg_tree.bind("<Double-1>", lambda e: self._config_edit_value())

        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(8, 0))
        for text, cmd in (("▲", lambda: self._config_move(-1)), ("▼", lambda: self._config_move(1)),
                          ("Değeri düzenle", self._config_edit_value), ("Sil", self._config_delete),
                          ("Temizle", self._config_clear)):
            ttk.Button(bar, text=text, command=cmd, width=len(text) + 2).pack(side="left", padx=(0, 4))
        self.cfg_verify = tk.BooleanVar(value=True)
        ttk.Button(bar, text="Cihaza gönder", command=self._config_apply).pack(side="right")
        ttk.Checkbutton(bar, text="RW registerları geri okuyup doğrula",
                        variable=self.cfg_verify).pack(side="right", padx=8)
        bar2 = ttk.Frame(t)
        bar2.pack(fill="x", pady=(6, 0))
        ttk.Button(bar2, text="Config yükle…", command=self._config_load).pack(side="left")
        ttk.Button(bar2, text="Config kaydet…", command=self._config_save).pack(side="left", padx=4)
        self._refresh_config_choices()

    def _refresh_config_choices(self):
        names = [ip.name for ip in self.project.ips]
        self.cfg_ip_box["values"] = names
        if self.cfg_ip.get() not in names:
            self.cfg_ip.set(names[0] if names else "")
        self._refresh_config_regs()

    def _refresh_config_regs(self):
        ip = self.project.get_ip(self.cfg_ip.get())
        regs = [r.name for r in sorted(ip.registers, key=lambda r: r.offset) if r.writable] if ip else []
        self.cfg_reg_box["values"] = regs
        if self.cfg_reg.get() not in regs:
            self.cfg_reg.set(regs[0] if regs else "")

    def _refresh_config_table(self):
        self.cfg_tree.delete(*self.cfg_tree.get_children())
        for n, (i, r, v) in enumerate(self.config_rows):
            ip = self.project.get_ip(i)
            reg = ip.get_register(r) if ip else None
            addr = hex32(ip.address(reg)) if reg else "TANIMSIZ"
            self.cfg_tree.insert("", "end", iid=str(n), values=(n + 1, i, r, addr, hex32(v)))

    def _config_add(self):
        if not self.cfg_ip.get() or not self.cfg_reg.get():
            return
        try:
            v = parse_int(self.cfg_val.get()) & 0xFFFFFFFF
        except ValueError:
            messagebox.showerror(APP_TITLE, "Geçersiz değer")
            return
        self.config_rows.append([self.cfg_ip.get(), self.cfg_reg.get(), v])
        self._refresh_config_table()

    def _config_from_values(self):
        ip = self.project.get_ip(self.cfg_ip.get())
        if not ip:
            return
        added = 0
        for reg in sorted(ip.registers, key=lambda r: r.offset):
            v = self.values.get((ip.name, reg.name))
            if reg.access == "RW" and v is not None:
                self.config_rows.append([ip.name, reg.name, v])
                added += 1
        self._refresh_config_table()
        self._status(f"{added} RW register eklendi" if added else
                     "Eklenecek değer yok: önce IP'nin registerlarını okuyun.")

    def _config_selected(self):
        sel = self.cfg_tree.selection()
        return int(sel[0]) if sel else None

    def _config_move(self, delta):
        i = self._config_selected()
        if i is None or not 0 <= i + delta < len(self.config_rows):
            return
        rows = self.config_rows
        rows[i], rows[i + delta] = rows[i + delta], rows[i]
        self._refresh_config_table()
        self.cfg_tree.selection_set(str(i + delta))

    def _config_edit_value(self):
        i = self._config_selected()
        if i is None:
            return
        row = self.config_rows[i]
        res = FormDialog(self.root, f"{row[0]}.{row[1]}", [("v", "Değer", hex32(row[2]), None)],
                         lambda v: _int_or_error(v["v"], "Değer") & 0xFFFFFFFF).result
        if res is not None:
            row[2] = res
            self._refresh_config_table()
            self.cfg_tree.selection_set(str(i))

    def _config_delete(self):
        i = self._config_selected()
        if i is not None:
            del self.config_rows[i]
            self._refresh_config_table()

    def _config_clear(self):
        if self.config_rows and messagebox.askyesno(APP_TITLE, "Config listesi temizlensin mi?"):
            self.config_rows.clear()
            self._refresh_config_table()

    def _config_load(self):
        path = filedialog.askopenfilename(filetypes=[("Config", "*.axcfg.json *.json"), ("Tümü", "*.*")])
        if not path:
            return
        try:
            rows = load_config(path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Config açılamadı", str(exc))
            return
        self.config_rows = [list(r) for r in rows]
        self._refresh_config_table()
        missing = [f"{i}.{r}" for i, r, _ in rows
                   if not (self.project.get_ip(i) and self.project.get_ip(i).get_register(r))]
        if missing:
            messagebox.showwarning(APP_TITLE, "Projede tanımlı olmayan registerlar:\n" + "\n".join(missing))
        self._status(f"Config yüklendi: {os.path.basename(path)} ({len(rows)} yazma)")

    def _config_save(self):
        if not self.config_rows:
            messagebox.showinfo(APP_TITLE, "Config listesi boş.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".axcfg.json",
                                            filetypes=[("Config", "*.axcfg.json")])
        if path:
            try:
                save_config(path, self.config_rows)
            except OSError as exc:
                messagebox.showerror("Kaydedilemedi", str(exc))
                return
            self._status(f"Config kaydedildi: {path}")

    def _config_apply(self):
        steps = []
        for i, r, v in self.config_rows:
            ip = self.project.get_ip(i)
            reg = ip.get_register(r) if ip else None
            if reg is None:
                messagebox.showerror(APP_TITLE, f"{i}.{r} projede tanımlı değil.")
                return
            if not reg.writable:
                messagebox.showerror(APP_TITLE, f"{i}.{r} salt okunur (RO).")
                return
            steps.append((ip, reg, v))
        if not steps:
            return
        verify = self.cfg_verify.get()

        def job():
            results, mismatches = [], []
            for ip, reg, v in steps:
                self.link.write(ip.address(reg), v)
                if verify and reg.access == "RW":
                    rb = self.link.read(ip.address(reg))
                    results.append((ip, reg, rb))
                    if rb != v:
                        mismatches.append(f"{ip.name}.{reg.name}: yazılan {hex32(v)}, okunan {hex32(rb)}")
                else:
                    results.append((ip, reg, v))
            return results, mismatches

        def done(result, error):
            if error:
                self._report_error(error)
                messagebox.showerror(APP_TITLE, f"Config gönderilemedi:\n{error}")
                return
            results, mismatches = result
            for ip, reg, v in results:
                self._set_value(ip, reg, v)
            if mismatches:
                messagebox.showwarning(APP_TITLE, "Doğrulama farkları:\n" + "\n".join(mismatches))
            self._status(f"Config gönderildi: {len(results)} yazma"
                         + (f", {len(mismatches)} fark" if mismatches else ", doğrulandı" if verify else ""))
        self._submit(job, done)


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
