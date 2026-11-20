"""Tool registry. Order here is the order on the home page and in the Tools menu (Ctrl+1 …)."""

from axil_studio.tools.address_map import AddressMapPage
from axil_studio.tools.debug_bridge import DebugBridgePage
from axil_studio.tools.interconnect import InterconnectPage
from axil_studio.tools.ip_library import IPLibraryPage
from axil_studio.tools.regmap import RegMapEditorPage
from axil_studio.tools.wrapper import WrapperPage

TOOLS = [RegMapEditorPage, WrapperPage, AddressMapPage, InterconnectPage, DebugBridgePage, IPLibraryPage]
TOOLS_BY_ID = {t.tool_id: t for t in TOOLS}
