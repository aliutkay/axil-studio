"""Tool registry. Order here is the order in the sidebar and on the home page."""

from axil_studio.tools.debug_bridge import DebugBridgePage
from axil_studio.tools.interconnect import InterconnectPage
from axil_studio.tools.ip_library import IPLibraryPage
from axil_studio.tools.wrapper import WrapperPage

TOOLS = [WrapperPage, InterconnectPage, DebugBridgePage, IPLibraryPage]
TOOLS_BY_ID = {t.tool_id: t for t in TOOLS}
