"""dos_ext.mcp_tools — an example third-party MCP tool (the `dos.mcp_tools` seam).

This is the copy-me demonstration that a THIRD PARTY can ADD a tool/verb to the DOS MCP
server from their OWN pip package, with no fork. `register(mcp)` is handed the running
FastMCP server and registers its own tool(s) — getting the SAME call-deadline +
deep-answer wrapping the built-in syscall tools get, because it calls the server's patched
`mcp.tool`. It is registered under the `dos.mcp_tools` entry-point group in this package's
`pyproject.toml`:

    [project.entry-points."dos.mcp_tools"]
    acme = "dos_ext.mcp_tools:register"

so once `dos-kernel[mcp]` is installed and this package is `pip install`ed, the `dos` MCP
server a host talks to exposes `acme_lane_hint` alongside the built-in `dos_*` tools. The
seam is ADDITIVE — a plugin adds a verb, never replaces a built-in.
"""

from __future__ import annotations


def register(mcp) -> None:
    """Register this package's MCP tool(s) on the DOS server's FastMCP ``mcp``.

    ``mcp.tool`` is the SAME decorator the built-in tools use (it carries the per-call
    deadline + the deep-answer link), so a tool registered here behaves like a native one.
    """

    @mcp.tool()
    def acme_lane_hint(area: str = "") -> dict:
        """Suggest which acme lane an area of work belongs to (a toy domain tool).

        A real third-party tool would do something domain-specific; this one just maps a
        free-text area onto the acme driver's lanes, to show the seam end-to-end.
        """
        area_l = (area or "").lower()
        if any(k in area_l for k in ("app", "ios", "android", "mobile")):
            lane = "mobile"
        elif any(k in area_l for k in ("service", "infra", "cloud", "backend")):
            lane = "cloud"
        elif any(k in area_l for k in ("release", "deploy", "ship", "version")):
            lane = "ship"
        else:
            lane = "global"
        return {"area": area, "lane": lane}


__all__ = ["register"]
