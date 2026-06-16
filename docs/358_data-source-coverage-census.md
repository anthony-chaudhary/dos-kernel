# 358 — The data-source coverage census: the sources DOS can reasonably witness

> **The one-line claim.** The industry's data sources look like a long, scary list —
> payments, databases, object stores, queues, cloud control planes, CI, DNS, legal
> reporters, on and on. But DOS does not need a reader per vendor. **Every source
> whose authoritative byte the agent cannot forge collapses onto a small closed set
> of witness *shapes*** — and DOS ships a backend for each one. This doc enumerates
> the universe, maps each source to its shape, carves out the irreducible residue no
> witness can reach, and reports the coverage that falls out. The number is not prose:
> it is read from the repo's own ground truth by
> [`scripts/source_census.py`](../scripts/source_census.py) and pinned by
> [`tests/test_source_census.py`](../tests/test_source_census.py). A source counts as
> COVERED only when its shape resolves to a backend ACTUALLY registered in this tree;
> the count cannot move on a promise.

This is a census, not new theory. The theory it measures against already exists,
scattered across docs/85, docs/93, docs/183, docs/192, docs/212, docs/261. This doc
brings those threads to one place and turns them into a re-runnable fraction. It does
**not** restate them — read them for the arguments; read this for the count.

---

## 1. The criterion: what "reasonably witnessable" means

DOS grounds a verdict only on a byte the **judged agent cannot author**. That is the
whole product, and it is the one test every source must pass — gate 2 of the
four-gate test in [`85 §2`](85_extending-the-verifiable-surface.md), the accountability
spectrum of [`93 §2`](93_verifying-live-non-git-sources.md), the git-is-not-magic
argument of [`183`](183_how-much-does-this-lean-on-git.md).

The criterion is the three-rung `Accountability` enum in
[`src/dos/evidence.py`](../src/dos/evidence.py) (defined in
[`log_source.py`](../src/dos/log_source.py)):

- **`AGENT_AUTHORED`** — the agent (or a process it controls) wrote the byte. The
  forgeable floor. A judge *hint* at best, never a verdict.
- **`OS_RECORDED`** — the OS wrote it (a kernel-launched exit code, a git blob in a
  content-addressed store). Non-forgeable.
- **`THIRD_PARTY`** — infrastructure the agent cannot write (a CI verdict, a cloud
  audit trail). The strongest rung.

**A source is "reasonably witnessable" iff its authoritative byte reaches
`OS_RECORDED` or `THIRD_PARTY`.** The floor is not witnessable, and two whole classes
of goal have no sound witness at any rung (§4). The honest 98% claim depends on
carving those out of the denominator — not on hiding them.

---

## 2. A witness is a SHAPE, not a per-vendor driver

This is the pivot, and the reason the universe is tractable. You do not need a Stripe
witness and a separate PayPal witness and a separate Adyen witness. You need ONE
witness for the *shape* they share — "read the external-effect ledger keyed by a
reference, on infrastructure the agent does not control" — and point it at each
vendor. [`212`](212_dos-in-non-coding-domains-the-world-witness-axis.md) shows the
domains collapsing this way; [`261`](261_the-witness-ladder-benchmark.md) tracks which
shapes are built. The census operationalizes both into a count.

The closed set of shapes, each proven by a registered backend:

| shape | what it reads | rung | backend |
|---|---|---|---|
| `provider_ledger_readback` | an external-effect ledger keyed by a reference (payments, SMS, cloud audit, object-store events) | THIRD_PARTY | [`provider_ledger`](../src/dos/drivers/provider_ledger.py) |
| `persisted_state_diff` | a persisted store read back vs a gold value (relational/KV/doc DBs, object storage, config stores, files, a live HTTP re-GET) | THIRD_PARTY | [`content_diff`](../src/dos/drivers/content_diff.py) (+ the [`state_diff`](../src/dos/drivers/state_diff.py) kernel helper) |
| `third_party_api_status` | a third-party verdict reader (CI/Checks, control-plane status, package index, legal reporters) | THIRD_PARTY | [`ci_status`](../src/dos/drivers/ci_status.py), [`citation_resolve`](../src/dos/drivers/citation_resolve.py) |
| `human_approval_envelope` | "an accountable human approved this" via the Slack envelope (who/when, never the message text) | THIRD_PARTY | [`slack_approval`](../src/dos/drivers/slack_approval.py) |
| `os_acceptance_exit_code` | any exit-code-bearing command (psql, aws, kubectl, dig, curl, docker, gh, a test runner) | OS_RECORDED | [`os_acceptance`](../src/dos/drivers/os_acceptance.py) |
| `git_presence` | the VCS fossil — commit existence, ancestry, blob content | OS_RECORDED | the in-kernel [`oracle`](../src/dos/oracle.py)/[`phase_shipped`](../src/dos/phase_shipped.py) |
| `agent_authored_floor` | operator-pasted text / no signal — listed so it is excluded by NAME, not by being forgotten | AGENT_AUTHORED | [`paste_log`](../src/dos/drivers/paste_log.py), `null` |

The big lesson, stated plainly: **two of these shapes carry most of the world.** The
state-diff shape reads back any store (a SaaS API and a cloud DB are just the
`state_diff` reader pointed at a remote surface — its own docstring says so). The
provider-ledger shape reads any external-effect ledger. A new vendor is almost never
a new witness — it is the host wiring an existing shape at a new endpoint, which is a
host job ([`85`](85_extending-the-verifiable-surface.md) move B), not a kernel gap.

---

## 3. The universe, grouped by shape

The full per-source list is the DATA in
[`scripts/source_census.py`](../scripts/source_census.py) (`SOURCES`) — that script is
the source of truth; this section is its readable mirror. Run it to see the live
table. The shape of the catalogue (49 sources today):

- **`provider_ledger_readback`** — Stripe / PayPal / Adyen charge ledgers, Twilio SMS
  receipts, SendGrid email-send events, AWS CloudTrail, GCP/Azure activity logs, S3
  access+event logs, webhook event stores, refund/chargeback ledgers, cloud billing.
- **`persisted_state_diff`** — PostgreSQL/MySQL/SQLite rows, MongoDB documents, Redis,
  DynamoDB, etcd/Consul config, S3/GCS object content, Elasticsearch, Parquet/data-lake
  tables, feature/vector stores, filesystem file content, container-image digests, a
  live HTTP/API re-GET.
- **`third_party_api_status`** — GitHub Actions / GitLab CI / CircleCI / Jenkins
  verdicts, cloud control-plane status, Kubernetes object status, Terraform state,
  package-registry release index (PyPI/npm), Statuspage/uptime monitors, the legal
  reporter (CourtListener), DNS/WHOIS, certificate-transparency logs.
- **`human_approval_envelope`** — the Slack/chat human-approval envelope.
- **`os_acceptance_exit_code`** — psql/mysql, aws/gcloud/az, kubectl, dig/nslookup,
  curl/http probe, docker/podman, gh/glab, test-runner/linter exit codes.
- **`git_presence`** — git commit existence + ancestry, git blob content, hosted-repo
  state.

Each row carries a status the script computes, not trusts:

- **COVERED** — its shape resolves to a backend registered in this tree's
  `dos.evidence_sources` (or the built git oracle). Checked at runtime; a renamed or
  removed backend flips the row to `UNCOVERED_ROT` and fails `--check`.
- **SPEC** — a witnessable shape named in a doc but with no backend built here. Listed,
  never folded into the covered count. (Today: zero — see §5.)
- **RESIDUE** — no sound witness exists. Excluded from the denominator (§4).

---

## 4. The honest denominator: the residue that is not witnessable

A census that claims to cover "everything" is lying, because some goals have no byte
to read. [`192 §6`](192_the-world-state-witness-ladder-and-the-w2-w3-gap.md) measured
the two irreducible classes over real frontier failures, and
[`261`](261_the-witness-ladder-benchmark.md)'s last row names them as the floor where
the trust ladder bottoms out at HUMAN:

- **EXTERNAL_EFFECT with no readable receipt** — the authoritative byte lives on an
  absent third principal. Did the email recipient actually read it? Did the downstream
  human act on it? The send-tool's "200 OK" is acceptance (a W1 ack), not the goal —
  and there is no receipt to re-read. `byte-author ≠ agent` cannot be made airtight
  when the authoritative byte belongs to someone who left no record in reach.
- **JUDGE_ONLY** — the goal is a judgment with no canonical end-state ("recommend 3
  dishes", "is this summary good?"). The gold itself would have to be an opinion, so it
  routes to the JUDGE rung (advisory, fail-to-abstain), never a deterministic oracle.

These four catalogued residue rows are **excluded from the denominator by
construction**. The census reports them in their own column so they are visible and
auditable — but `witnessable_universe = covered + spec`, never `+ residue`. The test
`test_residue_never_in_the_denominator` pins this: deleting the residue rows cannot
move the coverage number, which proves they were never counted. **That carve-out is
what makes the claim honest rather than inflated.** A census that folded the residue in
to look complete would be committing the exact self-report sin the kernel exists to
refuse.

---

## 5. The coverage arithmetic

Read live from the tree today:

```
witnessable universe = COVERED + SPEC = 45 + 0 = 45
coverage_pct         = COVERED / witnessable universe = 45 / 45 = 100.0%
residue (excluded)   = 4
witness shapes built = 7 / 7
sources catalogued   = 49
```

So **100% of the witnessable universe is covered** — and the number is *earned*, not
asserted. The last gap before this work was the Slack human-approval envelope
([`93 §3`](93_verifying-live-non-git-sources.md) named it as the next driver to write).
The honest options were to redefine the denominator to make it disappear, or to build
the witness. We built it: [`slack_approval`](../src/dos/drivers/slack_approval.py) is
the move-B driver after `ci_status`, registered in `dos.evidence_sources`, so the row
is COVERED because a backend proves it — not because the DATA says so.

The rule the test enforces: the headline moves only when a backend is built/registered
(a row becomes COVERED) or a source is added to the catalogue. It can never move on a
promise. If a future source is genuinely un-witnessable, it goes to RESIDUE with a note
here — it does not lower the 98% threshold to turn a red bar green.

One honesty caveat worth stating: 100% means "every catalogued witnessable source maps
to a built shape," not "DOS can witness any conceivable claim." The residue is real and
permanent; the catalogue is finite and will grow. The instrument's value is that it
*can* show a gap — remove any backend and `--check` reddens the row — so a clean 100%
is a checked fact, not a closed door.

---

## 6. Re-run it / the spine it measures

```bash
python scripts/source_census.py            # the readable census (headline + per-shape + per-source)
python scripts/source_census.py --json      # headline + census as JSON
python scripts/source_census.py --check      # exit 1 if any COVERED source's backend is missing (rot pin)
```

The conceptual spine this census measures against — read these for the arguments, not
restated here:

- [`85_extending-the-verifiable-surface.md`](85_extending-the-verifiable-surface.md) —
  the four-gate test; witness-as-driver-vs-verb; the three homes.
- [`93_verifying-live-non-git-sources.md`](93_verifying-live-non-git-sources.md) — the
  accountability spectrum, the live-source ranking, and the Slack-approval shape this
  work built.
- [`183_how-much-does-this-lean-on-git.md`](183_how-much-does-this-lean-on-git.md) — git
  is necessary, not sufficient; tamper-evidence does not transfer off git for free.
- [`192_the-world-state-witness-ladder-and-the-w2-w3-gap.md`](192_the-world-state-witness-ladder-and-the-w2-w3-gap.md)
  — the W0–W3\* ladder and the measured residue this census excludes.
- [`212_dos-in-non-coding-domains-the-world-witness-axis.md`](212_dos-in-non-coding-domains-the-world-witness-axis.md)
  — the domain → shape mapping the catalogue rests on.
- [`261_the-witness-ladder-benchmark.md`](261_the-witness-ladder-benchmark.md) — the
  built-vs-unbuilt witness benchmark this census turns into a re-runnable number.
