"""
Serial terminal tab for UART IP blocks behind the hexbus bridge
(ZipCPU axiluart / axiluart_phy register layout).

TX : bytes are written to TXREG while the TX FIFO reports free space.
RX : RXREG is read until its EMPTY bit is set, on a periodic poll.
"""

import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from hexbus import HexbusError

# Default axiluart offsets, used when the IP has no register with these names
DEFAULT_OFFSETS = {"FIFO": 0x04, "RXREG": 0x08, "TXREG": 0x0C}

RX_EMPTY, RX_PERR, RX_FERR, RX_BREAK, RX_OVFL = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
RX_BYTE_PERR, RX_BYTE_FERR = 1 << 13, 1 << 14     # axiluart_v2: flags of this byte
TX_FREE_SHIFT, TX_FREE_MASK = 18, 0x3FF

TX_ENDINGS = {"CR+LF": b"\r\n", "LF": b"\n", "CR": b"\r"}
RX_NEWLINE = ("Otomatik", "LF", "CR", "CR+LF")
MAX_RX_PER_POLL = 256
MAX_TX_CHUNK = 64


def parse_hex_bytes(text):
    """'41 42 0x43,44' -> b'ABCD'. Raises ValueError on malformed input."""
    tokens = [t for t in re.split(r"[\s,;:]+", text.strip()) if t]
    out = bytearray()
    for tok in tokens:
        tok = tok[2:] if tok.lower().startswith("0x") else tok
        if not re.fullmatch(r"[0-9a-fA-F]+", tok):
            raise ValueError(f"Geçersiz hex: {tok}")
        if len(tok) % 2:
            tok = "0" + tok
        out += bytes.fromhex(tok)
    return bytes(out)


def parse_ascii(text, escapes):
    if not escapes:
        return text.encode("latin-1", errors="replace")
    out = bytearray()
    i = 0
    simple = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, "e": 27}
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n in simple:
                out.append(simple[n])
                i += 2
                continue
            if n == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", text[i + 2:i + 4]):
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
        out += c.encode("latin-1", errors="replace")
        i += 1
    return bytes(out)


class TerminalTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.rx_running = False
        self.rx_busy = False
        self.tx_busy = False
        self.tx_queue = bytearray()
        self.rx_count = self.tx_count = self.err_count = 0
        self.history, self.hist_pos = [], 0
        self._last_was_newline = True
        self._last_ovfl = False
        self._prev_cr = {"rx": False, "tx": False}
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="UART IP").pack(side="left")
        self.ip_var = tk.StringVar()
        self.ip_box = ttk.Combobox(top, textvariable=self.ip_var, state="readonly", width=14)
        self.ip_box.pack(side="left", padx=4)
        self.rx_btn = ttk.Button(top, text="▶ Alımı başlat", command=self.toggle_rx)
        self.rx_btn.pack(side="left", padx=(8, 4))
        ttk.Label(top, text="ms").pack(side="left", padx=(8, 2))
        self.poll_ms = tk.StringVar(value="50")
        ttk.Spinbox(top, from_=10, to=2000, increment=10, textvariable=self.poll_ms, width=6).pack(side="left")
        self.log_poll = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Logla", variable=self.log_poll).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="RX reset", command=self.rx_reset).pack(side="right")
        ttk.Button(top, text="Bayrak sil", command=self.clear_flags).pack(side="right", padx=4)

        view = ttk.Frame(self)
        view.pack(fill="x", pady=(8, 4))
        ttk.Label(view, text="Gösterim").pack(side="left")
        self.view_mode = tk.StringVar(value="ASCII")
        for m in ("ASCII", "HEX", "ASCII+HEX"):
            ttk.Radiobutton(view, text=m, value=m, variable=self.view_mode).pack(side="left", padx=3)
        self.show_time = tk.BooleanVar(value=False)
        self.show_tx = tk.BooleanVar(value=True)
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(view, text="Zaman", variable=self.show_time).pack(side="left", padx=(12, 3))
        ttk.Checkbutton(view, text="TX göster", variable=self.show_tx).pack(side="left", padx=3)
        ttk.Checkbutton(view, text="Kaydır", variable=self.autoscroll).pack(side="left", padx=3)
        ttk.Button(view, text="Kaydet…", command=self.save_log).pack(side="right")
        ttk.Button(view, text="Temizle", command=self.clear).pack(side="right", padx=4)

        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        self.text = tk.Text(box, font=("Consolas", 10), wrap="char", height=14,
                            background="#101418", foreground="#d8dee9", insertbackground="#d8dee9")
        sb = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.tag_configure("rx", foreground="#a3be8c")
        self.text.tag_configure("tx", foreground="#88c0d0")
        self.text.tag_configure("ctl", foreground="#d08770")
        self.text.tag_configure("err", foreground="#bf616a", font=("Consolas", 10, "bold"))
        self.text.tag_configure("time", foreground="#6b7280")
        self.text.tag_configure("bad", foreground="#ffffff", background="#bf616a")
        self.text.bind("<Key>", lambda e: "break" if e.keysym not in ("c", "C", "a", "A") else None)

        send = ttk.Frame(self)
        send.pack(fill="x", pady=(8, 0))
        self.send_mode = tk.StringVar(value="ASCII")
        ttk.Combobox(send, textvariable=self.send_mode, values=("ASCII", "HEX"),
                     state="readonly", width=7).pack(side="left")
        self.entry_var = tk.StringVar()
        self.entry = ttk.Entry(send, textvariable=self.entry_var, font=("Consolas", 10))
        self.entry.pack(side="left", fill="x", expand=True, padx=4)
        self.entry.bind("<Return>", lambda e: self.send())
        self.entry.bind("<Up>", lambda e: self._history(-1))
        self.entry.bind("<Down>", lambda e: self._history(1))
        self.add_eol = tk.BooleanVar(value=True)
        ttk.Checkbutton(send, text="Sonuna ekle", variable=self.add_eol).pack(side="left")
        self.eol = tk.StringVar(value="CR+LF")
        ttk.Combobox(send, textvariable=self.eol, values=tuple(TX_ENDINGS),
                     state="readonly", width=6).pack(side="left", padx=(2, 0))
        ttk.Button(send, text="Gönder", command=self.send).pack(side="left", padx=(4, 0))

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(4, 0))
        self.escapes = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Kaçış (\\n \\xNN)", variable=self.escapes).pack(side="left")
        ttk.Label(opts, text="RX satır sonu").pack(side="left", padx=(10, 2))
        self.rx_nl = tk.StringVar(value="Otomatik")
        ttk.Combobox(opts, textvariable=self.rx_nl, values=RX_NEWLINE,
                     state="readonly", width=9).pack(side="left")
        self.stats = ttk.Label(opts, text="", foreground="#555")
        self.stats.pack(side="left", padx=6)
        ttk.Button(opts, text="Dosya gönder…", command=self.send_file).pack(side="right")
        self._update_stats()

    def refresh_ips(self):
        names = [ip.name for ip in self.app.project.ips
                 if ip.get_register("TXREG") or ip.get_register("RXREG")]
        names = names or [ip.name for ip in self.app.project.ips]
        self.ip_box["values"] = names
        if self.ip_var.get() not in names:
            self.ip_var.set(names[0] if names else "")

    # ------------------------------------------------------------------ helpers
    def _addr(self, name):
        ip = self.app.project.get_ip(self.ip_var.get())
        if ip is None:
            raise HexbusError("UART IP seçilmedi")
        reg = ip.get_register(name)
        return ip.base + (reg.offset if reg else DEFAULT_OFFSETS[name])

    def _update_stats(self):
        self.stats.config(text=f"RX {self.rx_count}  TX {self.tx_count}  "
                               f"Hata {self.err_count}  Kuyruk {len(self.tx_queue)}")

    def _history(self, step):
        if not self.history:
            return "break"
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.entry_var.set(self.history[self.hist_pos] if self.hist_pos < len(self.history) else "")
        self.entry.icursor("end")
        return "break"

    def _newline_action(self, b, tag):
        """Return 'nl' to break the line, 'skip' to hide the byte, None to show it."""
        mode = self.rx_nl.get()
        prev_cr = self._prev_cr[tag]
        self._prev_cr[tag] = b == 13
        if mode == "Otomatik":            # CR, LF and CR+LF each end one line
            if b == 13:
                return "nl"
            if b == 10:
                return "skip" if prev_cr else "nl"
        elif mode == "LF":
            if b == 10:
                return "nl"
            if b == 13:
                return "skip"
        elif mode == "CR":
            if b == 13:
                return "nl"
            if b == 10:
                return "skip"
        elif mode == "CR+LF":
            if b == 13:
                return "skip"
            if b == 10 and prev_cr:
                return "nl"
        return None

    def _render(self, data: bytes, tag, bad=frozenset()):
        """Render bytes; indices in `bad` were received with parity/frame errors."""
        mode = self.view_mode.get()
        t = self.text
        if self.show_time.get() and (self._last_was_newline or mode != "ASCII"):
            t.insert("end", time.strftime("[%H:%M:%S] "), "time")
        if mode == "HEX":
            for i, b in enumerate(data):
                t.insert("end", f"{b:02X}", "bad" if i in bad else tag)
                t.insert("end", " ", tag)
            if tag == "tx":
                t.insert("end", "\n")
            self._last_was_newline = tag == "tx"
        elif mode == "ASCII+HEX":
            for i in range(0, len(data), 16):
                chunk = data[i:i + 16]
                t.insert("end", f"{'TX' if tag == 'tx' else 'RX'}  ", tag)
                for j, b in enumerate(chunk):
                    t.insert("end", f"{b:02X}", "bad" if i + j in bad else tag)
                    t.insert("end", " ", tag)
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                t.insert("end", "   " * (16 - len(chunk)) + f" |{asc}|\n", tag)
            self._last_was_newline = True
        else:
            for i, b in enumerate(data):
                if i in bad:
                    shown = chr(b) if 32 <= b < 127 else f"<{b:02X}>"
                    t.insert("end", shown, "bad")
                    self._prev_cr[tag] = False
                    self._last_was_newline = False
                    continue
                if (self.rx_nl.get() == "CR+LF" and self._prev_cr[tag] and b != 10):
                    t.insert("end", "<0D>", "ctl")      # lone CR in CR+LF mode
                action = self._newline_action(b, tag)
                if action == "nl":
                    t.insert("end", "\n", tag)
                    self._last_was_newline = True
                    if self.show_time.get() and i + 1 < len(data):
                        t.insert("end", time.strftime("[%H:%M:%S] "), "time")
                    continue
                if action == "skip":
                    continue
                if 32 <= b < 127 or b >= 160:
                    t.insert("end", chr(b), tag)
                elif b == 9:
                    t.insert("end", "\t", tag)
                else:
                    t.insert("end", f"<{b:02X}>", "ctl")
                self._last_was_newline = False
        if int(t.index("end-1c").split(".")[0]) > 5000:
            t.delete("1.0", "1000.0")
        if self.autoscroll.get():
            t.see("end")

    def _mark(self, text):
        if not self._last_was_newline:
            self.text.insert("end", "\n")
        self.text.insert("end", f"[{text}]\n", "err")
        self._last_was_newline = True
        if self.autoscroll.get():
            self.text.see("end")

    # ------------------------------------------------------------------ TX
    def send(self):
        raw = self.entry_var.get()
        try:
            if self.send_mode.get() == "HEX":
                data = parse_hex_bytes(raw)
            else:
                data = parse_ascii(raw, self.escapes.get())
                if self.add_eol.get():
                    data += TX_ENDINGS[self.eol.get()]
        except ValueError as exc:
            messagebox.showerror("Gönderilemedi", str(exc))
            return
        if not data:
            return
        if raw and (not self.history or self.history[-1] != raw):
            self.history.append(raw)
        self.hist_pos = len(self.history)
        self.entry_var.set("")
        self.queue_tx(data)

    def send_file(self):
        path = filedialog.askopenfilename()
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            messagebox.showerror("Dosya okunamadı", str(exc))
            return
        if len(data) > 64 * 1024 and not messagebox.askyesno(
                "Büyük dosya", f"{len(data)} byte gönderilecek, bu uzun sürebilir. Devam?"):
            return
        self.queue_tx(data)

    def queue_tx(self, data):
        if not self.app.link.connected:
            self.app._status("Bağlı değil")
            return
        self.tx_queue += data
        if self.show_tx.get():
            self._render(data, "tx")
        self._update_stats()
        self._pump_tx()

    def _pump_tx(self):
        if self.tx_busy or not self.tx_queue:
            return
        try:
            fifo_addr, tx_addr = self._addr("FIFO"), self._addr("TXREG")
        except HexbusError as exc:
            self.app._report_error(exc)
            self.tx_queue.clear()
            return
        chunk = bytes(self.tx_queue[:MAX_TX_CHUNK])
        link = self.app.link

        def job():
            free = (link.read(fifo_addr) >> TX_FREE_SHIFT) & TX_FREE_MASK
            sent = 0
            for b in chunk[:free]:
                link.write(tx_addr, b)
                sent += 1
            return sent

        def done(sent, error):
            self.tx_busy = False
            if error:
                self.tx_queue.clear()
                self.err_count += 1
                self._mark(f"TX hata: {error}")
                self.app._report_error(error)
            else:
                del self.tx_queue[:sent]
                self.tx_count += sent
                if self.tx_queue:
                    self.after(1 if sent else 20, self._pump_tx)
            self._update_stats()

        self.tx_busy = True
        if not self.app._submit(job, done):
            self.tx_busy = False
            self.tx_queue.clear()
            self._update_stats()

    # ------------------------------------------------------------------ RX
    def toggle_rx(self):
        if self.rx_running:
            self.stop_rx()
        else:
            if not self.app.link.connected:
                self.app._status("Bağlı değil")
                return
            self.rx_running = True
            self.rx_btn.config(text="■ Alımı durdur")
            self._poll()

    def stop_rx(self):
        self.rx_running = False
        self.rx_btn.config(text="▶ Alımı başlat")

    def _poll(self):
        if not self.rx_running or self.rx_busy:
            return
        try:
            rx_addr = self._addr("RXREG")
            period = max(10, int(self.poll_ms.get()))
        except (HexbusError, ValueError) as exc:
            self.app._report_error(exc)
            self.stop_rx()
            return
        link = self.app.link
        quiet = not self.log_poll.get()

        def job():
            data, bad, flags = bytearray(), set(), 0
            link.quiet = quiet
            try:
                for _ in range(MAX_RX_PER_POLL):
                    v = link.read(rx_addr)
                    flags |= v & (RX_PERR | RX_FERR | RX_BREAK | RX_OVFL)
                    if v & RX_EMPTY:
                        break
                    if v & (RX_BYTE_PERR | RX_BYTE_FERR):
                        bad.add(len(data))
                        flags |= v & (RX_BYTE_PERR | RX_BYTE_FERR)
                    data.append(v & 0xFF)
            finally:
                link.quiet = False
            return bytes(data), bad, flags

        def done(result, error):
            self.rx_busy = False
            if error:
                self.err_count += 1
                self._mark(f"RX hata: {error}")
                self.app._report_error(error)
                self.stop_rx()
                self._update_stats()
                return
            data, bad, flags = result
            if data:
                self.rx_count += len(data)
                self._render(data, "rx", bad)
            self._handle_flags(flags, rx_addr)
            self._update_stats()
            if self.rx_running:
                self.after(1 if len(data) >= MAX_RX_PER_POLL else period, self._poll)

        self.rx_busy = True
        if not self.app._submit(job, done):
            self.rx_busy = False
            self.stop_rx()

    def _handle_flags(self, flags, rx_addr):
        per_byte = flags & (RX_BYTE_PERR | RX_BYTE_FERR)
        if per_byte:
            # Erroneous bytes are already highlighted; summarize once
            names = [n for bit, n in ((RX_BYTE_PERR, "PARITY"), (RX_BYTE_FERR, "FRAME"))
                     if flags & bit]
            names = ["hatalı byte(lar): " + ", ".join(names)]
        else:
            # Older RTL without per-byte bits: only the sticky summary exists
            names = [n for bit, n in ((RX_PERR, "PARITY"), (RX_FERR, "FRAME")) if flags & bit]
        if flags & RX_BREAK:
            names.append("BREAK")
        ovfl = bool(flags & RX_OVFL)
        if ovfl and not self._last_ovfl:
            names.append("OVERFLOW: veri kaybı, RX reset gerekir")
        self._last_ovfl = ovfl
        if names:
            self.err_count += 1
            self._mark("RX " + ", ".join(names))
        if flags & (RX_PERR | RX_FERR):
            self.app._submit(lambda: self.app.link.write(rx_addr, RX_PERR | RX_FERR))

    def clear_flags(self):
        try:
            rx_addr = self._addr("RXREG")
        except HexbusError as exc:
            self.app._report_error(exc)
            return
        self.app._submit(lambda: self.app.link.write(rx_addr, RX_PERR | RX_FERR))

    def rx_reset(self):
        try:
            rx_addr = self._addr("RXREG")
        except HexbusError as exc:
            self.app._report_error(exc)
            return
        self._last_ovfl = False
        self.app._submit(lambda: self.app.link.write(rx_addr, RX_OVFL))
        self._mark("RX reset")

    # ------------------------------------------------------------------ misc
    def clear(self):
        self.text.delete("1.0", "end")
        self.rx_count = self.tx_count = self.err_count = 0
        self._last_was_newline = True
        self._update_stats()

    def save_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Metin", "*.txt"), ("Tümü", "*.*")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.text.get("1.0", "end-1c"))
            except OSError as exc:
                messagebox.showerror("Kaydedilemedi", str(exc))

    def on_disconnect(self):
        self.stop_rx()
        self.tx_queue.clear()
        self._update_stats()
