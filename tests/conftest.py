"""Shared settings of the test suite.

The suite is split at the HDL spec (axil_studio/core/hdlspec.py), the only input of the Verilog generators:

    tests/part1   input -> HDL spec   tools and command line build the right HDL spec (tests/data/hdl)
    tests/part2   HDL spec -> Verilog  the expected files (tests/golden), and they work in simulation

Golden files: tests/golden holds the Verilog generated from the HDL specs in tests/data/hdl. A change of the
generators shows up as a difference; when it is intended, refresh them with
    pytest --update-golden
and review the diff in git.
"""

import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
GOLDEN = os.path.join(ROOT, "tests", "golden")
HDL = os.path.join(ROOT, "hdl")
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AXIL_STUDIO_LANG"] = os.environ.get("AXIL_TEST_LANG", "en")          # messages checked by the tests are the English ones

HAS_IVERILOG = bool(shutil.which("iverilog") and shutil.which("vvp"))
needs_iverilog = pytest.mark.skipif(not HAS_IVERILOG, reason="iverilog / vvp not installed")


def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", help="rewrite tests/golden from the current generators")


@pytest.fixture
def update_golden(request):
    return request.config.getoption("--update-golden")


def check_golden(files, subdir, update):
    """Compare {name: text} with tests/golden/<subdir>/<name> (or rewrite them)."""
    folder = os.path.join(GOLDEN, subdir)
    if update:
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        os.makedirs(folder)
        for name, text in files.items():
            with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return
    assert os.path.isdir(folder), f"no golden files for {subdir}: run pytest --update-golden"
    assert sorted(os.listdir(folder)) == sorted(files), f"{subdir}: other files than the golden ones"
    for name, text in files.items():
        with open(os.path.join(folder, name), encoding="utf-8", newline="") as fh:
            want = fh.read()
        assert text == want, f"{subdir}/{name} differs from the golden file (pytest --update-golden if intended)"


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
    QCoreApplication.setOrganizationName("AXILStudioTests")
    QCoreApplication.setApplicationName("tests")
    app = QApplication.instance() or QApplication([])
    from axil_studio import theme
    theme.install(app)
    return app


@pytest.fixture
def no_dialogs(monkeypatch):
    """Modal message boxes would block the tests: record them instead."""
    from PySide6.QtWidgets import QMessageBox
    from axil_studio import ui
    shown = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[2]))))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    return shown


def pytest_sessionfinish(session, exitstatus):
    """Delete the Qt widgets the tests left behind while the QApplication still exists: destroyed
    at interpreter exit (after it), they crash the process now and then."""
    import gc
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
