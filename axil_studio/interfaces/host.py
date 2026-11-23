"""The Debug Bridge side of IP interfaces: the local service an interface connects to, the
launcher that starts interface applications, and the settings of the interface folders.

Protocol (version 1): one JSON object per line (UTF-8) over TCP on 127.0.0.1. The interface sends
requests {"id": n, "op": ..., ...}; Studio answers {"id": n, "ok": true, "result": ...} or
{"id": n, "ok": false, "error": {"code": ..., "message": ...}} in the order the requests came, and
sends events {"event": ...} at any time. The first request must be "hello" with the session token
Studio passed to the process (environment AXIL_STUDIO_TOKEN). See interfaces/README.md.
"""

import json
import os
import secrets
import time

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QProcess, QProcessEnvironment, QSettings
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QTcpServer

from axil_studio import APP_VERSION, interfaces, theme
from axil_studio.core.model import hex32
from axil_studio.i18n import language, tr

ENV_PORT, ENV_TOKEN, ENV_LANG = "AXIL_STUDIO_PORT", "AXIL_STUDIO_TOKEN", "AXIL_STUDIO_LANG"
MAX_LINE = 4 * 1024 * 1024
MAX_UNTIL = 4096


def settings_folders():
    v = QSettings().value(interfaces.SETTINGS_KEY, []) or []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if x]


def set_settings_folders(folders):
    QSettings().setValue(interfaces.SETTINGS_KEY, list(folders))


def discover(ctx):
    """Interfaces visible from a project context: settings folders, open libraries, built-in."""
    libs = [lib.path for lib in getattr(ctx, "libraries", []) if getattr(lib, "path", None)]
    return interfaces.discover(settings_folders(), libs)


def theme_info():
    m = theme.manager()
    return {"dark": bool(m.dark), "tokens": dict(m.tokens)}


class RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Session:
    """One running interface application for one IP of the Debug Bridge."""

    def __init__(self, token, iface, group, ip_name):
        self.token, self.iface, self.group, self.ip_name = token, iface, group, ip_name
        self.process = None
        self.socket = None
        self.buf = b""
        self.stderr = b""
        self.pending_raise = False

    @property
    def key(self):
        return (self.group, self.ip_name)

    @property
    def running(self):
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning


class InterfaceService(QObject):
    """Local service of one Debug Bridge page. `host` is the DebugBridgePage."""

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._accept)
        self.sessions = {}              # token -> Session
        self.lock_owner = None          # Session holding the link
        host.connection_changed.connect(self._on_connection)
        host.ctx.modified.connect(self._on_project)
        host.ctx.replaced.connect(self._on_project)
        theme.manager().changed.connect(self._on_theme)

    # ------------------------------------------------------------ launching
    @property
    def port(self):
        return self.server.serverPort()

    def _listen(self):
        if not self.server.isListening() and not self.server.listen(QHostAddress.SpecialAddress.LocalHost, 0):
            raise RuntimeError(tr("The local interface service could not start: {0}").format(self.server.errorString()))

    def session_for(self, group, ip_name):
        return next((s for s in self.sessions.values() if s.key == (group, ip_name) and s.running), None)

    def open(self, iface, group, ip_name):
        """Start the interface for an IP, or bring its window to the front when it runs."""
        s = self.session_for(group, ip_name)
        if s is not None:
            if s.socket is not None:
                self._send(s, {"event": "raise"})
            else:
                s.pending_raise = True
            return s
        self._listen()
        token = secrets.token_hex(16)
        s = Session(token, iface, group, ip_name)
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert(ENV_PORT, str(self.port))
        env.insert(ENV_TOKEN, token)
        env.insert(ENV_LANG, language())
        env.insert("PYTHONPATH", os.pathsep.join([interfaces.SDK_DIR] + [x for x in [env.value("PYTHONPATH")] if x]))
        env.insert("PYTHONIOENCODING", "utf-8")
        p.setProcessEnvironment(env)
        p.setWorkingDirectory(iface.folder)
        p.readyReadStandardError.connect(lambda s=s: self._stderr(s))
        p.finished.connect(lambda code, _st, s=s: self._finished(s, code))
        p.errorOccurred.connect(lambda err, s=s: self._proc_error(s, err))
        s.process = p
        self.sessions[token] = s
        argv = iface.argv()
        self.host._log.emit("INFO", tr("Starting {0} for {1}: {2}").format(iface.label(language()), ip_name, " ".join(argv)))
        p.start(argv[0], argv[1:])
        return s

    def _stderr(self, s):
        s.stderr = (s.stderr + bytes(s.process.readAllStandardError()))[-8000:]

    def _proc_error(self, s, err):
        if err == QProcess.ProcessError.FailedToStart:
            self.host._log.emit("ERR", tr("{0} could not start: {1}").format(s.iface.label(language()),
                                                                             s.process.errorString()))
            self._drop(s)

    def _finished(self, s, code):
        tail = s.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        if code and not getattr(self, "_shutting_down", False):
            self.host._log.emit("ERR", tr("{0} closed with exit code {1}").format(s.iface.label(language()), code)
                                + ("\n" + "\n".join(tail) if tail else ""))
        self._drop(s)

    def _drop(self, s):
        if self.lock_owner is s:
            self._unlock()
        if s.socket is not None:
            s.socket.disconnectFromHost()
            s.socket = None
        self.sessions.pop(s.token, None)

    def shutdown(self, wait_ms=1500):
        """Studio closes: tell every interface, then end the ones that do not quit."""
        self._shutting_down = True
        socks = [s.socket for s in self.sessions.values() if s.socket is not None]
        for sock in socks:
            self._write(sock, {"event": "closing"})
        # let the event loop send them (a blocking wait would hold the data in the socket)
        def _pending():
            for sk in list(socks):
                try:
                    if sk.state() == QAbstractSocket.SocketState.ConnectedState and sk.bytesToWrite():
                        return True
                except RuntimeError:
                    socks.remove(sk)
            return False
        deadline = time.monotonic() + 0.5
        while _pending() and time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 20)
        deadline = time.monotonic() + wait_ms / 1000
        while any(s.running for s in self.sessions.values()) and time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 20)
            time.sleep(0.01)
        for s in list(self.sessions.values()):
            if s.process is not None and s.running:
                if not s.process.waitForFinished(10):
                    s.process.kill()
                    s.process.waitForFinished(500)
        self.sessions.clear()
        self.server.close()

    # ------------------------------------------------------------ connections
    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            state = {"session": None, "buf": b""}
            sock.readyRead.connect(lambda sock=sock, st=state: self._read(sock, st))
            sock.disconnected.connect(lambda sock=sock, st=state: self._closed(sock, st))

    def _closed(self, sock, state):
        s = state["session"]
        if s is not None:
            if self.lock_owner is s and s.socket is sock:
                self._unlock()
            if s.socket is sock:
                s.socket = None
        try:
            sock.deleteLater()
        except RuntimeError:
            pass

    def _read(self, sock, state):
        state["buf"] += bytes(sock.readAll())
        if len(state["buf"]) > MAX_LINE:
            sock.disconnectFromHost()
            return
        while b"\n" in state["buf"]:
            line, state["buf"] = state["buf"].split(b"\n", 1)
            if not line.strip():
                continue
            try:
                req = json.loads(line.decode("utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("a request is a JSON object")
            except ValueError as exc:
                self._write(sock, {"id": None, "ok": False, "error": {"code": "bad_request", "message": str(exc)}})
                continue
            self._handle(sock, state, req)

    def _write(self, sock, obj):
        if sock is None:
            return
        try:
            if sock.state() == QAbstractSocket.SocketState.ConnectedState:
                sock.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except RuntimeError:
            pass

    def _send(self, s, obj):
        self._write(s.socket, obj)

    def _reply(self, sock, rid, result=None, error=None):
        if error is None:
            self._write(sock, {"id": rid, "ok": True, "result": result})
        else:
            code = getattr(error, "code", "error")
            self._write(sock, {"id": rid, "ok": False, "error": {"code": code, "message": str(error)}})

    # ------------------------------------------------------------ requests
    def _handle(self, sock, state, req):
        rid, op = req.get("id"), req.get("op")
        s = state["session"]
        if op == "hello":
            s = self.sessions.get(str(req.get("token", "")))
            if s is None:
                self._reply(sock, rid, error=RequestError("bad_token", tr("Unknown session token")))
                sock.disconnectFromHost()
                return
            state["session"] = s
            s.socket = sock
            self._reply(sock, rid, self._hello(s))
            if s.pending_raise:
                s.pending_raise = False
                self._send(s, {"event": "raise"})
            return
        if s is None:
            self._reply(sock, rid, error=RequestError("no_session", tr("Send hello first")))
            return
        try:
            handler = getattr(self, "_op_" + str(op), None)
            if handler is None:
                raise RequestError("unknown_op", tr("Unknown request '{0}'").format(op))
            handler(s, sock, rid, req)
        except RequestError as exc:
            self._reply(sock, rid, error=exc)
        except (KeyError, TypeError, ValueError) as exc:
            self._reply(sock, rid, error=RequestError("bad_request", str(exc)))

    def _ip(self, s):
        ip = self.host.find_ip(s.group, s.ip_name)
        if ip is None:
            raise RequestError("ip_removed", tr("{0} is no longer in the address map").format(s.ip_name))
        return ip

    def ip_info(self, s):
        ip = self._ip(s)
        d = ip.to_dict()
        d.pop("regspec", None)
        d["base"] = ip.base
        d["group"] = s.group
        return d

    def _hello(self, s):
        return {"protocol": interfaces.PROTOCOL, "studio": APP_VERSION, "ip": self.ip_info(s),
                "connected": bool(self.host.link.connected), "port": self.host.port_name(),
                "lang": language(), "theme": theme_info(), "settings": self._settings(s)}

    def _addr(self, s, req):
        if "addr" in req:
            return int(req["addr"]) & 0xFFFFFFFF
        off = int(req["offset"])
        if off < 0:
            raise RequestError("bad_request", "negative offset")
        return (self._ip(s).base + off) & 0xFFFFFFFF

    def _check_link(self, s):
        if not self.host.link.connected:
            raise RequestError("not_connected", tr("The Debug Bridge is not connected"))
        if self.lock_owner is not None and self.lock_owner is not s:
            raise RequestError("busy", tr("Another IP interface holds the connection"))

    def _run(self, s, sock, rid, job, reads=(), quiet=False):
        """Run `job` on the Debug Bridge's request queue; reads: [(addr)] whose values `job` returns
        (recorded in the Debug Bridge views unless quiet)."""
        link = self.host.link

        def work():
            link.quiet = quiet
            try:
                return job(link)
            finally:
                link.quiet = False

        def ok(result):
            if not quiet:
                for addr, value in reads_of(result):
                    self.host.record(addr, value, s.iface.label(language()))
            self._reply(sock, rid, result if not isinstance(result, tuple) else result[0])

        def reads_of(result):
            return result[1] if isinstance(result, tuple) else []

        def fail(exc):
            self._reply(sock, rid, error=RequestError("bus", str(exc)))
        self.host.bus.run(work, ok, fail)

    def _op_read(self, s, sock, rid, req):
        self._check_link(s)
        addr = self._addr(s, req)
        self._run(s, sock, rid, lambda link: (lambda v: (v, [(addr, v)]))(link.read(addr)),
                  quiet=bool(req.get("quiet")))

    def _op_write(self, s, sock, rid, req):
        self._check_link(s)
        addr, value = self._addr(s, req), int(req["value"]) & 0xFFFFFFFF
        self._run(s, sock, rid, lambda link: link.write(addr, value), quiet=bool(req.get("quiet")))

    def _op_batch(self, s, sock, rid, req):
        self._check_link(s)
        steps = []
        for st in req["ops"]:
            kind = st.get("op")
            if kind not in ("read", "write"):
                raise RequestError("bad_request", f"batch step '{kind}'")
            steps.append((kind, self._addr(s, st), int(st.get("value", 0)) & 0xFFFFFFFF))

        def job(link):
            out, reads = [], []
            for kind, addr, value in steps:
                if kind == "read":
                    v = link.read(addr)
                    out.append(v)
                    reads.append((addr, v))
                else:
                    link.write(addr, value)
                    out.append(None)
            return out, reads
        self._run(s, sock, rid, job, quiet=bool(req.get("quiet")))

    def _op_read_until(self, s, sock, rid, req):
        """Read one address up to `max` times; stop after the first word with (word & mask) == value."""
        self._check_link(s)
        addr = self._addr(s, req)
        n = max(1, min(MAX_UNTIL, int(req.get("max", 1))))
        mask, want = int(req.get("mask", 0)), int(req.get("value", 0))

        def job(link):
            out = []
            for _ in range(n):
                v = link.read(addr)
                out.append(v)
                if mask and v & mask == want:
                    break
            return out, []
        self._run(s, sock, rid, job, quiet=bool(req.get("quiet", True)))

    def _op_lock(self, s, sock, rid, req):
        self._check_link(s)
        self.lock_owner = s
        self.host.set_exclusive(s)
        self._reply(sock, rid, None)

    def _op_unlock(self, s, sock, rid, req):
        if self.lock_owner is s:
            self._unlock()
        self._reply(sock, rid, None)

    def _unlock(self):
        self.lock_owner = None
        self.host.set_exclusive(None)

    def _op_ip(self, s, sock, rid, req):
        self._reply(sock, rid, self.ip_info(s))

    def _settings(self, s, name=None):
        d = (self.host.ctx.project.debug or {}).get("interfaces", {})
        return dict(d.get(name or s.ip_name, {}))

    def _peer_ips(self, s):
        """The other IPs of the same group that use the same interface."""
        if s.group == "project":
            ips = self.host.ctx.address_ips()
        else:
            ips = [ip for ip in (self.host.find_ip(s.group, n) for n in self._group_names(s.group)) if ip]
        return [ip for ip in ips if ip.name != s.ip_name and ip.panel == s.iface.id]

    def _group_names(self, group):
        return [ip.name for ip in self.host._extra()] if group == "extra" else []

    def _target(self, s, req):
        """The instance a settings request is for: this IP, or (with "ip") one of its peers."""
        name = req.get("ip") or s.ip_name
        if name != s.ip_name and name not in [p.name for p in self._peer_ips(s)]:
            raise RequestError("bad_request", tr("{0} is not an IP with the same interface").format(name))
        return name

    def _op_peers(self, s, sock, rid, req):
        out = []
        for ip in self._peer_ips(s):
            d = ip.to_dict()
            d.pop("regspec", None)
            d["base"] = ip.base
            d["settings"] = self._settings(s, ip.name)
            out.append(d)
        self._reply(sock, rid, out)

    def _op_address_map(self, s, sock, rid, req):
        """Every entry of the address map (project IPs) with its base, window and register map:
        what an interface needs to resolve names (e.g. the cmd_seq assembler)."""
        out = []
        if s.group == "project":
            from axil_studio.core import addrmap
            proj = self.host.ctx.project
            for inst, parent in proj.walk():
                d = proj.definition_of(inst)
                kind = ("cmd_seq" if inst.sequencer else "subsystem" if inst.bridge
                        else "external" if inst.external else "ip")
                out.append({"name": inst.name, "kind": kind, "ip": d.name if d else "",
                            "base": inst.base, "window": addrmap.window_of(proj, inst),
                            "parent": parent.name if parent is not None else "",
                            "registers": [r.to_dict() for r in d.registers] if d else []})
        else:
            for ip in [self._ip(s)] + self._peer_ips(s):
                out.append({"name": ip.name, "kind": "ip", "ip": ip.ref or ip.name, "base": ip.base,
                            "window": ip.span, "parent": "", "registers": [r.to_dict() for r in ip.registers]})
        self._reply(sock, rid, out)

    def _op_settings_get(self, s, sock, rid, req):
        self._reply(sock, rid, self._settings(s, self._target(s, req)))

    def _op_settings_set(self, s, sock, rid, req):
        values = req["values"]
        if not isinstance(values, dict):
            raise RequestError("bad_request", "values must be an object")
        name = self._target(s, req)
        cur = self._settings(s, name)
        if any(cur.get(k) != v for k, v in values.items()):
            cur.update(values)
            for k in [k for k, v in values.items() if v is None]:
                cur.pop(k, None)              # None removes a value
            dbg = dict(self.host.ctx.project.debug or {})
            ifs = dict(dbg.get("interfaces", {}))
            ifs[name] = cur
            dbg["interfaces"] = ifs
            self.host.ctx.project.debug = dbg
            self.host.ctx.mark_modified("interface")
        self._reply(sock, rid, None)

    def _op_show_register(self, s, sock, rid, req):
        self._ip(s)
        self.host.show_register(s.group, s.ip_name, req.get("register"))
        self._reply(sock, rid, None)

    def _op_add_config(self, s, sock, rid, req):
        ip = self._ip(s)
        rows = []
        for r in req["rows"]:
            if ip.get_register(str(r["register"])) is None:
                raise RequestError("bad_request", tr("{0} has no register {1}").format(ip.name, r["register"]))
            rows.append((s.group, ip.name, str(r["register"]), int(r["value"])))
        self.host.add_config_rows(rows)
        self._reply(sock, rid, None)

    def _op_status(self, s, sock, rid, req):
        self.host.status(f"{s.iface.label(language())}: {req.get('text', '')}")
        self._reply(sock, rid, None)

    def _op_log(self, s, sock, rid, req):
        self.host._log.emit("INFO", f"{s.iface.label(language())}: {req.get('text', '')}")
        self._reply(sock, rid, None)

    # ------------------------------------------------------------ events
    def _broadcast(self, make):
        for s in list(self.sessions.values()):
            if s.socket is not None:
                try:
                    self._send(s, make(s))
                except RequestError:
                    pass

    def _on_connection(self, on):
        if not on and self.lock_owner is not None:
            self._unlock()
        self._broadcast(lambda s: {"event": "connection", "connected": bool(on), "port": self.host.port_name()})

    def _on_project(self, _source=None):
        def ev(s):
            try:
                return {"event": "ip", "ip": self.ip_info(s)}
            except RequestError:
                return {"event": "ip_removed"}
        self._broadcast(ev)

    def _on_theme(self):
        self._broadcast(lambda s: {"event": "theme", "theme": theme_info()})


def describe(iface, lang):
    """One line about an interface for tooltips and lists."""
    return f"{iface.label(lang)} {iface.version}".strip() + f" · {iface.folder}"


# ---------------------------------------------------------------- settings dialog
def folders_dialog(parent, ctx):
    """Edit the IP interface folders and see which interfaces are found."""
    from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QListWidget, QTreeWidget,
                                   QTreeWidgetItem, QVBoxLayout)
    from axil_studio import ui
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("IP interface folders"))
    dlg.resize(760, 520)
    lay = QVBoxLayout(dlg)
    lay.addWidget(ui.note(tr("AXIL Studio looks for IP interfaces (folders with an axil-interface.json) in these folders "
                             "first, then next to the open IP libraries, then in its own interfaces folder.")))
    lst = QListWidget()
    for f in settings_folders():
        lst.addItem(f)
    lay.addWidget(lst)
    row = QHBoxLayout()
    found = QTreeWidget()
    found.setHeaderLabels([tr("Interface"), "Id", tr("Version"), tr("Where")])
    found.setRootIsDecorated(False)

    def folders():
        return [lst.item(i).text() for i in range(lst.count())]

    def refresh():
        found.clear()
        libs = [lib.path for lib in getattr(ctx, "libraries", []) if getattr(lib, "path", None)]
        its, errors = interfaces.discover(folders(), libs)
        where = {"settings": tr("settings folder"), "library": tr("library folder"), "builtin": "AXIL Studio"}
        for it in sorted(its.values(), key=lambda x: x.label(language()).lower()):
            item = QTreeWidgetItem([it.label(language()), it.id, it.version, f"{where.get(it.source, it.source)} · {it.folder}"])
            item.setToolTip(3, it.folder + ("\n" + tr("Also found (not used): {0}").format(", ".join(it.shadowed))
                                            if it.shadowed else ""))
            found.addTopLevelItem(item)
        for e in errors:
            item = QTreeWidgetItem([tr("Error"), "", "", e])
            found.addTopLevelItem(item)
        for c in range(4):
            found.resizeColumnToContents(c)

    def add():
        d = QFileDialog.getExistingDirectory(dlg, tr("IP interface folder"))
        if d and d not in folders():
            lst.addItem(d)
            refresh()

    def remove():
        for it in lst.selectedItems():
            lst.takeItem(lst.row(it))
        refresh()
    row.addWidget(ui.button(tr("Add folder…"), "plus", "ghost", add))
    row.addWidget(ui.button(tr("Remove"), None, "ghost", remove))
    row.addStretch(1)
    lay.addLayout(row)
    lay.addWidget(ui.label(tr("Interfaces found"), "section"))
    lay.addWidget(found, 1)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    refresh()
    if dlg.exec() == QDialog.DialogCode.Accepted:
        set_settings_folders(folders())
        return True
    return False


def ip_tooltip_line(ctx, panel_id):
    """Tooltip line for an IP that names an interface ("" when none)."""
    if not panel_id:
        return ""
    its, _ = discover(ctx)
    it = its.get(panel_id)
    if it is None:
        return "\n" + tr("IP interface: {0} (not found)").format(panel_id)
    return "\n" + tr("IP interface: {0} (UART-to-AXI Debug Bridge)").format(it.label(language()))


__all__ = ["InterfaceService", "folders_dialog", "discover", "settings_folders", "set_settings_folders",
           "ip_tooltip_line", "describe", "hex32"]
