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
        foot = (f"{sp.module}_pk (HDL Designer): port başına paketli REQ[110:0] / RSP[40:0]" if sp.style == "packed"
                else f"{sp.module}: ayrık AXI4-Lite sinyalleri")
        p.drawText(QRectF(xr.left() + 6, xr.bottom() - 22, xr.width() - 12, 18), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(foot, Qt.TextElideMode.ElideRight, int(xr.width() - 12)))

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


# ============================================================================ whole system
class SystemDiagram(BlockDiagram):
    """The whole bus hierarchy left to right: every interconnect with its masters on the left and
    its slaves on the right. A Sub-bus Bridge is drawn as its own block: a slave of the bus on its
    left and MASTER0 of the interconnect behind it (whose extra masters sit below it). Click an
    interconnect or a master to pick that interconnect, a slave to select it; double-click a
    bridge to pick its interconnect, any other slave to edit it in the Address Map Editor."""

    levelClicked = Signal(str)
    instClicked = Signal(str)
    instActivated = Signal(str)

    MW, XW, SW, SWIRE = 170, 190, 230, 56
    LEAF_H, BRIDGE_H, MASTER_H, VGAP, XHEAD = 56, 66, 42, 12, 30

    def __init__(self):
        super().__init__()
        self.project = None
        self.style = "packed"
        self.level_sel = ""
        self.inst_sel = None
        self._items = []

    def set_project(self, project, style, level_sel="", inst_sel=None):
        self.project, self.style, self.level_sel, self.inst_sel = project, style, level_sel, inst_sel
        self.spec = project          # BlockDiagram.paintEvent guard
        try:
            self._build()
            self.error = None
        except Exception as exc:  # noqa: BLE001 - keep the page working
            self.error = f"Sistem diyagramı çizilemedi: {exc}"
        self.update()

    def set_all_signals(self, on):
        pass

    # ------------------------------------------------------------ layout
    def _colx(self, depth):
        return MARGIN + depth * (self.SW + self.SWIRE + self.XW + self.SWIRE)

    def _build(self):
        from axil_studio.core import addrmap as A
        proj = self.project
        self._items = []          # (kind, rect, data)
        self._wires = []          # (QPointF, QPointF)
        self._maxdepth = 0
        top_masters = A.xbar_masters(proj, None)
        mrects = []
        y = TOP - 40 + self.XHEAD + 6
        for i, m in enumerate(top_masters):
            r = QRectF(self._colx(0), y, self.SW, self.MASTER_H)
            self._items.append(("m", r, ("", i, m.name, False)))
            mrects.append(r)
            y += self.MASTER_H + self.VGAP
        bottom = self._level(A, None, TOP - 40, 0, mrects)
        w = self._colx(self._maxdepth + 1) + self.SW + MARGIN
        self._size = QSize(int(w), int(max(bottom, y) + 30))
        self._fit()
        self.updateGeometry()

    def _level(self, A, bridge, y0, depth, mrects):
        """Lay out one interconnect starting at y0 (its masters' rects given); returns its bottom."""
        proj = self.project
        self._maxdepth = max(self._maxdepth, depth)
        xx = self._colx(depth) + self.SW + self.SWIRE
        sx = self._colx(depth + 1)
        y = y0 + self.XHEAD + 6
        ports = []
        sl = A.xbar_slaves(proj, bridge)
        bad = A.overlapping(proj, bridge) | (A.outside(proj, bridge) if bridge is not None else set())
        for j, (inst, s) in enumerate(zip(proj.level(bridge), sl)):
            if not inst.bridge:
                r = QRectF(sx, y, self.SW, self.LEAF_H)
                self._items.append(("s", r, (inst, s, j, j in bad)))
                ports.append(r.center().y())
                y = r.bottom() + self.VGAP
                continue
            r = QRectF(sx, y, self.SW, self.BRIDGE_H)
            self._items.append(("b", r, (inst, s, j, j in bad)))
            ports.append(r.center().y())
            sub = [r]
            yy = r.bottom() + self.VGAP
            for k, name in enumerate(inst.xbar_masters):
                mr = QRectF(sx, yy, self.SW, self.MASTER_H)
                self._items.append(("m", mr, (inst.name, k + 1, name, True)))
                sub.append(mr)
                yy = mr.bottom() + self.VGAP
            child_bottom = self._level(A, inst, y - 8, depth + 1, sub)
            y = max(yy, child_bottom + self.VGAP)
        last = max([y - self.VGAP] + [r.bottom() for r in mrects]) + 10
        xr = QRectF(xx, y0, self.XW, max(last - y0, self.XHEAD + 40))
        spec_name = A.xbar_module(proj, bridge)
        self._items.append(("x", xr, (bridge.name if bridge else "", spec_name, len(mrects), len(ports))))
        for i, r in enumerate(mrects):
            self._wires.append((QPointF(r.right(), r.center().y()), QPointF(xr.left(), r.center().y()), f"M{i}"))
        for j, py in enumerate(ports):
            self._wires.append((QPointF(xr.right(), py), QPointF(sx, py), f"S{j}"))
        return xr.bottom()

    # ------------------------------------------------------------ paint
    def paintEvent(self, _):
        if self.project is None:
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
        self._hits = []
        bold = QFont(self.font())
        bold.setBold(True)
        small = theme.mono_font()
        small.setPixelSize(10)
        p.setFont(self.font())
        p.setPen(QColor(t["muted"]))
        p.drawText(QPointF(MARGIN, 22), "İstek yönü soldan sağa: master → interconnect → slave · "
                                        "Sub-bus Bridge: üst bus'ın slave'i, arkasındaki interconnect'in MASTER0'ı")
        # wires, blocks, then the port labels on the interconnects
        for a, b, label in self._wires:
            p.setPen(QPen(QColor(IN_COLOR), 2))
            p.drawLine(a, b)
            self._arrow(p, b, 1, QColor(IN_COLOR))
        self._blocks(p, t, bold, small)
        for a, b, label in self._wires:
            p.setFont(small)
            p.setPen(QColor(t["muted"]))
            if label.startswith("M"):
                p.drawText(QRectF(b.x() + 4, b.y() - 8, 40, 16), Qt.AlignmentFlag.AlignLeft |
                           Qt.AlignmentFlag.AlignVCenter, label)
            else:
                p.drawText(QRectF(a.x() - 44, a.y() - 8, 40, 16), Qt.AlignmentFlag.AlignRight |
                           Qt.AlignmentFlag.AlignVCenter, label)

    def _blocks(self, p, t, bold, small):
        for kind, r, data in self._items:
            if kind == "x":
                self._xbar(p, t, bold, small, r, *data)
            elif kind == "m":
                lvl, i, name, extra = data
                sel = lvl == self.level_sel
                p.setPen(QPen(QColor(t["text"] if sel else t["border"]), 1.8 if sel else 1.2))
                p.setBrush(QColor(t["surface"]))
                p.drawRoundedRect(r, 8, 8)
                p.setFont(bold)
                p.setPen(QColor(t["text"]))
                p.drawText(r.adjusted(10, 0, -60, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(r.width() - 70)))
                p.setFont(small)
                p.setPen(QColor(t["muted"]))
                p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           f"MASTER{i}")
                where = "üst bus" if not lvl else lvl
                self._hits.append((r, "m", lvl, f"MASTER{i} · {name}\n{where} interconnect'i"
                                   + ("\n(ek master: yalnızca bu interconnect'in slave'lerine erişir)" if extra else "")))
            else:
                inst, s, j, bad = data
                self._slave(p, t, bold, small, r, kind == "b", inst, s, j, bad)

    def _xbar(self, p, t, bold, small, r, lvl, module, nm, ns):
        sel = lvl == self.level_sel
        p.setPen(QPen(QColor(t["text"] if sel else t["primary"]), 3 if sel else 2))
        p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(r, 10, 10)
        head = QRectF(r.left(), r.top(), r.width(), self.XHEAD)
        path = QPainterPath()
        path.addRoundedRect(head, 10, 10)
        p.fillPath(path, QColor(t["primary"]))
        p.fillRect(QRectF(head.left(), head.bottom() - 10, head.width(), 10), QColor(t["primary"]))
        p.setPen(QColor("#FFFFFF"))
        p.setFont(bold)
        p.drawText(head.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(module, Qt.TextElideMode.ElideRight, int(head.width() - 16)))
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(r.left(), r.bottom() - 22, r.width(), 16), Qt.AlignmentFlag.AlignCenter,
                   f"axilxbar · {nm}M × {ns}S")
        self._hits.append((r, "x", lvl, f"{module}\n{'üst bus' if not lvl else lvl + ' arkasındaki'} interconnect · "
                                        f"{nm} master, {ns} slave\nTıkla: seç"))

    def _slave(self, p, t, bold, small, r, is_bridge, inst, s, j, bad):
        from axil_studio.core import addrmap as A
        sel = inst.name == self.inst_sel or (is_bridge and inst.name == self.level_sel)
        if is_bridge:
            p.setBrush(QColor(t["accent_soft"]))
            border = QColor(t["danger"] if bad else t["text"] if sel else t["accent"])
            p.setPen(QPen(border, 2.5 if sel or bad else 1.6))
        else:
            p.setBrush(QColor(t["surface"]))
            border = QColor(t["danger"] if bad else t["text"] if sel else t["border"])
            p.setPen(QPen(border, 2.5 if sel or bad else 1.2))
        p.drawRoundedRect(r, 8, 8)
        inner = r.adjusted(10, 5, -10, -4)
        p.setFont(bold)
        p.setPen(QColor(t["accent"] if is_bridge else t["text"]))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width() - 60, 18), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(inst.name, Qt.TextElideMode.ElideRight, int(inner.width() - 60)))
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 18), Qt.AlignmentFlag.AlignRight, f"SLAVE{j}")
        rng = f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}"
        p.setPen(QColor(t["danger"]) if bad else QColor(t["muted"]))
        p.drawText(QRectF(inner.left(), inner.top() + 19, inner.width(), 15), Qt.AlignmentFlag.AlignLeft, rng)
        p.setPen(QColor(t["muted"]))
        if is_bridge:
            kind = A.BRIDGE_LABEL + (f" · kendi {size_text(inst.self_window)}" if inst.self_window else "")
        else:
            kind = "harici" if inst.external else inst.ip
        p.drawText(QRectF(inner.left(), inner.top() + 34, inner.width(), 15), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(kind, Qt.TextElideMode.ElideRight, int(inner.width() - 60)))
        p.drawText(QRectF(inner.left(), inner.top() + 34, inner.width(), 15), Qt.AlignmentFlag.AlignRight,
                   size_text(s.window))
        if is_bridge:
            tip = (f"{inst.name} · {A.BRIDGE_LABEL}\n{rng}\nüst bus'ta SLAVE{j}, arkasındaki interconnect'te MASTER0"
                   f"\nÇift tık: arkasındaki interconnect'i seç")
        else:
            tip = f"SLAVE{j} · {inst.name}\n{rng}\nÇift tık: Address Map Editor'da düzenle"
        self._hits.append((r, "b" if is_bridge else "s", inst.name, tip))

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, ev):
        kind, name, _ = self._hit(ev.position())
        if kind in ("x", "m"):
            self.levelClicked.emit(name)
        elif kind in ("s", "b"):
            self.instClicked.emit(name)
        else:
            self._pan = ev.globalPosition()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseDoubleClickEvent(self, ev):
        kind, name, _ = self._hit(ev.position())
        if kind == "b":
            self.levelClicked.emit(name)
        elif kind == "s":
            self.instActivated.emit(name)

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            return super().mouseMoveEvent(ev)
        kind, _, tip = self._hit(ev.position())
        if tip:
            QToolTip.showText(ev.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()
        self.setCursor(Qt.CursorShape.PointingHandCursor if kind in ("x", "m", "s", "b")
                       else Qt.CursorShape.ArrowCursor)
