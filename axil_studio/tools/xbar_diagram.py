"""Block diagram of the interconnect: masters on the left, the crossbar in the middle, slaves on
the right. Crossbar pins come from the generated wrapper header (xbargen.xbar_ports), so names,
widths and directions are exactly those of the HDL for the chosen port style. Every master and
slave block shows its own side of each connection (M_AXI_* / S_AXI_*, or the packed
MASTER_AXI_* / SLAVE_AXI_* ports of the HDL Designer blocks). Ctrl + wheel or the page's
buttons zoom; "fit" scales the diagram to the available width."""

import re

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from axil_studio.core import xbargen as X
from axil_studio import theme
from axil_studio.dialogs import size_text
from axil_studio.core.model import hex32

IN_COLOR = "#3B82F6"     # into the crossbar
OUT_COLOR = "#D99A00"    # out of the crossbar
PIN_H = 20
HEAD = 46                # block title area above the first pin
GAP = 16
WIRE = 70
MARGIN = 24
TOP = 92
ZOOM_MIN, ZOOM_MAX = 0.3, 3.0


def block_pin_name(xbar_name):
    """Name of the same connection on the master / slave side."""
    packed = re.match(r"^(MASTER|SLAVE)\d+_AXI_(REQ|RSP)$", xbar_name)
    if packed:
        return f"{packed.group(1)}_AXI_{packed.group(2)}"
    m = re.match(r"^(MASTER|SLAVE)\d+_AXI_(.+)$", xbar_name)
    if m:
        return ("M_AXI_" if m.group(1) == "MASTER" else "S_AXI_") + m.group(2)
    return xbar_name


class BlockDiagram(QWidget):
    masterClicked = Signal(int)
    slaveClicked = Signal(int)
    masterActivated = Signal(int)
    slaveActivated = Signal(int)
    zoomChanged = Signal(float)

    def __init__(self):
        super().__init__()
        self.spec = None
        self.selected = None          # ("m", i) | ("s", j)
        self.all_signals = False
        self.bad = set()
        self.error = None
        self.zoom = None              # None: fit to width
        self._scale = 1.0
        self._hits = []
        self._size = QSize(900, 400)
        self.mono = theme.mono_font()
        self.scroll = None            # the QScrollArea around the diagram (set by the page)
        self._pan = None
        self.setMouseTracking(True)

    # ------------------------------------------------------------ data
    def set_spec(self, spec, selected=None, bad=()):
        self.spec, self.selected, self.bad = spec, selected, set(bad)
        try:
            self._build()
            self.error = None
        except Exception as exc:  # noqa: BLE001 - the page must keep working; show why instead
            self.error = f"Blok diyagramı çizilemedi: {exc}"
        self.update()

    def set_all_signals(self, on):
        self.all_signals = on
        self.set_spec(self.spec, self.selected, self.bad)

    # ------------------------------------------------------------ zoom
    def set_zoom(self, z):
        self.zoom = None if z is None else max(ZOOM_MIN, min(ZOOM_MAX, z))
        self._fit()
        self.update()
        self.zoomChanged.emit(self._scale)

    def zoom_by(self, factor):
        self.set_zoom(self._scale * factor)

    def _fit(self):
        if self.zoom is None:
            avail = self.parentWidget().width() if self.parentWidget() else self.width()
            self._scale = min(1.0, max(ZOOM_MIN, avail / max(1, self._size.width())))
            self.setMinimumSize(0, int(self._size.height() * self._scale))
        else:
            self._scale = self.zoom
            self.setMinimumSize(int(self._size.width() * self._scale), int(self._size.height() * self._scale))

    def resizeEvent(self, ev):
        if self.zoom is None:
            old = self._scale
            self._fit()
            if abs(old - self._scale) > 1e-3:
                self.zoomChanged.emit(self._scale)
        super().resizeEvent(ev)

    def zoom_at(self, factor, pos):
        """Zoom keeping the diagram point under the mouse in place."""
        old = self._scale
        self.set_zoom(old * factor)
        sa = self.scroll
        if sa is None or abs(self._scale - old) < 1e-6:
            return
        new = self._scale
        h, v = sa.horizontalScrollBar(), sa.verticalScrollBar()
        vx, vy = pos.x() - h.value(), pos.y() - v.value()

        def keep():
            h.setValue(int(pos.x() * new / old - vx))
            v.setValue(int(pos.y() * new / old - vy))
        QTimer.singleShot(0, keep)

    def wheelEvent(self, ev):
        """Mouse wheel zooms around the cursor (Shift + wheel scrolls)."""
        if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            ev.ignore()
            return
        dy = ev.angleDelta().y() or ev.pixelDelta().y()
        if dy:
            self.zoom_at(1.0015 ** dy if abs(dy) < 60 else (1.15 if dy > 0 else 1 / 1.15), ev.position())
        ev.accept()

    def event(self, ev):
        # trackpad pinch (macOS)
        if ev.type() == QEvent.Type.NativeGesture and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_at(1 + ev.value(), ev.position())
            return True
        return super().event(ev)

    def sizeHint(self):
        return QSize(int(self._size.width() * self._scale), int(self._size.height() * self._scale))

    # ------------------------------------------------------------ layout
    def _pins(self, pins, side_prefix):
        """[(dir, range, xbar_label, block_label, members)]; the signals style is bundled per
        direction unless all signals are shown."""
        if self.spec.style == "signals" and not self.all_signals:
            short = "M" if side_prefix.startswith("MASTER") else "S"
            out = []
            for d in ("input", "output"):
                grp = [p for p in pins if p[0] == d]
                if grp:
                    xb = "giriş" if d == "input" else "çıkış"
                    own = "çıkış" if d == "input" else "giriş"     # the block's own direction
                    out.append((d, f"({len(grp)} {xb})", f"{side_prefix}_AXI_*",
                                f"{short}_AXI_* ({len(grp)} {own})", grp))
            return out
        return [(d, w, n, f"{block_pin_name(n)} {w}".strip(), None) for d, w, n in pins]

    def _build(self):
        sp = self.spec
        if sp is None:
            return
        ports = X.xbar_ports(sp)
        self.clock = [p for p in ports if p[2] in ("ACLK", "ARESETN")]
        self.left = {i: self._pins([p for p in ports if p[2].startswith(f"MASTER{i}_")], f"MASTER{i}")
                     for i in range(sp.nm)}
        self.right = {j: self._pins([p for p in ports if p[2].startswith(f"SLAVE{j}_")], f"SLAVE{j}")
                      for j in range(sp.ns)}

        mono = theme.mono_font()
        mono.setPixelSize(11)
        bold = QFont(self.font())
        bold.setBold(True)
        mfm, bfm = QFontMetrics(mono), QFontMetrics(bold)
        self.mono = mono

        def xlabel(p):
            return f"{p[2]} {p[1]}".strip()
        lw = max([mfm.horizontalAdvance(xlabel(p)) for g in self.left.values() for p in g] + [80])
        rw = max([mfm.horizontalAdvance(xlabel(p)) for g in self.right.values() for p in g] + [80])
        mlw = max([mfm.horizontalAdvance(p[3]) for g in self.left.values() for p in g] + [60])
        slw = max([mfm.horizontalAdvance(p[3]) for g in self.right.values() for p in g] + [60])
        mtw = max([bfm.horizontalAdvance(m.name) for m in sp.masters] + [60]) + 80
        stw = max([bfm.horizontalAdvance(s.name) for s in sp.slaves] + [60]) + 80
        self.xbar_w = max(300, lw + rw + 70)
        self.mx, self.mw = MARGIN, max(180, mlw + 28, mtw)
        self.xx = self.mx + self.mw + WIRE
        self.sx = self.xx + self.xbar_w + WIRE
        self.sw = max(240, slw + 28, stw, mfm.horizontalAdvance("0x00000000 – 0x00000000") + 90)

        def slots(groups):
            out, y = {}, TOP + 40
            for k, pins in groups.items():
                h = HEAD + max(1, len(pins)) * PIN_H + 8
                out[k] = QRectF(0, y, 0, h)
                y += h + GAP
            return out, y
        self.mslots, ly = slots(self.left)
        self.sslots, ry = slots(self.right)
        self.xbar_rect = QRectF(self.xx, TOP, self.xbar_w, max(ly, ry) - TOP + 24)
        self._size = QSize(int(self.sx + self.sw + MARGIN), int(self.xbar_rect.bottom() + 40))
        self._fit()
        self.updateGeometry()

    @staticmethod
    def _pin_ys(slot, n):
        return [slot.top() + HEAD + i * PIN_H + PIN_H / 2 for i in range(n)]

    # ------------------------------------------------------------ paint
    def paintEvent(self, _):
        if self.spec is None:
            return
        t = theme.manager().tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.error:
            p.setPen(QColor(t["danger"]))
            p.drawText(QRectF(self.rect()).adjusted(16, 16, -16, -16),
                       Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.error)
            return
        p.scale(self._scale, self._scale)
        sp = self.spec
        self._hits = []
        bold = QFont(self.font())
        bold.setBold(True)
        small = self.mono

        # legend
        p.setFont(self.font())
        x = MARGIN
        for color, text in ((IN_COLOR, "crossbar girişi (input)"), (OUT_COLOR, "crossbar çıkışı (output)")):
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(QPointF(x, 18), QPointF(x + 26, 18))
            self._arrow(p, QPointF(x + 26, 18), 1, QColor(color))
            p.setPen(QColor(t["muted"]))
            p.drawText(QPointF(x + 34, 22), text)
            x += 34 + p.fontMetrics().horizontalAdvance(text) + 24

        # crossbar
        xr = self.xbar_rect
        p.setPen(QPen(QColor(t["primary"]), 2))
        p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(xr, 10, 10)
        head = QRectF(xr.left(), xr.top(), xr.width(), 34)
        path = QPainterPath()
        path.addRoundedRect(head, 10, 10)
        p.fillPath(path, QColor(t["primary"]))
        p.fillRect(QRectF(head.left(), head.bottom() - 10, head.width(), 10), QColor(t["primary"]))
        p.setPen(QColor("#FFFFFF"))
        p.setFont(bold)
        p.drawText(head, Qt.AlignmentFlag.AlignCenter, f"{sp.module}   ·   axilxbar")
        p.setFont(small)
        for k, (d, w, n) in enumerate(self.clock):
            cx = xr.left() + 40 + k * 110
            p.setPen(QPen(QColor(IN_COLOR), 2))
            p.drawLine(QPointF(cx, xr.top() - 34), QPointF(cx, xr.top()))
            self._arrow(p, QPointF(cx, xr.top()), 2, QColor(IN_COLOR))
            p.setPen(QColor(t["text"]))
            p.drawText(QPointF(cx + 6, xr.top() - 22), n)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(xr.left(), xr.bottom() - 22, xr.width(), 18), Qt.AlignmentFlag.AlignCenter,
                   f"{sp.module}_pk (HDL Designer): port başına paketli REQ[110:0] / RSP[40:0]" if sp.style == "packed"
                   else f"{sp.module}: ayrık AXI4-Lite sinyalleri")

        for i, m in enumerate(sp.masters):
            box = QRectF(self.mx, self.mslots[i].top(), self.mw, self.mslots[i].height())
            self._block(p, t, bold, small, box, m.name, f"MASTER{i}", "", "", ("m", i) == self.selected, False)
            self._hits.append((box, "m", i, f"MASTER{i} · {m.name}"))
            self._side(p, t, small, self.left[i], self.mslots[i], True)
        for j, s in enumerate(sp.slaves):
            box = QRectF(self.sx, self.sslots[j].top(), self.sw, self.sslots[j].height())
            rng = f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}"
            self._block(p, t, bold, small, box, s.name, f"SLAVE{j}", rng, size_text(s.window),
                        ("s", j) == self.selected, j in self.bad)
            self._hits.append((box, "s", j, f"SLAVE{j} · {s.name}\n{rng}"))
            self._side(p, t, small, self.right[j], self.sslots[j], False)

    def _block(self, p, t, bold, small, box, title, port, line2, line3, selected, bad):
        fill = QColor(t["surface"])
        border = QColor(t["danger"]) if bad else QColor(t["text"] if selected else t["border"])
        p.setPen(QPen(border, 2.5 if selected or bad else 1.2))
        p.setBrush(fill)
        p.drawRoundedRect(box, 8, 8)
        inner = box.adjusted(10, 6, -10, 0)
        p.setFont(bold)
        p.setPen(QColor(t["text"]))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width() - 70, 18), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(inner.width() - 70)))
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 18), Qt.AlignmentFlag.AlignRight, port)
        if line2:
            p.drawText(QRectF(inner.left(), inner.top() + 19, inner.width(), 16), Qt.AlignmentFlag.AlignLeft, line2)
        if line3:
            p.drawText(QRectF(inner.left(), inner.top() + 19, inner.width(), 16), Qt.AlignmentFlag.AlignRight, line3)
        p.setPen(QPen(QColor(t["border"]), 1))
        p.drawLine(QPointF(box.left() + 1, box.top() + HEAD - 6), QPointF(box.right() - 1, box.top() + HEAD - 6))

    @staticmethod
    def _arrow(p, tip, direction, color):
        """direction: 1 right, -1 left, 2 down."""
        s = 6
        if direction == 2:
            poly = QPolygonF([tip, QPointF(tip.x() - s / 1.4, tip.y() - s), QPointF(tip.x() + s / 1.4, tip.y() - s)])
        else:
            d = direction
            poly = QPolygonF([tip, QPointF(tip.x() - d * s, tip.y() - s / 1.4),
                              QPointF(tip.x() - d * s, tip.y() + s / 1.4)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(poly)

    def _side(self, p, t, small, pins, slot, left):
        ys = self._pin_ys(slot, len(pins))
        x_edge = self.xbar_rect.left() if left else self.xbar_rect.right()
        block_edge = self.mx + self.mw if left else self.sx
        for (d, w, xname, bname, members), y in zip(pins, ys):
            is_in = d == "input"
            color = QColor(IN_COLOR if is_in else OUT_COLOR)
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(block_edge, y), QPointF(x_edge, y))
            # arrow at the receiving end: crossbar for its inputs, the block for crossbar outputs
            if left:
                self._arrow(p, QPointF(x_edge if is_in else block_edge, y), 1 if is_in else -1, color)
            else:
                self._arrow(p, QPointF(x_edge if is_in else block_edge, y), -1 if is_in else 1, color)
            p.setFont(small)
            p.setPen(QColor(t["text"]))
            half = self.xbar_w / 2 - 12
            xr = QRectF(x_edge + 8, y - 9, half, 18) if left else QRectF(x_edge - half - 8, y - 9, half, 18)
            p.drawText(xr, (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight) |
                       Qt.AlignmentFlag.AlignVCenter, f"{xname} {w}".strip())
            bw = self.mw if left else self.sw
            br = QRectF(block_edge - bw + 8, y - 9, bw - 16, 18) if left else \
                QRectF(block_edge + 8, y - 9, bw - 16, 18)
            p.setPen(QColor(t["muted"]))
            p.drawText(br, (Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft) |
                       Qt.AlignmentFlag.AlignVCenter, bname)
            own = "output" if is_in else "input"
            tip = f"crossbar: {d} {w} {xname}\nblok: {own} {w} {block_pin_name(xname)}".replace("  ", " ")
            if members:
                tip = f"crossbar {d}, blok {own}:\n" + "\n".join(
                    f"  {m[2]:<22} {m[1]}  ↔  {block_pin_name(m[2])}" for m in members)
            self._hits.append((QRectF(min(block_edge, x_edge), y - 8, abs(x_edge - block_edge), 16),
                               "pin", -1, tip))
            self._hits.append((xr, "pin", -1, tip))
            self._hits.append((br, "pin", -1, tip))

    # ------------------------------------------------------------ mouse
    def _hit(self, pos):
        pos = QPointF(pos.x() / self._scale, pos.y() / self._scale)
        for r, kind, idx, tip in self._hits:
            if r.contains(pos):
                return kind, idx, tip
        return None, None, None

    def mouseMoveEvent(self, ev):
        if self._pan is not None and self.scroll is not None:
            d = ev.globalPosition() - self._pan
            self._pan = ev.globalPosition()
            self.scroll.horizontalScrollBar().setValue(self.scroll.horizontalScrollBar().value() - int(d.x()))
            self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().value() - int(d.y()))
            return
        kind, idx, tip = self._hit(ev.position())
        if tip:
            QToolTip.showText(ev.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()
        self.setCursor(Qt.CursorShape.PointingHandCursor if kind in ("m", "s") else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, ev):
        kind, idx, _ = self._hit(ev.position())
        if kind == "m":
            self.masterClicked.emit(idx)
        elif kind == "s":
            self.slaveClicked.emit(idx)
        else:   # drag the empty background to pan
            self._pan = ev.globalPosition()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, ev):
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, ev):
        kind, idx, _ = self._hit(ev.position())
        if kind == "m":
            self.masterActivated.emit(idx)
        elif kind == "s":
            self.slaveActivated.emit(idx)
