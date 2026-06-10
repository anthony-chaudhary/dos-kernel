# The "Verified by DOS" badge

> **Paste-me adoption mark.** Drop it in your README so the world can see your
> repo gates its agents' ship-claims with [`dos verify`](../README.md) — ground
> truth from git history, **not the agent's word**.

[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

## What it means

A repo wearing this badge **runs `dos verify` in its ship-gate** — every claim
that a phase/PR "shipped" is checked against git ancestry by the kernel before it
counts, instead of being taken on the worker's self-report. The badge is the
visible tell of one discipline: *the kernel is the part that doesn't believe the
agents*, wired into your pipeline.

## Paste this (the shields.io static endpoint — no service to run)

The recommended form. It hits shields.io's **static** badge endpoint (a fixed
label/colour, no live query), so there's nothing to host and nothing to break,
and it links back to the DOS repo:

```markdown
[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)
```

Rendered:
[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

- `verified%20by` / `DOS` / `2ea44f` are the label, message, and the DOS ship
  green (`#2ea44f`) — the same green the oracle prints `SHIPPED` in.
- The link target is the DOS repo, so a reader can click through to *why* the
  badge means something.

## Alternative — vendor the SVG, depend on no one

If you'd rather not call out to shields.io at render time (air-gapped repo,
zero-external-host policy), copy the hand-built badge into your own tree and
reference it as a local `<img>`. It's a small, self-contained, two-segment pill
that versions cleanly as text:

```html
<a href="https://github.com/anthony-chaudhary/dos-kernel">
  <img src="docs/assets/verified-by-dos.svg" alt="verified by DOS" height="20">
</a>
```

The source SVG lives at [`docs/assets/verified-by-dos.svg`](assets/verified-by-dos.svg)
— copy it into your repo (adjust the `src` path to wherever you put it). It
renders inline on GitHub with no external host, exactly like the other
[hand-built assets](assets/README.md).

## Honesty note — what the static badge does and doesn't assert

Read this rung carefully, because the kernel's whole point is to not over-claim:

- **The static badge asserts adoption, not a verdict.** It says *"this repo wires
  `dos verify` into its gate."* It is **not** a live, per-commit pass/fail — it
  shows the same green whether your last `verify` answered `SHIPPED` or
  `NOT_SHIPPED`. Wearing it is a claim about your **process**, not a real-time
  truth syscall result.
- **The live, per-commit badge exists today in one place: on DOS's own repo.**
  The kernel repo's README wears a "verified by DOS" badge that is the status of
  [`.github/workflows/dos-gate.yml`](../.github/workflows/dos-gate.yml) — its own
  [`verify-action`](../verify-action/README.md) running `dos commit-audit` over
  every pushed commit plus `dos verify` of the badge's own ship-stamp, so the
  colour is a kernel verdict a CI runner computed from git ancestry, never a
  static pill ([`docs/112`](112_the-dynamic-verified-by-dos-badge.md) Phase 0).
  The *generalized* dynamic badge — the three-state shields endpoint
  (green/neutral/red) safe to aim at any repo — is docs/112 Phases 1–2, still
  future work, because a binary badge must never be pointed at a foreign repo
  (it would false-accuse the `via none` majority). Until then, for **your** repo
  the static badge is an honest *adoption* mark, and the **gate itself** (below)
  is what makes it true.

## Where to earn it

The badge is only honest if `verify` actually gates your ships. Pair it with the
CI ship-gate so the green is backed by a real syscall on every PR:

- **The worked example is this repo itself** → the self-gate in
  [`.github/workflows/dos-gate.yml`](../.github/workflows/dos-gate.yml) (two
  `verify-action` legs: claim-vs-diff on every push, the truth syscall on the
  repo's own stamp). Its workflow badge is the live form of this mark.

- **Wire the gate first** → the GitHub Actions ship-gate in
  [`examples/playbooks/cookbook-ci-integration.md`](../examples/playbooks/cookbook-ci-integration.md)
  (Recipe 1). Once that workflow is green on your PRs, paste the badge.
- **New to DOS?** Start at
  [`examples/playbooks/01_onboard-a-repo.md`](../examples/playbooks/01_onboard-a-repo.md)
  — `pip install dos-kernel` → `dos verify` on a phase, in ~10 minutes.

The order matters: the gate is the substrate, the badge is the projection. Don't
wear the mark until `dos verify` is the thing that closes a phase in your repo.
