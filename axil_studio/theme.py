"""Colour tokens and the Qt style sheet.

Fusion is used on every platform so Windows and macOS render the same widgets;
the palette and style sheet below give it a flat, modern look. The mode is
"system" (follows the OS light/dark setting), "light" or "dark".
"""

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

# Colours follow the corporate palette: the blue (R0 G48 B135) and its 20 % tints,
# text in 90 % black, the orange support colour used only for rare highlights.
BLUE = "#003087"
BLUE_TINTS = {100: "#003087", 80: "#33599F", 60: "#6683B7", 40: "#99ACCF", 20: "#CCD6E7"}
ORANGE = "#F7931D"

LIGHT = {
    "bg": "#F2F4F8", "surface": "#FFFFFF", "surface2": "#EEF1F7", "sidebar": "#E8ECF4",
    "border": "#D3DAE6", "text": "#1A1A1A", "muted": "#5A6475", "accent": BLUE,
    "accent_hover": "#33599F", "accent_text": "#FFFFFF", "accent_soft": "#E5EAF3",
    "primary": BLUE, "primary_hover": "#33599F",
    "hover": "#E6EAF2", "selection": "#CCD6E7", "danger": "#C62828", "success": "#2E7D32",
    "warning": ORANGE, "highlight": ORANGE, "input": "#FFFFFF",
}
DARK = {   # black / graphite, not navy; the corporate blue stays on buttons and accents
    "bg": "#0B0B0D", "surface": "#151518", "surface2": "#1C1C20", "sidebar": "#111114",
    "border": "#2D2D33", "text": "#F2F2F4", "muted": "#9C9CA6", "accent": "#99ACCF",
    "accent_hover": "#CCD6E7", "accent_text": "#FFFFFF", "accent_soft": "#202634",
    "primary": "#1F4C9C", "primary_hover": "#33599F",
    "hover": "#232328", "selection": "#2C3A57", "danger": "#EF6B6B", "success": "#5CBF73",
    "warning": ORANGE, "highlight": ORANGE, "input": "#101013",
}
# Field-kind colours are their own signal colours (not the corporate palette, which is for
# menus and buttons): blue control, green status, amber pulse, red event flag, grey constant.
KIND_LIGHT = {"RW": "#3B82F6", "RO": "#22A55B", "PULSE": "#D99A00", "W1C": "#E5484D", "CONST": "#8B95A1"}
KIND_DARK = KIND_LIGHT


def kind_color(kind):
    return (KIND_DARK if manager().dark else KIND_LIGHT).get(kind, BLUE)


def ink_on(hex_color):
    """Readable text colour on top of a fill."""
    c = QColor(hex_color)
    return "#0A1A3A" if (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 150 else "#FFFFFF"


# Calm, professional text faces: IBM Plex Sans when bundled (axil_studio/fonts) or installed,
# otherwise the platform UI font (Segoe UI on Windows, San Francisco on macOS).
UI_FONTS = ("IBM Plex Sans",)
MONO_FONTS = ("IBM Plex Mono",)
MODES = ("system", "light", "dark")
MODE_LABELS = {"system": "Sistem", "light": "Açık", "dark": "Koyu"}

QSS = """
* {{ outline: none; }}
QWidget {{ color: {text}; font-size: 13px; {font_rule} }}
QMainWindow, QDialog, #Page, QStackedWidget > QWidget {{ background: {bg}; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 4px 6px; }}

/* ---------------------------------------------------------------- sidebar */
#Sidebar {{ background: {sidebar}; border-right: 1px solid {border}; }}
#Brand {{ font-size: 16px; font-weight: 700; }}
#BrandSub {{ color: {muted}; font-size: 11px; }}
#NavButton {{
    text-align: left; padding: 8px 10px; border: none; border-radius: 8px;
    background: transparent; color: {muted}; font-weight: 500;
}}
#NavButton:hover {{ background: {hover}; color: {text}; }}
#NavButton:checked {{ background: {accent_soft}; color: {accent}; font-weight: 600; }}
#SidebarFooter {{ color: {muted}; font-size: 11px; }}

/* ---------------------------------------------------------------- text roles */
QLabel[role="title"] {{ font-size: 20px; font-weight: 700; }}
QLabel[role="subtitle"] {{ color: {muted}; font-size: 13px; }}
QLabel[role="section"] {{ color: {muted}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
QLabel[role="muted"] {{ color: {muted}; }}
QLabel[role="cardTitle"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: "{mono}"; }}
QLabel[role="pill"] {{ border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600;
    background: {surface2}; color: {muted}; border: 1px solid {border}; }}
QLabel[role="pill"][state="ok"] {{ background: transparent; color: {success}; border-color: {success}; }}
QLabel[role="pill"][state="err"] {{ background: transparent; color: {danger}; border-color: {danger}; }}
QLabel[role="pill"][state="accent"] {{ background: {accent_soft}; color: {accent}; border-color: {accent_soft}; }}
QLabel[role="pill"][state="warn"] {{ background: transparent; color: {highlight}; border-color: {highlight}; }}

/* ---------------------------------------------------------------- action bar */
#ActionBar {{ background: {surface}; border: 1px solid {border}; border-radius: 10px; }}
#ActionBar QPushButton {{ background: transparent; border-color: transparent; padding: 5px 10px; }}
#ActionBar QPushButton:hover {{ background: {hover}; }}
#ActionSep {{ background: {border}; min-width: 1px; max-width: 1px; margin: 6px 4px; }}

/* ---------------------------------------------------------------- cards */
#Card {{ background: {surface}; border: 1px solid {border}; border-radius: 12px; }}
#Card[hover="true"]:hover {{ border-color: {accent}; }}
#IconTile {{ background: {accent_soft}; border-radius: 10px; }}
#Divider {{ background: {border}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------------------------------------------------------------- buttons */
QPushButton, QToolButton {{
    background: {surface}; border: 1px solid {border}; border-radius: 7px;
    padding: 6px 12px; min-height: 18px;
}}
QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {selection}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {muted}; background: {surface2}; }}
QPushButton[variant="primary"] {{ background: {primary}; border-color: {primary}; color: {accent_text}; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {primary_hover}; border-color: {primary_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {accent_soft}; border-color: {accent_soft}; color: {muted}; }}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{ background: {hover}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {accent}; padding: 2px 0; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---------------------------------------------------------------- inputs */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {input}; border: 1px solid {border}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {selection}; selection-color: {text};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {accent}; }}
QLineEdit:read-only {{ background: {surface2}; }}
QLineEdit[mono="true"], QPlainTextEdit[mono="true"] {{ font-family: "{mono}"; }}
QComboBox QAbstractItemView {{
    background: {surface}; border: 1px solid {border}; selection-background-color: {selection};
    selection-color: {text}; padding: 4px;
}}
QCheckBox {{ spacing: 7px; }}

/* ---------------------------------------------------------------- views */
QTreeView, QTableView, QListView {{
    background: {surface}; alternate-background-color: {surface2}; border: 1px solid {border};
    border-radius: 8px; gridline-color: {border};
    selection-background-color: {selection}; selection-color: {text};
}}
QTreeView::item, QTableView::item, QListView::item {{ padding: 4px 2px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {hover}; }}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{ background: {selection}; color: {text}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {surface2}; color: {muted}; border: none; border-bottom: 1px solid {border};
    padding: 6px 8px; font-size: 11px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {surface2}; border: none; }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {surface}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; font-weight: 600; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {border}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenuBar {{ background: {bg}; border-bottom: 1px solid {border}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {hover}; }}
QMenu {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {selection}; color: {text}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}

QStatusBar {{ background: {sidebar}; color: {muted}; border-top: 1px solid {border}; font-size: 12px; }}
QStatusBar::item {{ border: none; }}
"""


def ui_family():
    """First installed family of UI_FONTS; fonts dropped into axil_studio/fonts are loaded first."""
    import glob
    import os
    for path in glob.glob(os.path.join(os.path.dirname(__file__), "fonts", "*.[ot]tf")):
        QFontDatabase.addApplicationFont(path)
    families = set(QFontDatabase.families())
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    return next((f for f in UI_FONTS if f in families), system)


_UI_FAMILY = None


def mono_family():
    families = set(QFontDatabase.families())
    return next((f for f in MONO_FONTS if f in families),
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())


def mono_font(point_delta=0):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setFamily(mono_family())
    if point_delta:
        f.setPointSizeF(f.pointSizeF() + point_delta)
    return f


class ThemeManager(QObject):
    """Applies the palette + style sheet and re-applies it when the mode or OS scheme changes."""

    changed = Signal()

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.app = app
        self.mode = QSettings().value("theme/mode", "system")
        if self.mode not in MODES:
            self.mode = "system"
        app.setStyle(QStyleFactory.create("Fusion"))
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.mode == "system" and self.apply())
        self.apply()

    @property
    def dark(self):
        if self.mode == "system":
            hints = QGuiApplication.styleHints()
            return hasattr(hints, "colorScheme") and hints.colorScheme() == Qt.ColorScheme.Dark
        return self.mode == "dark"

    @property
    def tokens(self):
        return DARK if self.dark else LIGHT

    def color(self, name):
        return QColor(self.tokens[name])

    def set_mode(self, mode):
        self.mode = mode
        QSettings().setValue("theme/mode", mode)
        self.apply()

    def cycle(self):
        self.set_mode(MODES[(MODES.index(self.mode) + 1) % len(MODES)])

    def apply(self):
        t = self.tokens
        pal = QPalette()
        roles = {
            QPalette.ColorRole.Window: t["bg"], QPalette.ColorRole.WindowText: t["text"],
            QPalette.ColorRole.Base: t["input"], QPalette.ColorRole.AlternateBase: t["surface2"],
            QPalette.ColorRole.Text: t["text"], QPalette.ColorRole.Button: t["surface"],
            QPalette.ColorRole.ButtonText: t["text"], QPalette.ColorRole.Highlight: t["accent"],
            QPalette.ColorRole.HighlightedText: t["accent_text"], QPalette.ColorRole.ToolTipBase: t["surface"],
            QPalette.ColorRole.ToolTipText: t["text"], QPalette.ColorRole.PlaceholderText: t["muted"],
            QPalette.ColorRole.Link: t["accent"], QPalette.ColorRole.Mid: t["border"],
            QPalette.ColorRole.Light: t["surface"], QPalette.ColorRole.Dark: t["border"],
        }
        for role, value in roles.items():
            pal.setColor(role, QColor(value))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["muted"]))
        self.app.setPalette(pal)
        font_rule = f'font-family: "{_UI_FAMILY}";' if _UI_FAMILY in UI_FONTS else ""   # else platform default
        self.app.setStyleSheet(QSS.format(mono=mono_family(), font_rule=font_rule, **t))
        self.changed.emit()


_manager = None


def install(app: QApplication) -> ThemeManager:
    global _manager, _UI_FAMILY
    _UI_FAMILY = ui_family()
    font = QFont(_UI_FAMILY) if _UI_FAMILY in UI_FONTS else QFont(app.font())
    font.setPointSizeF(max(font.pointSizeF(), 10.0))
    app.setFont(font)
    _manager = ThemeManager(app)
    return _manager


def manager() -> ThemeManager:
    return _manager


def repolish(widget):
    """Re-evaluate the style sheet after a dynamic property change."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
