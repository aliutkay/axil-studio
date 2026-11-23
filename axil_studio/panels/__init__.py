"""IP interfaces: Python windows made for one kind of IP, opened from the UART-to-AXI Debug Bridge.

An IP definition names its interface in its register map (`IPBlock.panel`, kept in the project and
in IP libraries). When such an IP is selected in the Debug Bridge, *IP interface* opens the window:
it works through the Debug Bridge's own connection and request queue (the serial port is not
opened twice), puts what it reads into the Debug Bridge's views and can take the user back to a
register there.

An interface is registered with `register(PanelInfo(...))`; `factory(host, group, ip_name)` builds
its window. `host` is the Debug Bridge page; an interface uses only this part of it:
    host.ctx                       project context
    host.link / host.bus           hexbus link and its one-worker request queue (bus.run(fn, ok, err))
    host.link.connected            connection state; host.connection_changed(bool) signal
    host.find_ip(group, name)      the IPBlock (name, base, registers) as the Debug Bridge sees it
    host.record(addr, value, src)  a value read from the board (shown in every Debug Bridge view)
    host.show_register(group, ip, reg)   select a register in the Debug Bridge and raise it
    host.set_exclusive(owner|None) hold the link (live watch stops, Connect is locked)
    host.add_config_rows(rows)     add (group, ip, reg, value) rows to the Debug Bridge config list
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass
class PanelInfo:
    id: str
    title: str
    desc: str
    factory: Callable          # (host, group, ip_name) -> QWidget (a top-level window)


_PANELS: Dict[str, PanelInfo] = {}
_loaded = False


def register(info: PanelInfo):
    _PANELS[info.id] = info


def _load():
    global _loaded
    if not _loaded:
        _loaded = True
        from . import uart_phy_panel  # noqa: F401  (registers itself)


def get(panel_id) -> Optional[PanelInfo]:
    if not panel_id:
        return None
    _load()
    return _PANELS.get(panel_id)


def available() -> List[PanelInfo]:
    _load()
    return sorted(_PANELS.values(), key=lambda p: p.title.lower())
