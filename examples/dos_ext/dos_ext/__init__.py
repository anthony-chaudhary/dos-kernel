"""dos_ext — a copy-me example DOS extension package.

Ships occupants across the hackability axes (HACKING.md), each registered under its
entry-point group in this package's `pyproject.toml` — and NONE of them imported by the
`dos` package, which discovers each by name:
  * the `terse` / `friendly` renderers (`dos_ext.renderer` / `dos_ext.friendly_renderer`)
    under `dos.renderers` — Axis 4.
  * the `budget_guard` admission predicate (`dos_ext.predicates`) under `dos.predicates`
    — Axis 3 (the conjunctive-only safety seam).
  * the `keyword` judge (`dos_ext.judge`) under `dos.judges` — Axis 6 (the JUDGE rung).
  * the `semantic-groups` overlap policy (`dos_ext.overlap`) under `dos.overlap_policies`
    — Axis 7 (the disjointness scorer).
  * the `acme` host-policy DRIVER (`dos_ext.driver`) under `dos.drivers` — Axis 1 (a whole
    host policy pack shipped from a third-party package; `dos --driver acme`).
  * the `acme` MCP tool (`dos_ext.mcp_tools`) under `dos.mcp_tools` — a third-party verb
    added to the `dos` MCP server (`register(mcp)`).

`pip install -e examples/dos_ext` makes `dos --output terse` resolve the renderer, the
arbiter pick up the predicate, `dos --driver acme` resolve the host pack, and the MCP
server expose `acme_lane_hint` — without the `dos` package knowing this one exists.
`dos plugins` lists every one of them under its seam.
"""
