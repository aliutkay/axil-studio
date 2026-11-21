"""Unexpected errors: logged to a file and shown to the user instead of being lost on a console.

install() sets sys.excepthook (exceptions in Qt slots end up there too) and threading.excepthook
(worker threads), routes Qt's own warnings to the log, and keeps the log in
    <settings folder>/logs/axil_studio.log   (see i18n.settings_path(); rotated, 1 MB x 3)
The dialog shows a short message, the details (traceback) and where the log is.
"""

import logging
import logging.handlers
import os
import sys
import threading
import traceback

from axil_studio import APP_NAME, i18n, version_text
from axil_studio.i18n import tr

log = logging.getLogger("axil_studio")
_showing = False


def log_dir():
    return os.path.join(os.path.dirname(i18n.settings_path()), "logs")


def log_path():
    return os.path.join(log_dir(), "axil_studio.log")


def _setup_logging():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        os.makedirs(log_dir(), exist_ok=True)
        h = logging.handlers.RotatingFileHandler(log_path(), maxBytes=1 << 20, backupCount=3, encoding="utf-8")
    except OSError:
        h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.info("%s %s started (Python %s, %s)", APP_NAME, version_text(), sys.version.split()[0], sys.platform)


def report(exc_type, exc, tb, where="main"):
    """Log an exception and, on the GUI thread, show it."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("unexpected error (%s thread)\n%s", where, text)
    try:
        from PySide6.QtCore import QThread, QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.__excepthook__(exc_type, exc, tb)
        return
    app = QApplication.instance()
    if app is None:
        sys.__excepthook__(exc_type, exc, tb)
        return
    if QThread.currentThread() is not app.thread():
        QTimer.singleShot(0, app, lambda: _show(exc_type, exc, text))
        return
    _show(exc_type, exc, text)


def _show(exc_type, exc, text):
    global _showing
    if _showing:                       # an error while the dialog is open: only logged
        return
    _showing = True
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        box = QMessageBox(QApplication.activeWindow())
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(tr("Unexpected error"))
        box.setText(tr("Something went wrong: {0}").format(f"{exc_type.__name__}: {exc}"))
        box.setInformativeText(tr("Your work is still open; save it if you can. The details are also in the log:\n{0}")
                               .format(log_path()))
        box.setDetailedText(f"{APP_NAME} {version_text()}\n\n{text}")
        copy = box.addButton(tr("Copy details"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is copy:
            QApplication.clipboard().setText(f"{APP_NAME} {version_text()}\n\n{text}")
    finally:
        _showing = False


def _thread_hook(args):
    report(args.exc_type, args.exc_value, args.exc_traceback, getattr(args.thread, "name", "worker"))


def _qt_messages(mode, context, message):
    from PySide6.QtCore import QtMsgType
    level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
             QtMsgType.QtWarningMsg: logging.WARNING}.get(mode, logging.ERROR)
    log.log(level, "Qt: %s", message)


def install():
    _setup_logging()
    sys.excepthook = report
    threading.excepthook = _thread_hook
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_messages)
    except ImportError:
        pass
