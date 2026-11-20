"""Small line icons drawn from inline SVG and tinted with the current theme colour."""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from axil_studio import theme

_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
         'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">')

PATHS = {
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    # chip with pins: Slave Interface Wrapper
    "wrapper": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/>'
               '<path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    # table with a bit row: Register Map Editor
    "regmap": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 9v11M15 9v11"/>',
    # stacked address blocks with a pointer: Address Map Editor
    "addrmap": '<rect x="8" y="3" width="13" height="5" rx="1"/><rect x="8" y="10" width="13" height="5" rx="1"/>'
               '<rect x="8" y="17" width="13" height="4" rx="1"/><path d="M2 12.5h4M4.5 10.5l2 2-2 2"/>',
    # nodes on a bus: Interconnect
    "interconnect": '<circle cx="5" cy="5" r="2.2"/><circle cx="5" cy="19" r="2.2"/><circle cx="19" cy="5" r="2.2"/>'
                    '<circle cx="19" cy="12" r="2.2"/><circle cx="19" cy="19" r="2.2"/>'
                    '<path d="M7.2 5H11v14H7.2M11 12h5.8M11 5h5.8M11 19h5.8"/>',
    # terminal prompt: Debug Bridge
    "debug": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9l3 3-3 3M12.5 15H17"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "save": '<path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8M7 21v-7h10v7"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
           'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "auto": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17" /><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="{c}"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "plug": '<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.6-4.5L3 9"/><path d="M3 4v5h5"/><path d="M4 13a8 8 0 0 0 14.6 4.5L21 15"/>'
               '<path d="M21 20v-5h-5"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "read": '<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>',
    "write": '<path d="M12 20V8M7 13l5-5 5 5M5 4h14"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "alert": '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    # stacked books: IP Library
    "library": '<rect x="4" y="4" width="4" height="16" rx="1"/><rect x="10" y="4" width="4" height="16" rx="1"/>'
               '<path d="M16.5 5.2l3.6-1 3 15.4-3.6 1z"/>',
}


def svg(name, color):
    return _HEAD.format(c=color) + PATHS[name].format(c=color) + "</svg>"


def pixmap(name, size=18, color=None):
    color = color or theme.manager().tokens["text"]
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name, color_token="text", size=18):
    """Icon tinted with a theme token; normal and checked (accent) states."""
    t = theme.manager().tokens
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, t[color_token]), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, t["accent"]), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, size, t["muted"]), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic
