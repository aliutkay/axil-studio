"""Block diagrams of the interconnects.

BlockDiagram: one crossbar with its masters on the left and slaves on the right. Crossbar pins
come from the generated wrapper header (xbargen.xbar_ports), so names, widths and directions are
exactly those of the HDL for the shown port style (AXI4-Lite signals or HDL Designer REQ/RSP).
Every master and slave block shows its own side of each connection (M_AXI_* / S_AXI_*, or the
packed MASTER_AXI_* / SLAVE_AXI_* ports of the HDL Designer blocks).

SystemDiagram: the whole bus hierarchy left to right; each connection is drawn as its AXI4-Lite
channels (AW, W, B, AR, R) or as the HDL Designer REQ / RSP pair.

Blocks are coloured by kind (master, interconnect, IP, external slave, Sub-bus Bridge). Both
diagrams draw through a Canvas that can also record what it draws, so they export to PDF and to
draw.io (.drawio) exactly as shown. The mouse wheel zooms around the cursor, dragging the empty
background pans; "fit" scales the diagram to the available width."""

import html
import re

from PySide6.QtCore import QEvent, QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter,
                           QPen, QPicture, QPolygonF)
from PySide6.QtWidgets import QToolTip, QWidget

from axil_studio.core import xbargen as X
from axil_studio import theme
from axil_studio.dialogs import size_text
from axil_studio.core.model import hex32
from axil_studio.i18n import tr

IN_COLOR = "#3B82F6"     # towards the slaves (requests)
OUT_COLOR = "#D99A00"    # towards the masters (responses)
PIN_H = 20
HEAD = 46                # block title area above the first pin
GAP = 16
WIRE = 70
MARGIN = 24
TOP = 92
ZOOM_MIN, ZOOM_MAX = 0.3, 3.0

# how much of a connection is shown (both diagrams), from the plainest to the HDL Designer ports
DETAILS = {
    "simple": tr("Master / slave"),
    "axi": tr("AXI Lite (Simplified View)"),
    "axi_full": tr("AXI Lite (Detailed View)"),
    "packed": "HDL Designer (REQ / RSP)",
}
# whole-system diagram: the lines drawn per connection: (label, 1 towards the slave / -1 back)
CHANNELS = {
    "simple": [("", 1)],
    "axi": [("AW", 1), ("W", 1), ("B", -1), ("AR", 1), ("R", -1)],
    "axi_full": [(name, 1 if d == "in" else -1) for name, d, _w in X.AXIL_SIGNALS],
    "packed": [("REQ", 1), ("RSP", -1)],
}
STEP = {"simple": 0, "axi": 9, "axi_full": 8, "packed": 13}
MODE_TEXT = DETAILS


def block_pin_name(xbar_name):
    """Name of the same connection on the master / slave side."""
    packed = re.match(r"^(MASTER|SLAVE)\d+_AXI_(REQ|RSP)$", xbar_name)
    if packed:
        return f"{packed.group(1)}_AXI_{packed.group(2)}"
    m = re.match(r"^(MASTER|SLAVE)\d+_AXI_(.+)$", xbar_name)
    if m:
        return ("M_AXI_" if m.group(1) == "MASTER" else "S_AXI_") + m.group(2)
    return xbar_name


# ============================================================================ drawing + recording
class Canvas:
    """Draws rectangles, text and arrows on a QPainter; with `rec` (a list) it also records them
    as primitives for the draw.io export."""

    def __init__(self, painter, rec=None):
        self.p = painter
        self.rec = rec

    def rect(self, r, fill, stroke, width=1.2, radius=8):
        p = self.p
        p.setPen(QPen(QColor(stroke), width) if stroke else Qt.PenStyle.NoPen)
        p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, radius, radius)
        if self.rec is not None:
            self.rec.append(("rect", QRectF(r), fill, stroke, width, radius))

    def head(self, r, fill, radius=10):
        """A rounded box's title band (rounded on top only)."""
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        self.p.fillPath(path, QColor(fill))
        self.p.fillRect(QRectF(r.left(), r.bottom() - radius, r.width(), radius), QColor(fill))
        if self.rec is not None:
            self.rec.append(("rect", QRectF(r), fill, None, 0, radius))

    def text(self, r, s, color, font, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
             elide=True):
        p = self.p
        p.setFont(font)
        p.setPen(QColor(color))
        if elide:
            s = QFontMetrics(font).elidedText(s, Qt.TextElideMode.ElideRight, int(max(4, r.width())))
        p.drawText(r, align, s)
        if self.rec is not None and s:
            self.rec.append(("text", QRectF(r), s, color, QFont(font), int(align)))

    def line(self, a, b, color, width=2, arrow=0):
        """arrow: 1 at b, -1 at a, 0 none."""
        p = self.p
        p.setPen(QPen(QColor(color), width))
        p.drawLine(a, b)
        if arrow:
            tip, frm = (b, a) if arrow > 0 else (a, b)
            self._head(tip, frm, QColor(color))
        if self.rec is not None:
            self.rec.append(("line", QPointF(a), QPointF(b), color, width, arrow))

    def _head(self, tip, frm, color, s=6):
        dx, dy = tip.x() - frm.x(), tip.y() - frm.y()
        n = max(1e-6, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / n, dy / n
        poly = QPolygonF([tip, QPointF(tip.x() - ux * s - uy * s / 1.4, tip.y() - uy * s + ux * s / 1.4),
                          QPointF(tip.x() - ux * s + uy * s / 1.4, tip.y() - uy * s - ux * s / 1.4)])
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(color)
        self.p.drawPolygon(poly)


def drawio_xml(prims, width, height, name="diagram"):
    """A draw.io (diagrams.net) file holding the recorded primitives as shapes, texts and arrows."""
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    k = 2

    def esc(v):
        return html.escape(str(v), quote=True)
    for prim in prims:
        if prim[0] == "rect":
            _, r, fill, stroke, w, radius = prim
            arc = int(min(50, radius * 100 / max(1, min(r.width(), r.height()))))
            style = (f"rounded=1;arcSize={arc};absoluteArcSize=0;whiteSpace=wrap;html=1;"
                     f"fillColor={fill or 'none'};strokeColor={stroke or 'none'};strokeWidth={w or 1};")
            cells.append(f'<mxCell id="c{k}" value="" style="{esc(style)}" vertex="1" parent="1">'
                         f'<mxGeometry x="{r.x():.1f}" y="{r.y():.1f}" width="{r.width():.1f}" '
                         f'height="{r.height():.1f}" as="geometry"/></mxCell>')
        elif prim[0] == "text":
            _, r, s, color, font, align = prim
            h = ("center" if align & int(Qt.AlignmentFlag.AlignHCenter) else
                 "right" if align & int(Qt.AlignmentFlag.AlignRight) else "left")
            size = font.pixelSize() if font.pixelSize() > 0 else round(font.pointSizeF() * 4 / 3)
            style = (f"text;html=1;whiteSpace=nowrap;align={h};verticalAlign=middle;fontColor={color};"
                     f"fontSize={size};fontFamily={font.family()};fontStyle={1 if font.bold() else 0};"
                     "spacing=0;overflow=visible;")
            cells.append(f'<mxCell id="c{k}" value="{esc(s)}" style="{esc(style)}" vertex="1" parent="1">'
                         f'<mxGeometry x="{r.x():.1f}" y="{r.y():.1f}" width="{r.width():.1f}" '
                         f'height="{r.height():.1f}" as="geometry"/></mxCell>')
        else:
            _, a, b, color, w, arrow = prim
            if arrow < 0:
                a, b = b, a
            style = (f"endArrow={'block' if arrow else 'none'};endFill=1;html=1;strokeColor={color};"
                     f"strokeWidth={w};")
            cells.append(f'<mxCell id="c{k}" value="" style="{esc(style)}" edge="1" parent="1">'
                         f'<mxGeometry relative="1" as="geometry">'
                         f'<mxPoint x="{a.x():.1f}" y="{a.y():.1f}" as="sourcePoint"/>'
                         f'<mxPoint x="{b.x():.1f}" y="{b.y():.1f}" as="targetPoint"/>'
                         f'</mxGeometry></mxCell>')
        k += 1
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mxfile host="AXIL Studio"><diagram name="{esc(name)}" id="axil">'
            f'<mxGraphModel dx="{int(width)}" dy="{int(height)}" grid="0" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{int(width)}" '
            f'pageHeight="{int(height)}" background="#FFFFFF" math="0" shadow="0"><root>'
            + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")


# ============================================================================ one crossbar
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
        self.detail = "axi"           # see DETAILS
        self.bad = set()
        self.kinds = []               # per slave: "ip" | "ext" | "bridge"
        self.error = None
        self.zoom = None              # None: fit to width
        self._scale = 1.0
        self._hits = []
        self._size = QSize(900, 400)
        self._tokens = None           # a fixed palette while exporting
        self.mono = theme.mono_font()
        self.scroll = None            # the QScrollArea around the diagram (set by the page)
        self._pan = None
        self.setMouseTracking(True)

    def tokens(self):
        return self._tokens or theme.manager().tokens

    # ------------------------------------------------------------ data
    def set_spec(self, spec, selected=None, bad=(), kinds=None):
        self.spec, self.selected, self.bad = spec, selected, set(bad)
        self.kinds = list(kinds or [])
        try:
            self._build()
            self.error = None
        except Exception as exc:  # noqa: BLE001 - the page must keep working; show why instead
            self.error = tr("Could not draw the block diagram: {0}").format(exc)
        self.update()

    def set_detail(self, detail):
        """How much of each connection to show; the spec's port style must match (packed for
        "packed", signals otherwise)."""
        self.detail = detail if detail in DETAILS else "axi"
        if self.spec is not None:
            self.set_spec(self.spec, self.selected, self.bad, self.kinds)

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

    # ------------------------------------------------------------ painting / export
    def paintEvent(self, _):
        t = self.tokens()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t["diagram_bg"]))
        if self.spec is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.error:
            p.setPen(QColor(t["danger"]))
            p.drawText(QRectF(self.rect()).adjusted(16, 16, -16, -16),
                       Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.error)
            return
        p.scale(self._scale, self._scale)
        self._hits = []
        self.render_to(Canvas(p), t)

    def _export_tokens(self):
        return dict(theme.LIGHT)          # exports are always light, on white

    SEL_ATTRS = ("selected", "level_sel", "inst_sel")

    def _render_export(self, painter, rec=None):
        """Draw for an export: light palette, no selection highlight; the screen state is kept."""
        keep = {a: getattr(self, a) for a in self.SEL_ATTRS if hasattr(self, a)}
        hits = self._hits
        for a in keep:
            setattr(self, a, "" if a == "level_sel" else None)
        if "level_sel" in keep:
            self.level_sel = None
        self._tokens = self._export_tokens()
        try:
            self.render_to(Canvas(painter, rec), self._tokens)
        finally:
            self._tokens = None
            self._hits = hits
            for a, v in keep.items():
                setattr(self, a, v)

    def export_pdf(self, path):
        """The diagram as one PDF page of its own size (always the light palette, white page)."""
        if self.spec is None or self.error:
            raise RuntimeError(self.error or tr("Nothing to draw"))
        w, h = self._size.width(), self._size.height()
        pdf = QPdfWriter(path)
        pdf.setResolution(72)
        pdf.setPageLayout(QPageLayout(QPageSize(QSizeF(w, h), QPageSize.Unit.Point, "diagram",
                                                QPageSize.SizeMatchPolicy.ExactMatch),
                                      QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
        pdf.setTitle(tr("AXIL Studio block diagram"))
        p = QPainter(pdf)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sx = pdf.width() / w
        p.scale(sx, sx)
        p.fillRect(QRectF(0, 0, w, h), QColor("#FFFFFF"))
        try:
            self._render_export(p)
        finally:
            p.end()

    def export_drawio(self, path, name="diagram"):
        """The diagram as a draw.io file: every block, text and connection as an editable shape."""
        if self.spec is None or self.error:
            raise RuntimeError(self.error or tr("Nothing to draw"))
        pic = QPicture()
        p = QPainter(pic)
        rec = []
        try:
            self._render_export(p, rec)
        finally:
            p.end()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(drawio_xml(rec, self._size.width(), self._size.height(), name))

    # ------------------------------------------------------------ layout
    def _pins(self, pins, side_prefix):
        """[(dir, range, xbar_label, block_label, members)]: one arrow per port (master / slave),
        the signals bundled per direction (simplified AXI Lite) or every pin (detailed AXI Lite,
        HDL Designer)."""
        short = "M" if side_prefix.startswith("MASTER") else "S"
        if self.detail == "simple":
            d = "input" if short == "M" else "output"         # requests flow master -> crossbar -> slave
            return [(d, "", f"{side_prefix}_AXI", f"{short}_AXI", pins)]
        if self.spec.style == "signals" and self.detail != "axi_full":
            out = []
            for d in ("input", "output"):
                grp = [p for p in pins if p[0] == d]
                if grp:
                    xb = tr("input") if d == "input" else tr("output")
                    own = tr("output") if d == "input" else tr("input")     # the block's own direction
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

    def render_to(self, cv, t):
        sp = self.spec
        bold = QFont(self.font())
        bold.setBold(True)
        small = self.mono
        base = QFont(self.font())

        # legend
        x = MARGIN
        legend = (((IN_COLOR, tr("request direction (master → slave)")),) if self.detail == "simple" else
                  ((IN_COLOR, tr("crossbar input")), (OUT_COLOR, tr("crossbar output"))))
        for color, text in legend:
            cv.line(QPointF(x, 18), QPointF(x + 26, 18), color, 2, 1)
            w = QFontMetrics(base).horizontalAdvance(text) + 4
            cv.text(QRectF(x + 34, 8, w, 20), text, t["muted"], base, elide=False)
            x += 34 + w + 20

        # crossbar
        xr = self.xbar_rect
        xf, xb, xt = theme.block_colors("xbar", t)
        cv.rect(xr, xf, xb, 2, 10)
        head = QRectF(xr.left(), xr.top(), xr.width(), 34)
        cv.head(head, xb)
        cv.text(head, f"{sp.module}   ·   axilxbar", "#FFFFFF", bold, Qt.AlignmentFlag.AlignCenter)
        for k, (d, w, n) in enumerate(self.clock):
            cx = xr.left() + 40 + k * 110
            cv.line(QPointF(cx, xr.top() - 34), QPointF(cx, xr.top()), IN_COLOR, 2, 1)
            cv.text(QRectF(cx + 6, xr.top() - 32, 100, 16), n, t["text"], small)
        foot = (tr("{0}_pk (HDL Designer): packed REQ[110:0] / RSP[40:0] per port").format(sp.module) if sp.style == "packed"
                else tr("{0}: separate AXI4-Lite signals").format(sp.module))
        cv.text(QRectF(xr.left() + 6, xr.bottom() - 22, xr.width() - 12, 18), foot, xt, small,
                Qt.AlignmentFlag.AlignCenter)

        for i, m in enumerate(sp.masters):
            box = QRectF(self.mx, self.mslots[i].top(), self.mw, self.mslots[i].height())
            self._block(cv, t, bold, small, box, "master", m.name, f"MASTER{i}", "", "", ("m", i) == self.selected,
                        False)
            self._hits.append((box, "m", i, f"MASTER{i} · {m.name}"))
            self._side(cv, t, small, self.left[i], self.mslots[i], True)
        for j, s in enumerate(sp.slaves):
            box = QRectF(self.sx, self.sslots[j].top(), self.sw, self.sslots[j].height())
            rng = f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}"
            kind = self.kinds[j] if j < len(self.kinds) else ("ext" if s.external else "ip")
            self._block(cv, t, bold, small, box, kind, s.name, f"SLAVE{j}", rng, size_text(s.window),
                        ("s", j) == self.selected, j in self.bad)
            self._hits.append((box, "s", j, f"SLAVE{j} · {s.name}\n{rng}"))
            self._side(cv, t, small, self.right[j], self.sslots[j], False)

    def _block(self, cv, t, bold, small, box, kind, title, port, line2, line3, selected, bad):
        fill, border, tcol = theme.block_colors(kind, t)
        stroke = t["danger"] if bad else t["text"] if selected else border
        cv.rect(box, fill, stroke, 2.5 if selected or bad else 1.4, 8)
        inner = box.adjusted(10, 6, -10, 0)
        cv.text(QRectF(inner.left(), inner.top(), inner.width() - 70, 18), title, tcol, bold)
        cv.text(QRectF(inner.left(), inner.top(), inner.width(), 18), port, t["muted"], small,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if line2:
            cv.text(QRectF(inner.left(), inner.top() + 19, inner.width() - 60, 16), line2, t["muted"], small)
        if line3:
            cv.text(QRectF(inner.left(), inner.top() + 19, inner.width(), 16), line3, t["muted"], small,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cv.line(QPointF(box.left() + 1, box.top() + HEAD - 6), QPointF(box.right() - 1, box.top() + HEAD - 6),
                border, 1)

    def _side(self, cv, t, small, pins, slot, left):
        ys = self._pin_ys(slot, len(pins))
        x_edge = self.xbar_rect.left() if left else self.xbar_rect.right()
        block_edge = self.mx + self.mw if left else self.sx
        for (d, w, xname, bname, members), y in zip(pins, ys):
            is_in = d == "input"
            color = IN_COLOR if is_in or self.detail == "simple" else OUT_COLOR    # simple: request direction
            # arrow at the receiving end: crossbar for its inputs, the block for crossbar outputs
            a, b = QPointF(block_edge, y), QPointF(x_edge, y)
            cv.line(a, b, color, 2, 1 if is_in else -1)
            half = self.xbar_w / 2 - 12
            xr = QRectF(x_edge + 8, y - 9, half, 18) if left else QRectF(x_edge - half - 8, y - 9, half, 18)
            cv.text(xr, f"{xname} {w}".strip(), t["text"], small,
                    (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight) |
                    Qt.AlignmentFlag.AlignVCenter)
            bw = self.mw if left else self.sw
            br = QRectF(block_edge - bw + 8, y - 9, bw - 16, 18) if left else \
                QRectF(block_edge + 8, y - 9, bw - 16, 18)
            cv.text(br, bname, t["muted"], small,
                    (Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft) |
                    Qt.AlignmentFlag.AlignVCenter)
            own = "output" if is_in else "input"
            tip = tr("crossbar: {0} {1} {2}\nblock: {3} {4} {5}").format(d, w, xname, own, w, block_pin_name(xname)).replace("  ", " ")
            if members and self.detail == "simple":
                tip = tr("{0}: AXI4-Lite connection ({1} signals)\n").format(xname, len(members)) + "\n".join(
                    f"  {m[0]:<6} {m[2]:<22} {m[1]}" for m in members)
            elif members:
                tip = tr("crossbar {0}, block {1}:\n").format(d, own) + "\n".join(
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
    left and MASTER0 of the interconnect behind it (whose extra masters sit below it). Each
    connection shows its AXI4-Lite channels or the HDL Designer REQ / RSP pair. Click an
    interconnect or a master to pick that interconnect, a slave to select it; double-click a
    bridge to pick its interconnect, any other slave to edit it in the Address Map Editor."""

    levelClicked = Signal(str)
    instClicked = Signal(str)
    instActivated = Signal(str)

    XW, SW, SWIRE = 190, 230, 84
    VGAP, XHEAD = 12, 30

    def __init__(self):
        super().__init__()
        self.project = None
        self.mode = "axi"
        self.level_sel = ""
        self.inst_sel = None
        self._items = []
        self._wires = []

    def set_detail(self, detail):
        if self.project is not None:
            self.set_project(self.project, detail, self.level_sel, self.inst_sel)

    def set_project(self, project, mode="simple", level_sel="", inst_sel=None):
        self.project, self.level_sel, self.inst_sel = project, level_sel, inst_sel
        self.mode = mode if mode in CHANNELS else "simple"
        span = (len(CHANNELS[self.mode]) - 1) * STEP[self.mode] + 16      # the lines of one connection
        self.LEAF_H, self.BRIDGE_H, self.MASTER_H = max(58, span), max(66, span), max(42, span)
        self.spec = project          # "something to draw" for the base class
        try:
            self._build()
            self.error = None
        except Exception as exc:  # noqa: BLE001 - keep the page working
            self.error = tr("Could not draw the system diagram: {0}").format(exc)
        self.update()

    # ------------------------------------------------------------ layout
    def _colx(self, depth):
        return MARGIN + depth * (self.SW + self.SWIRE + self.XW + self.SWIRE)

    def _build(self):
        from axil_studio.core import addrmap as A
        proj = self.project
        self._items = []          # (kind, rect, data)
        self._wires = []          # (from x, to x, y, port label)
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
        self._items.append(("x", xr, (bridge.name if bridge else "", A.xbar_module(proj, bridge),
                                      len(mrects), len(ports))))
        for i, r in enumerate(mrects):
            self._wires.append((r.right(), xr.left(), r.center().y(), f"M{i}", "m"))
        for j, py in enumerate(ports):
            self._wires.append((xr.right(), sx, py, f"S{j}", "s"))
        return xr.bottom()

    # ------------------------------------------------------------ paint
    def render_to(self, cv, t):
        bold = QFont(self.font())
        bold.setBold(True)
        small = theme.mono_font()
        small.setPixelSize(10)
        tiny = theme.mono_font()
        tiny.setPixelSize(8)
        base = QFont(self.font())
        cv.text(QRectF(MARGIN, 8, 1400, 20),
                tr("Display: {0} · requests flow left to right (master → interconnect → slave) · Sub-bus Bridge: a slave of the upper bus, MASTER0 of the interconnect behind it").format(MODE_TEXT[self.mode]), t["muted"], base,
                elide=False)
        # legend: one swatch per block kind
        x = MARGIN
        for kind, text in (("master", "master"), ("xbar", "interconnect"), ("ip", "IP"), ("ext", tr("external slave")),
                           ("bridge", "Sub-bus Bridge")):
            f, b, _ = theme.block_colors(kind, t)
            cv.rect(QRectF(x, 32, 16, 12), f, b, 1.2, 3)
            w = QFontMetrics(base).horizontalAdvance(text) + 6
            cv.text(QRectF(x + 22, 29, w, 18), text, t["muted"], base, elide=False)
            x += 22 + w + 16
        chans = CHANNELS[self.mode]
        step = STEP[self.mode]
        for a, b, y, label, side in self._wires:
            n = len(chans)
            for k, (name, d) in enumerate(chans):
                yy = y + (k - (n - 1) / 2) * step
                color = IN_COLOR if d > 0 else OUT_COLOR
                cv.line(QPointF(a, yy), QPointF(b, yy), color, 1.4 if self.mode.startswith("axi") else 2, d)
                if not name:
                    continue
                f = tiny if self.mode.startswith("axi") else small
                tw = QFontMetrics(f).horizontalAdvance(name) + 6
                lab = QRectF(a + (b - a) / 2 - tw / 2, yy - step / 2, tw, step)
                cv.rect(lab, t["diagram_bg"], None, 0, 2)          # the label cuts its line
                cv.text(lab, name, color, f, Qt.AlignmentFlag.AlignCenter, elide=False)
        self._blocks(cv, t, bold, small)
        for a, b, y, label, side in self._wires:
            if side == "m":
                cv.text(QRectF(b + 4, y - 8, 40, 16), label, t["muted"], small)
            else:
                cv.text(QRectF(a - 44, y - 8, 40, 16), label, t["muted"], small,
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _blocks(self, cv, t, bold, small):
        for kind, r, data in self._items:
            if kind == "x":
                self._xbar(cv, t, bold, small, r, *data)
            elif kind == "m":
                lvl, i, name, extra = data
                sel = lvl == self.level_sel
                f, b, tc = theme.block_colors("master", t)
                cv.rect(r, f, t["text"] if sel else b, 2.2 if sel else 1.4, 8)
                cv.text(r.adjusted(10, 0, -70, 0), name, tc, bold)
                cv.text(r.adjusted(0, 0, -10, 0), f"MASTER{i}", t["muted"], small,
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                where = tr("top bus") if not lvl else lvl
                self._hits.append((r, "m", lvl, tr("MASTER{0} · {1}\n{2} interconnect").format(i, name, where)
                                   + (tr("\n(extra master: reaches only this interconnect's slaves)") if extra else "")))
            else:
                inst, s, j, bad = data
                self._slave(cv, t, bold, small, r, kind == "b", inst, s, j, bad)

    def _xbar(self, cv, t, bold, small, r, lvl, module, nm, ns):
        sel = lvl == self.level_sel
        f, b, tc = theme.block_colors("xbar", t)
        cv.rect(r, f, t["text"] if sel else b, 3 if sel else 1.8, 10)
        head = QRectF(r.left(), r.top(), r.width(), self.XHEAD)
        cv.head(head, b)
        cv.text(head.adjusted(8, 0, -8, 0), module, "#FFFFFF", bold, Qt.AlignmentFlag.AlignCenter)
        cv.text(QRectF(r.left(), r.bottom() - 22, r.width(), 16), f"axilxbar · {nm}M × {ns}S", tc, small,
                Qt.AlignmentFlag.AlignCenter)
        self._hits.append((r, "x", lvl, tr("{0}\n{1} interconnect · {2} masters, {3} slaves\nClick: select").format(module, tr("top bus") if not lvl else lvl + tr(" behind"), nm, ns)))

    def _slave(self, cv, t, bold, small, r, is_bridge, inst, s, j, bad):
        from axil_studio.core import addrmap as A
        sel = inst.name == self.inst_sel or (is_bridge and inst.name == self.level_sel)
        f, b, tc = theme.block_colors("bridge" if is_bridge else "ext" if inst.external else "ip", t)
        cv.rect(r, f, t["danger"] if bad else t["text"] if sel else b, 2.5 if sel or bad else 1.4, 8)
        inner = r.adjusted(10, 5, -10, -4)
        cv.text(QRectF(inner.left(), inner.top(), inner.width() - 60, 18), inst.name, tc, bold)
        cv.text(QRectF(inner.left(), inner.top(), inner.width(), 18), f"SLAVE{j}", t["muted"], small,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rng = f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}"
        cv.text(QRectF(inner.left(), inner.top() + 19, inner.width(), 15), rng,
                t["danger"] if bad else t["muted"], small)
        if is_bridge:
            kind = A.BRIDGE_LABEL + (tr(" · own {0}").format(size_text(inst.self_window)) if inst.self_window else "")
        else:
            kind = tr("external") if inst.external else inst.ip
        cv.text(QRectF(inner.left(), inner.top() + 34, inner.width() - 60, 15), kind, t["muted"], small)
        cv.text(QRectF(inner.left(), inner.top() + 34, inner.width(), 15), size_text(s.window), t["muted"], small,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if is_bridge:
            tip = (tr("{0} · {1}\n{2}\nSLAVE{3} on the upper bus, MASTER0 of the interconnect behind it\nDouble-click: select the interconnect behind it").format(inst.name, A.BRIDGE_LABEL, rng, j))
        else:
            tip = tr("SLAVE{0} · {1}\n{2}\nDouble-click: edit in the Address Map Editor").format(j, inst.name, rng)
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
