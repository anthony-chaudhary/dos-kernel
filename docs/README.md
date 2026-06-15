# DOS documentation — start here

This directory has two kinds of document, and a newcomer wants very different
things from each:

- **Guides** tell you how to *use* and *extend* DOS. Read these — the table
  below is the map.
- **Design notes & plan records** are the build journal — the *why* behind
  each decision and the phased plans that shipped (or are still on the bench).
  They live on their own page, **[BUILD_JOURNAL.md](BUILD_JOURNAL.md)**; read
  it when you want the reasoning, or before changing the kernel.

If you read nothing else, read the first two rows of the table below.

```text
    NEW HERE?  follow the arrows — the doc NUMBERS are chronology, not reading order.

  [QUICKSTART.md] ──run──▶ [../README.md] ──extend──▶ [HACKING.md]
   5-min verdict            what DOS is, the          add reasons / lanes /
   from git alone           syscall ABI, full CLI     judges as DATA (7 axes)
        │                          │
        │ want the WHY?            │ changing the kernel?
        ▼                          ▼
  [BUILD_JOURNAL.md]         [../CLAUDE.md] + [../CONTRIBUTING.md]
   design notes, plan         the 4-layer contract + the layering litmus tests
   records, research arcs
        │
        │ the deep map of one research arc
        ▼
  [ENTERPRISEOPS_ARC.md]
```

## Guides (read these first)

| Doc | What it gives you |
|---|---|
| [**QUICKSTART.md**](QUICKSTART.md) | A runnable 5-minute hello-world: install → `dos init` → commit → `dos verify` shows a real verdict from git history alone. **Start here.** |
| [**CLI-REFERENCE.md**](CLI-REFERENCE.md) | Every `dos` verb, grouped — the full user-facing CLI reference (the README shows only the core dozen). Distinct from `CLI.md`, which is the `cli.py` design prose. |
| [**incidents/**](incidents/README.md) | Arriving from a burn? One page per failure mode — "my agent said it committed, but there's no commit" and its siblings — each with the command that catches it. |
| [**ALTERNATIVES.md**](ALTERNATIVES.md) | How DOS relates to the neighbors — eval platforms, framework guardrails, Temporal, in-toto, plain CI: what each does well, what DOS adds, and when NOT to use DOS. |
| [**../README.md**](../README.md) | What DOS is, the syscall ABI, the core CLI verbs, the install. The front door. |
| [**HACKING.md**](HACKING.md) | How to extend DOS *without forking it* — add refusal reasons, lanes, renderers, judges, and safety predicates as workspace policy. The seven extension axes + the plugin model. |
| [**STABILITY.md**](STABILITY.md) | The compatibility promise: which surfaces you may depend on, what the version number means for each, the deprecation window (`DosDeprecationWarning`), and the short list of what will never break. |
| [**../CONTRIBUTING.md**](../CONTRIBUTING.md) | How to send a change to the kernel: the layering rule, the CI-enforced litmus tests, the green bar. |
| [**../CLAUDE.md**](../CLAUDE.md) | The full architecture contract — the four layers and the one-directional import rule. The canonical reference for *where code belongs*. |
| [**ENTERPRISEOPS_ARC.md**](ENTERPRISEOPS_ARC.md) | The map of the EnterpriseOps-Gym benchmark research line (docs/143–153): designed vs measured vs shipped, the crack/supersede graph, the one-canonical-value results registry, and the coordination rule for parallel research. Read before touching that arc. |

## The build journal — the *why* and the *what shipped*

Everything numbered lives in **[BUILD_JOURNAL.md](BUILD_JOURNAL.md)**: the
design notes (the thinking the code rests on), the plan records (each
capability's phased plan, with its shipped/partial/planned status), and the
research arcs (the 144→ experiment lines). The numbers are chronology, **not**
a reading order, and a handful were used twice — the journal's header carries
the disambiguation. Per-version release notes stay in [releases/](releases/).
