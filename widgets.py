"""Tk widgets shared by the debug application and the wrapper generator."""

import tkinter as tk
from tkinter import messagebox, ttk

from model import KIND_READS, KIND_WRITES, EnumValue, parse_int


class TextField(ttk.Frame):
    """Multi-line text entry with get()/set()."""

    def __init__(self, parent, text="", height=3, width=40):
        super().__init__(parent)
        self.text = tk.Text(self, height=height, width=width, wrap="word", font=("TkDefaultFont", 9),
                            relief="solid", borderwidth=1, undo=True)
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.set(text)

    def get(self):
        return self.text.get("1.0", "end").strip()

    def set(self, text):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text or "")

    def focus_set(self):
        self.text.focus_set()


def return_is_ok(event):
    """<Return> confirms a dialog unless the cursor is in a multi-line text box."""
    return not isinstance(event.widget, tk.Text)


class _ValueDialog(tk.Toplevel):
    def __init__(self, parent, e: EnumValue, width, reads, writes):
        super().__init__(parent)
        self.title("Değer anlamı")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self._width = width
        b = ttk.Frame(self, padding=10)
        b.pack(fill="both", expand=True)
        self.v_val = tk.StringVar(value=f"0x{e.value:X}")
        ttk.Label(b, text="Değer").grid(row=0, column=0, sticky="w", pady=3)
        ent = ttk.Entry(b, textvariable=self.v_val, width=14)
        ent.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(b, text=f"0..{(1 << width) - 1} (hex/dec/bin)", foreground="#777").grid(row=0, column=2, padx=6)
        ttk.Label(b, text="Okunursa anlamı").grid(row=1, column=0, sticky="nw", pady=3)
        self.t_read = TextField(b, e.read, height=2, width=44)
        self.t_read.grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(b, text="Yazılırsa etkisi").grid(row=2, column=0, sticky="nw", pady=3)
        self.t_write = TextField(b, e.write, height=2, width=44)
        self.t_write.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        if not reads:
            self.t_read.text.configure(state="disabled", background="#eeeeee")
        if not writes:
            self.t_write.text.configure(state="disabled", background="#eeeeee")
        bar = ttk.Frame(b)
        bar.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(bar, text="İptal", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Tamam", command=self._ok).pack(side="right", padx=5)
        self.bind("<Return>", lambda ev: self._ok() if return_is_ok(ev) else None)
        self.bind("<Escape>", lambda ev: self.destroy())
        ent.focus_set()
        ent.select_range(0, "end")
        self.grab_set()
        self.wait_window()

    def _ok(self):
        try:
            v = parse_int(self.v_val.get())
        except ValueError:
            messagebox.showerror("Geçersiz değer", "Değer bir sayı olmalı", parent=self)
            return
        if not 0 <= v < (1 << self._width):
            messagebox.showerror("Geçersiz değer", f"Değer {self._width} bite sığmıyor", parent=self)
            return
        self.result = EnumValue(v, self.t_read.get(), self.t_write.get())
        self.destroy()


class ValuesEditor(ttk.LabelFrame):
    """Table of field values with their read meaning and write effect."""

    def __init__(self, parent, values, kind="", width_getter=lambda: 32, height=5):
        super().__init__(parent, text="Değer anlamları (isteğe bağlı)", padding=6)
        self._values = [EnumValue(e.value, e.read, e.write) for e in values]
        self._kind = kind
        self._width = width_getter
        self.tree = ttk.Treeview(self, columns=("read", "write"), height=height)
        self.tree.heading("#0", text="Değer")
        self.tree.heading("read", text="Okunursa anlamı")
        self.tree.heading("write", text="Yazılırsa etkisi")
        self.tree.column("#0", width=70, anchor="center")
        self.tree.column("read", width=200)
        self.tree.column("write", width=200)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._edit())
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(4, 0))
        ttk.Button(bar, text="Ekle", command=self._add).pack(side="left")
        ttk.Button(bar, text="Düzenle", command=self._edit).pack(side="left", padx=3)
        ttk.Button(bar, text="Sil", command=self._delete).pack(side="left")
        self.hint = ttk.Label(bar, text="", foreground="#777")
        self.hint.pack(side="left", padx=8)
        self.set_kind(kind)

    @property
    def reads(self):
        return KIND_READS.get(self._kind, True)

    @property
    def writes(self):
        return KIND_WRITES.get(self._kind, True)

    def set_kind(self, kind):
        self._kind = kind
        if not self.writes:
            self.hint.config(text="Bu tipte sadece okuma anlamı kullanılır")
        elif not self.reads:
            self.hint.config(text="Bu tipte sadece yazma etkisi kullanılır")
        else:
            self.hint.config(text="")
        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        self._values.sort(key=lambda e: e.value)
        for i, e in enumerate(self._values):
            self.tree.insert("", "end", iid=str(i), text=f"0x{e.value:X}",
                             values=(e.read.replace("\n", " ") if self.reads else "—",
                                     e.write.replace("\n", " ") if self.writes else "—"))

    def _selected(self):
        sel = self.tree.selection()
        return self._values[int(sel[0])] if sel else None

    def _store(self, new, old=None):
        if any(e.value == new.value and e is not old for e in self._values):
            messagebox.showerror("Değer anlamı", f"0x{new.value:X} zaten tanımlı", parent=self)
            return
        if old is None:
            self._values.append(new)
        else:
            self._values[self._values.index(old)] = new
        self._refresh()

    def _add(self):
        used = {e.value for e in self._values}
        nxt = next((v for v in range(1 << min(self._width(), 16)) if v not in used), 0)
        new = _ValueDialog(self.winfo_toplevel(), EnumValue(nxt), self._width(), self.reads, self.writes).result
        if new:
            self._store(new)

    def _edit(self):
        e = self._selected()
        if e:
            new = _ValueDialog(self.winfo_toplevel(), e, self._width(), self.reads, self.writes).result
            if new:
                self._store(new, e)

    def _delete(self):
        e = self._selected()
        if e:
            self._values.remove(e)
            self._refresh()

    def get(self):
        """Values for the current kind (meanings that do not apply are dropped)."""
        out = []
        for e in self._values:
            read = e.read if self.reads else ""
            write = e.write if self.writes else ""
            if read or write:
                out.append(EnumValue(e.value, read, write))
        return out


def field_details(f, reg_access="RW"):
    """Multi-line human readable description of a field and its values."""
    lines = []
    if f.desc:
        lines.append(f.desc)
    reads = KIND_READS.get(f.kind, reg_access != "WO")
    writes = KIND_WRITES.get(f.kind, reg_access != "RO")
    rd = [e for e in f.values if e.read and reads]
    wr = [e for e in f.values if e.write and writes]
    if rd:
        lines.append("Okunursa:")
        lines += [f"   0x{e.value:X} → {e.read}" for e in rd]
    if wr:
        lines.append("Yazılırsa:")
        lines += [f"   0x{e.value:X} → {e.write}" for e in wr]
    return "\n".join(lines)
