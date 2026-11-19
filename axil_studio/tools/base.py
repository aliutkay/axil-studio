"""Base class for a tool page. A page works the same inside the hub and in its own window."""

from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from axil_studio import theme, ui
from axil_studio.project import ProjectContext


class ToolPage(QWidget):
    tool_id = ""
    title = ""
    short_title = ""        # sidebar label
    subtitle = ""
    description = ""        # home page card text
    icon_name = "file"
    version = "0.0.1"       # each tool has its own version (stage: axil_studio.RELEASE_STAGE)
    legacy_script = None    # original Tkinter tool, reachable while features are ported

    def __init__(self, ctx: ProjectContext, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(28, 24, 28, 20)
        self.outer.setSpacing(18)
        self.header = ui.PageHeader(self.title, self.subtitle, self.icon_name)
        from axil_studio import version_text
        self.header.add(ui.Pill(version_text(self.version)))
        self.outer.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        self.outer.addLayout(self.body, 1)
        if self.legacy_script:
            self.header.add(ui.button("Klasik arayüz", "legacy", "ghost",
                                      lambda: ui.launch_legacy(self.legacy_script),
                                      "Henüz taşınmamış özellikler için eski Tkinter aracını açar"))
        ctx.replaced.connect(self.on_project_replaced)
        # bound methods (not lambdas) so the connections die with the page
        ctx.modified.connect(self._on_modified)
        theme.manager().changed.connect(self._on_theme)

    # ------------------------------------------------------------ hooks for subclasses
    def on_project_replaced(self):
        self.on_project_modified()

    def on_project_modified(self):
        pass

    def on_theme(self):
        pass

    def can_close(self):
        """Return False to keep the window open."""
        return True

    def shutdown(self):
        """The window is closing: release ports, threads ..."""

    def build_menus(self, menubar):
        """Add the tool's own menus to its window's menu bar."""

    # ------------------------------------------------------------ helpers
    def _on_modified(self, source):
        if source != self.tool_id:
            self.on_project_modified()

    def _on_theme(self):
        self.header.retint()
        ui.refresh_icons(self)
        self.on_theme()

    def status(self, text, ms=5000):
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text, ms)

    def error(self, title, exc):
        QMessageBox.critical(self, title, str(exc))
