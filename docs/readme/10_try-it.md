## Try it in 60 seconds

Got a terminal? This runs the whole thing in a throwaway repo — one command
scaffolds it, makes a real commit, verifies it, and cleans up after itself:

```bash
pip install dos-kernel      # PyYAML is the only runtime dep
dos quickstart              # → SHIPPED AUTH AUTH1 … then NOT_SHIPPED AUTH AUTH2
```

One `SHIPPED`, one `NOT_SHIPPED`: the first is a claim git can back, the second
is a claim nothing landed for. That contrast is the product. The demo closes
with a router to wherever you already run agents — a Claude Code / Cursor tab
(`dos init --hooks`), an MCP host, a CI step, or a fleet — so your next move is
one line, not a docs dig. (Add `--keep ./demo` to keep the repo and poke at it.
Don't even want the install? `uvx --from dos-kernel dos quickstart` runs the
same demo ephemerally — nothing left behind.)

<details>
<summary><strong>Prefer to watch the gears turn?</strong> The same thing, by hand, in 5 lines — click to expand</summary>

A *plan* (`AUTH`) groups *phases* (`AUTH1`, `AUTH2`); `dos verify` takes
`<plan> <phase>`, and a commit whose subject starts `AUTH1:` is what stamps that
phase shipped.

```bash
mkdir hello-dos && cd hello-dos
dos init .                                       # writes one dos.toml
git init -q
git config user.email you@example.com            # skip if you have a global git identity
git config user.name  "You"
echo 'def login(): ...' > login.py
git add -A
git commit -m "AUTH1: ship the login endpoint"   # stamp AUTH1 shipped: <PHASE-ID>: <message>

dos verify --workspace . AUTH AUTH1   # → SHIPPED     AUTH AUTH1 <your-sha> (via grep-subject)  exit 0
dos verify --workspace . AUTH AUTH2   # → NOT_SHIPPED  AUTH AUTH2            (via none)          exit 1
```

An agent can claim `AUTH2` is done all day long; `verify` just reports what the
artifacts say — and they say it isn't. The `via grep-subject` / `via none` tag
tells you *how it knows*: it found the phase token in a commit subject, or it
found it nowhere. The full walkthrough is in
**[docs/QUICKSTART.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md)**.

</details>

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/examples/demo/verify-moment.svg" alt="The dos verify money-moment. Two equally-confident agent claims, checked against git. Left, what the agent claims (forgeable): 'Shipped AUTH1 — the login endpoint is done' and 'AUTH2 is done too — all work completed!'. Right, what git actually records: one real commit e389e8b 'AUTH1: ship the login endpoint', and no commit anywhere mentions AUTH2. The two verdicts: dos verify AUTH AUTH1 finds the token in a real commit subject → SHIPPED, exit 0, via grep-subject; dos verify AUTH AUTH2 finds it nowhere → NOT_SHIPPED, exit 1, via none. The confident AUTH2 claim collapses the instant no commit backs it." width="100%">
  <br>
  <sub><em>Two equally confident claims, one verdict each — <code>SHIPPED</code> for the one git can back, <code>NOT_SHIPPED</code> for the one nothing landed for. Every string is verbatim output of <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/demo/verify_demo.sh"><code>examples/demo/verify_demo.sh</code></a>. <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/demo/verify_visual.html">Step through it locally</a> for the click-through version (it's an HTML file — clone the repo and open it in a browser; GitHub shows its source, not the running page).</em></sub>
</p>

The smallest real win: in a CI step or dispatch loop, replace the line that
trusts an agent's "done" with `dos verify PLAN PHASE` and branch on its exit
code (`0` shipped / `1` not). No parsing, no plan, no config — the
[CI integration cookbook](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-ci-integration.md) walks it
end-to-end. To run it on a repo shaped like yours, start with
[Onboard a repo in 10 minutes](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/01_onboard-a-repo.md).

*Next level up — wire the verdict into your own stack: [How you plug it in](#how-you-plug-it-in).*
