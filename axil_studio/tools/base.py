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

    # ------------------------------------------------------------ other tools
    def open_tool(self, tool_id):
        """Open (or raise) another tool's window that shares this page's project; returns the window."""
        from PySide6.QtWidgets import QApplication
        from axil_studio.hub import HubWindow, ToolWindow
        hub = next((w for w in QApplication.topLevelWidgets() if isinstance(w, HubWindow) and w.ctx is self.ctx),
                   None)
        if hub is not None:
            return hub.open_tool_window(tool_id)
        for w in QApplication.topLevelWidgets():     # stand-alone launch: reuse an open window
            if isinstance(w, ToolWindow) and w.ctx is self.ctx and w.page.tool_id == tool_id and w.isVisible():
                w.showNormal()
                w.raise_()
                w.activateWindow()
                return w
        w = ToolWindow(self.ctx, tool_id, owned=False)
        self._windows = [x for x in getattr(self, "_windows", []) if x is not None] + [w]
        w.show()
        return w

    def edit_register_map(self, ip_name, reg=None, field=None):
        """Every register-map edit goes through the Register Map Editor."""
        w = self.open_tool("regmap")
        w.page.edit_project_ip(ip_name, reg, field)
        return w

    def edit_address_map(self, instance=None):
        """Every address edit goes through the Address Map Editor."""
        w = self.open_tool("addrmap")
        if instance:
            w.page.select_instance(instance)
        return w

    def pick_ip(self, title="IP seç", note=""):
        """Choose an IP in the IP Library (project IPs or any open library). A library IP is
        brought into the project first. Returns the project IP definition name or None."""
        from axil_studio.tools.ip_library import IPPickerDialog
        return IPPickerDialog.pick(self, self.ctx, title, note)

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
