## From the same team

DOS is one of three open tools from [Anthony Chaudhary](https://github.com/anthony-chaudhary)
for running AI agents you can actually trust — at three different moments:

- **[fak — the agent kernel](https://github.com/anthony-chaudhary/fak)** — DOS reads *what an
  agent already did* (after the fact, from git and other witnesses it can't forge); `fak` governs
  *what an agent is allowed to do* as it happens. A single static Go binary that fronts your token
  engine and adjudicates every tool call at the boundary — capability gate, tool-result
  quarantine, audit trail — the inline gate to DOS's out-of-loop referee. `go install
  github.com/anthony-chaudhary/fak/cmd/fak@latest` · [docs](https://anthony-chaudhary.github.io/fak/).
- **[Diffgram](https://github.com/diffgram/diffgram)** — the AI datastore for human supervision of
  AI *data* (labeling, workflow, catalog). Where DOS and `fak` supervise the *agents*, Diffgram
  supervises the *data* they learn from and produce.

## Citation

The ideas here are written up in a paper — *"Verification Is All You Need — But
Not Where You Think"* — on the out-of-loop referee for agent fleets. A built PDF
lives at [`paper/releases/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/paper/releases); the arXiv preprint is in
preparation. Until the arXiv ID lands, cite the repository:

```bibtex
@misc{dos_kernel,
  title        = {Verification Is All You Need --- But Not Where You Think},
  author       = {Chaudhary, Anthony},
  howpublished = {\url{https://github.com/anthony-chaudhary/dos-kernel}},
  note         = {DOS --- the Dispatch Operating System; arXiv preprint in preparation},
  year         = {2026}
}
```

## License

MIT — see [LICENSE](https://github.com/anthony-chaudhary/dos-kernel/blob/master/LICENSE).

<!-- The marker below is the official MCP Registry's PyPI ownership proof: the
     registry only accepts a server.json naming the `dos-kernel` PyPI package if
     this exact token appears in the published package README. Keep it intact. -->
<!-- mcp-name: io.github.anthony-chaudhary/dos-kernel -->
