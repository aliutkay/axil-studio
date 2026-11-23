"""Look of the IP interface: AXIL Studio sends its colour tokens (light / dark) with the hello
answer and on every theme change, so the interface looks like the rest of the tools. Small widget
helpers (card, pill, buttons) and the translation table are here too."""

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

HERE = os.path.dirname(os.path.abspath(__file__))
FALLBACK = {   # used until Studio's tokens arrive (light theme)
    "bg": "#F2F4F8", "surface": "#FFFFFF", "surface2": "#EEF1F7", "border": "#D3DAE6", "text": "#1A1A1A",
    "muted": "#5A6475", "accent": "#003087", "accent_text": "#FFFFFF", "accent_soft": "#E5EAF3",
    "primary": "#003087", "primary_hover": "#33599F", "hover": "#E6EAF2", "selection": "#CCD6E7",
    "danger": "#C62828", "success": "#2E7D32", "warning": "#F7931D", "input": "#FFFFFF",
    "row": "#F6F7FA", "row_alt": "#ECEFF5",
}
_tokens = dict(FALLBACK)
_texts = {}


# ---------------------------------------------------------------- translations
def use_language(lang):
    """Load texts_<lang>.json (English is the source language)."""
    global _texts
    _texts = {}
    path = os.path.join(HERE, f"texts_{lang}.json")
    if lang != "en" and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            _texts = json.load(fh)


def tr(text):
    return _texts.get(text, text)


# ---------------------------------------------------------------- colours
def tokens():
    return _tokens


def color(name):
    return QColor(_tokens.get(name, FALLBACK.get(name, "#000000")))


def mono_font(delta=0):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(max(7.0, f.pointSizeF() + delta))
    return f


def apply(app, theme=None):
    """Style the application from Studio's theme ({"dark": bool, "tokens": {...}})."""
    global _tokens
    if theme and theme.get("tokens"):
        _tokens = dict(FALLBACK, **theme["tokens"])
    t = _tokens
    app.setStyleSheet(f"""
QWidget {{ color: {t['text']}; }}
QWidget#Page, QDialog {{ background: {t['bg']}; }}
QFrame#Card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
QLabel[role="title"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="cardTitle"] {{ font-weight: 600; font-size: 13px; }}
QLabel[role="muted"] {{ color: {t['muted']}; }}
QLabel[role="mono"] {{ font-family: monospace; }}
QLabel[role="pill"] {{ border: 1px solid {t['border']}; border-radius: 9px; padding: 2px 9px; color: {t['muted']};
    font-size: 11px; font-weight: 600; }}
QLabel[role="pill"][state="ok"] {{ color: {t['success']}; border-color: {t['success']}; }}
QLabel[role="pill"][state="err"] {{ color: {t['danger']}; border-color: {t['danger']}; }}
QLabel[role="pill"][state="warn"] {{ color: {t['warning']}; border-color: {t['warning']}; }}
QLabel[role="pill"][state="accent"] {{ color: {t['accent']}; border-color: {t['accent']}; }}
QPushButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:disabled {{ color: {t['muted']}; }}
QPushButton[variant="primary"] {{ background: {t['primary']}; color: {t['accent_text']}; border-color: {t['primary']};
    font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {t['primary_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background: {t['border']}; border-color: {t['border']}; }}
QPushButton[variant="ghost"] {{ background: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {t['hover']}; }}
QLineEdit, QPlainTextEdit {{ background: {t['input']}; border: 1px solid {t['border']};
    border-radius: 6px; padding: 3px 6px; selection-background-color: {t['selection']}; selection-color: {t['text']}; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; selection-background-color: {t['selection']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; color: {t['muted']}; border: none; }}
QTabBar::tab:selected {{ color: {t['accent']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
QTableWidget {{ background: {t['row']}; alternate-background-color: {t['row_alt']}; border: 1px solid {t['border']};
    border-radius: 6px; gridline-color: {t['border']}; }}
QHeaderView::section {{ background: {t['surface2']}; border: none; padding: 5px; color: {t['muted']}; font-weight: 600; }}
QCheckBox, QRadioButton {{ spacing: 6px; }}
""")


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


# ---------------------------------------------------------------- widgets
def label(text="", role=None, wrap=False):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    return w


def button(text, variant=None, slot=None, tip=None):
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, margins=16, spacing=10):
        super().__init__()
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins, margins, margins, margins)
        self.lay.setSpacing(spacing)
        if title:
            self.lay.addWidget(label(title, "cardTitle"))
            if subtitle:
                self.lay.addWidget(label(subtitle, "muted", wrap=True))


class Pill(QLabel):
    def __init__(self, text="", state=""):
        super().__init__(text)
        self.setProperty("role", "pill")
        self.set(text, state)

    def set(self, text, state=""):
        self.setText(text)
        self.setProperty("state", state)
        self.setVisible(bool(text))
        repolish(self)


def hline(*widgets, stretch=True):
    row = QHBoxLayout()
    row.setSpacing(8)
    for w in widgets:
        if isinstance(w, int):
            row.addSpacing(w)
        elif w == "stretch":
            row.addStretch(1)
        else:
            row.addWidget(w)
    if stretch:
        row.addStretch(1)
    return row


def confirm(parent, title, text, ok):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


def bold(f: QFont):
    f.setBold(True)
    return f
