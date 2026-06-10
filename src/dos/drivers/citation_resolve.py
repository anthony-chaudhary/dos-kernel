"""dos.drivers.citation_resolve — the legal-citation witness (docs/277 §6 #1, docs/279).

The catastrophic, *sanctioned* legal-AI failure is the **fabricated citation** (the
*Mata v. Avianca* class — fake cases cited to a federal court, $5,000 sanction, May
2023). Stanford measured 17–33% hallucination on legal-RAG tools, and the field's own
verdict (Harvey-LAB, 2026) is that citation hallucination "is not captured by any
benchmark." That failure sits on DOS's *cleanest* rung: a cited case either **resolves
in a third-party reporter** — bytes the agent authored zero of — or it does not.

This is the second occupant of the docs/265 `dos.evidence_sources` seam (the first is
`ci_status`), and the same **move (B)**: a new artifact oracle for a non-git surface.
It has the surface the kernel forbids — network I/O against a third party
(CourtListener / Free Law Project) — so it lives HERE, in a driver, exactly as
`ci_status` / `llm_judge` do, and for the same structural reason. It imports the kernel;
the kernel never imports it (`drivers/__init__` rule).

What it witnesses (Tier 1 — existence + quote-fidelity), and what it does NOT
============================================================================
It answers "does this citation EXIST, and does the quoted holding MATCH the resolved
opinion?" It does **not** make the legal argument correct (Tier 3 — abstain). A
caught-count (`J`) here is a flagged fabrication, never a won case. Selling it as "DOS
verifies legal correctness" is the docs/277 §7 over-claim — and in this domain an
over-claim is a liability, not a bug.

The shape (the `ci_status` template, verbatim)
==============================================
  * the **boundary reader** `gather()` mirrors `dos.git_delta`/`ci_status.gather`: the
    HTTP call (`urllib` against CourtListener) happens HERE, at the caller boundary, and
    every failure mode (no token, network error, timeout, rate-limit, malformed JSON)
    degrades to an honest `ABSTAIN` evidence object — never a crash, never a fabricated
    RESOLVED. A deployment with no corpus access gets "abstain," the truthful floor.
  * the **pure classifier** `classify(CitationEvidence, CitationPolicy) -> CitationVerdict`
    is in the `classify(Evidence, Policy) -> Verdict` family: a closed-enum verdict, a
    frozen caller-gathered evidence dataclass, a frozen policy, an operator-facing
    `reason`, a `to_dict()`. `classify()` makes NO I/O — the whole verdict is
    replay-testable on frozen fixtures (the family discipline, and what makes the
    benchmark's $0 replay deterministic).

The two non-forgeable operands (why resolution ALONE is insufficient — docs/279 §3)
===================================================================================
A *Mata* fabrication, `92 F.3d 1074` (Hyatt v. N. Cent. Airlines), *resolves* in the
reporter — but to *Grilli v. Metropolitan Life*, a DIFFERENT real case: the fabricator
reused a real reporter slot with a wrong case name. So citation-string resolution alone
would rubber-stamp it. We therefore check TWO operands, BOTH authored by Free Law
Project (THIRD_PARTY): (1) a cluster carries the claimed **citation string** AND (2) the
cluster's **case name** agrees with the claimed party names. A cite that resolves to a
name that does not match is `UNRESOLVED` — the citation, *as claimed*, does not exist.

The resolver fitness (docs/279 §2)
==================================
CourtListener has two endpoints. `/citation-lookup/` is the purpose-built
normalized-citation resolver but needs a TOKEN (rate-limited). `/search/` is
unauthenticated but is a full-text relevance search, NOT a citation index — its recall
on real cites is unreliable. So: prefer `/citation-lookup/` when `COURTLISTENER_TOKEN`
is set; fall back to `/search/` exact-citation-array match otherwise; ABSTAIN on no
access. The reproducible *measured* benchmark scores against a FROZEN local sample
(`benchmark/legalcite/`); the live driver is for adoption, not the headline number.

The quote-match (docs/156 `derived_witness`)
============================================
The quoted holding is matched against the resolved opinion text by a DECLARED op
(normalized substring containment) — committed up front, never reverse-searched to fit.
A mis-quote (resolved cite, quote absent) is `RESOLVED_MISMATCH` → REFUTED, distinct
from "no signal." Quote-matching needs the opinion BODY, which the unauthenticated
search snippet does not always carry, so the verdict is honest: with no opinion text the
quote rung ABSTAINs (it does not claim a match it could not check), and the citation
rung still stands on its own.
"""

from __future__ import annotations

import argparse
import enum
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

# Imports the kernel — never the other way round (the driver rule). The evidence
# vocabulary for the `EvidenceSource` face; `config` only for the CLI workspace seam.
from dos.evidence import Accountability, EvidenceFacts

# The public Free Law Project base. A host pointing at a mirror passes --base / base=.
DEFAULT_BASE = "https://www.courtlistener.com"
# Cap the network call so a hung API can't stall a gather — the `ci_status._GH_TIMEOUT_S`
# discipline, a touch longer for a possibly-cold third-party search.
_HTTP_TIMEOUT_S = 25
# The env var carrying a CourtListener API token (optional). With it, the driver uses
# the purpose-built /citation-lookup/ endpoint; without it, the noisier /search/ rung.
_TOKEN_ENV = "COURTLISTENER_TOKEN"


class Citation(str, enum.Enum):
    """The typed citation verdict — four states, mutually exclusive.

    `str`-valued so it round-trips through a CLI token / exit-code map without a lookup
    table (the `Ci` / `Liveness` idiom). The four-way split is the honest part: a binary
    valid/invalid would have to LIE about the two cases where there is no answer — a
    mis-quote of a real case (RESOLVED_MISMATCH, a distinct, stronger signal than "fake")
    and no corpus access (ABSTAIN). Collapsing either manufactures a verdict the evidence
    does not support — the typed-verdict-over-binary-gate law on a sometimes-silent source.
    """

    RESOLVED_MATCH = "RESOLVED_MATCH"        # cite resolves AND the quote is in the opinion
    RESOLVED_MISMATCH = "RESOLVED_MISMATCH"  # cite resolves BUT the quote is absent (mis-quote)
    UNRESOLVED = "UNRESOLVED"                # no cluster carries this citation (the fabrication)
    ABSTAIN = "ABSTAIN"                      # no corpus access — never a fabricated verdict

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class CitationPolicy:
    """The knobs separating the verdicts — policy, not mechanism.

      require_name_match — when True (default), a cite that resolves to a cluster whose
                           name does NOT agree with the claimed party names is UNRESOLVED
                           (the docs/279 §3 collision trap: a fabricated name on a real
                           reporter slot). The load-bearing precision guard. A host that
                           only has bare reporter strings (no claimed name) sets it False.
      name_overlap_min   — the minimum normalized-token Jaccard overlap between the
                           claimed name and the cluster name to count as "same case."
                           0.34 ≈ "at least one distinctive party token in common" once
                           v./in re/et al. stop-words are stripped.
      quote_min_len      — quotes shorter than this are too generic to witness (a 3-word
                           phrase appears in thousands of opinions); the quote rung
                           ABSTAINs below it rather than manufacture a coincidental match.
    """

    require_name_match: bool = True
    name_overlap_min: float = 0.34
    quote_min_len: int = 12


DEFAULT_POLICY = CitationPolicy()


@dataclass(frozen=True)
class ResolvedCluster:
    """One reporter cluster, normalized from CourtListener (the unforgeable bit).

    The agent under adjudication cannot author a cluster bearing a given citation string
    in a public reporter database — Free Law Project did. `citations` is the cluster's
    full citation array (parallel cites); `name` is its case name; `opinion_text` is the
    body when available (the search snippet, or the full opinion on a token read), used
    ONLY by the quote rung. That byte-author≠claimant split is the THIRD_PARTY rung the
    whole witness stands on.

    `text_is_full` is the load-bearing honesty flag for the quote rung: a search-result
    SNIPPET (the unauthenticated default) is the opening fragment of the opinion, NOT the
    whole text — so a quote's ABSENCE from it proves nothing (the holding may be on page
    20). The quote rung may only REFUTE a mis-quote when it has the FULL opinion
    (`text_is_full=True`, set by a full-opinion fetch); against a mere snippet it
    ABSTAINs on the quote and stands on existence alone. This is the docs/277 precision
    discipline made structural — a noisy resolver is worse than none, so we never refute
    on evidence we know is partial.
    """

    name: str
    citations: tuple[str, ...]
    opinion_text: str = ""
    text_is_full: bool = False


@dataclass(frozen=True)
class CitationEvidence:
    """Everything `classify()` needs, gathered by the CALLER before the call. PURE in.

    No network inside the verdict — the `ci_status.CiEvidence` rule.

      cite          — the reporter citation string as CLAIMED by the agent (echoed).
      claimed_name  — the case name as CLAIMED (e.g. "Varghese v. China Southern"),
                      checked against the resolved cluster's name. "" disables the
                      name rung for this cite (a bare-reporter claim).
      quote         — the quoted holding as CLAIMED, "" if none (citation-only check).
      clusters      — the reporter clusters whose citation array CONTAINS `cite`
                      (exact, normalized match — NOT a fuzzy search hit). Empty means
                      "no reporter carries this cite" → UNRESOLVED.
      reachable     — False when the corpus call itself failed (no token+noisy fallback
                      failed, network/timeout/rate-limit, bad JSON). With reachable=False
                      the verdict is ALWAYS ABSTAIN regardless of clusters — we observed
                      nothing, so we assert nothing (fail-safe, never fail-open).
      detail        — a one-line note from the gather (the resolver used, or the error
                      class) — carried into the verdict reason so an operator sees WHY.
    """

    cite: str
    claimed_name: str = ""
    quote: str = ""
    clusters: tuple[ResolvedCluster, ...] = ()
    reachable: bool = True
    detail: str = ""


@dataclass(frozen=True)
class CitationVerdict:
    """The single verdict `classify()` returns, with the evidence echoed back.

    `reason` NAMES the driving fact (legible distrust — not just UNRESOLVED but "no
    reporter carries 925 F.3d 1339"; not just MISMATCH but "resolved to Grilli, claimed
    Hyatt"). `to_dict()` is the JSON shape for `--json` / the benchmark / the decisions
    queue. Conforms structurally to the typed-verdict family.
    """

    verdict: Citation
    reason: str
    evidence: CitationEvidence
    matched_name: str = ""

    def to_dict(self) -> dict:
        ev = self.evidence
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "matched_name": self.matched_name,
            "evidence": {
                "cite": ev.cite,
                "claimed_name": ev.claimed_name,
                "quote": ev.quote,
                "reachable": ev.reachable,
                "detail": ev.detail,
                "clusters": [
                    {"name": c.name, "citations": list(c.citations)}
                    for c in ev.clusters
                ],
            },
        }


# ---------------------------------------------------------------------------
# Normalization helpers — pure. The "same citation" / "same case" predicates.
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
# Case-name stop-words: connective/procedural tokens that carry no party identity.
_NAME_STOP = frozenset(
    {"v", "vs", "in", "re", "the", "of", "et", "al", "ex", "rel", "co", "inc",
     "llc", "ltd", "corp", "company", "and", "a", "an"}
)


def _norm_cite(cite: str) -> str:
    """Collapse whitespace + case so '925  F.3d 1339' == '925 F.3d 1339'. The exact
    (not fuzzy) citation-string equality the resolution rung stands on."""
    return _WS.sub(" ", (cite or "").strip()).lower()


def _name_tokens(name: str) -> frozenset[str]:
    """The distinctive party tokens of a case name (stop-words stripped, lowercased).
    'Varghese v. China Southern Airlines' -> {varghese, china, southern, airlines}."""
    raw = re.findall(r"[a-z0-9]+", (name or "").lower())
    return frozenset(t for t in raw if t not in _NAME_STOP and len(t) > 1)


def _names_agree(claimed: str, resolved: str, min_overlap: float) -> bool:
    """True iff the claimed and resolved case names share enough distinctive tokens.

    Jaccard over the smaller side (asymmetric: a claimed 'Varghese v. China Southern'
    matching a resolved 'Varghese v. China Southern Airlines Co.' should agree even
    though the resolved side has extra tokens). Empty claimed name → caller decides via
    `require_name_match`; here an empty token set cannot agree (no identity to confirm)."""
    a, b = _name_tokens(claimed), _name_tokens(resolved)
    if not a or not b:
        return False
    overlap = len(a & b)
    denom = min(len(a), len(b))
    return denom > 0 and (overlap / denom) >= min_overlap


def _quote_in_text(quote: str, text: str, *, min_len: int) -> "bool | None":
    """The declared op of the docs/156 derivation: normalized substring containment.

    Returns True/False if the quote is long enough to witness AND the opinion text is
    present; returns None (ABSTAIN) when there is no text to check or the quote is too
    short to be distinctive (a coincidental match would forge the rung). Committed up
    front — never a reverse search for which opinion contains the quote."""
    q = _WS.sub(" ", (quote or "").strip())
    if len(q) < min_len:
        return None
    if not text or not text.strip():
        return None
    hay = _WS.sub(" ", text).lower()
    return q.lower() in hay


def classify(ev: CitationEvidence, policy: CitationPolicy = DEFAULT_POLICY) -> CitationVerdict:
    """Classify one (cite, name, quote) claim from already-gathered evidence. PURE — no I/O.

    The ladder, top to bottom:

      1. ABSTAIN  — the corpus was unreachable. We saw nothing → assert nothing. Checked
                    FIRST so a failed read can never be mistaken for a real verdict
                    (fail-safe; the `ci_status` NO_SIGNAL-on-unreachable rung).
      2. UNRESOLVED — no cluster carries this citation (the fabrication), OR a cluster
                    carries it but its name does not agree with the claimed name (the
                    docs/279 §3 collision: a fabricated name on a real slot). The
                    citation, AS CLAIMED, does not exist.
      3. RESOLVED_MISMATCH — the cite resolves to the claimed case, but the quoted
                    holding is NOT in the opinion (a checkable mis-quote). Only reached
                    when there IS opinion text and a long-enough quote (else the quote
                    rung abstains and we fall through to MATCH on the citation alone).
      4. RESOLVED_MATCH — the cite resolves to the claimed case AND (the quote matched OR
                    there was no checkable quote). The honest top: existence confirmed;
                    quote-fidelity confirmed-or-not-applicable.
    """
    # 1. ABSTAIN (unreachable) — fail-safe floor.
    if not ev.reachable:
        return CitationVerdict(
            verdict=Citation.ABSTAIN,
            reason=(
                f"no corpus access for '{ev.cite}'"
                + (f" — {ev.detail}" if ev.detail else " — resolver unreachable")
            ),
            evidence=ev,
        )

    norm = _norm_cite(ev.cite)
    # The clusters whose citation array literally contains this cite (exact, normalized).
    carrying = [c for c in ev.clusters if norm in {_norm_cite(x) for x in c.citations}]

    # 2a. UNRESOLVED — nothing in the reporter carries this citation string.
    if not carrying:
        return CitationVerdict(
            verdict=Citation.UNRESOLVED,
            reason=(
                f"no reporter cluster carries '{ev.cite}' — citation does not resolve "
                f"({ev.detail or 'searched the reporter index'})"
            ),
            evidence=ev,
        )

    # 2b. Name agreement — the collision guard. With a claimed name and the policy armed,
    #     a resolved cluster whose name disagrees means the citation AS CLAIMED is fake
    #     (the slot is real, the case is not the one named).
    if policy.require_name_match and ev.claimed_name.strip():
        agreeing = [c for c in carrying if _names_agree(ev.claimed_name, c.name, policy.name_overlap_min)]
        if not agreeing:
            resolved_to = "; ".join(sorted({c.name for c in carrying if c.name})[:3]) or "(unnamed cluster)"
            return CitationVerdict(
                verdict=Citation.UNRESOLVED,
                reason=(
                    f"'{ev.cite}' resolves to a DIFFERENT case — claimed "
                    f"'{ev.claimed_name}', reporter has '{resolved_to}' "
                    f"(citation as claimed does not exist; the docs/279 §3 collision)"
                ),
                evidence=ev,
                matched_name=resolved_to,
            )
        carrying = agreeing  # quote-check against the name-agreeing cluster(s)

    matched_name = next((c.name for c in carrying if c.name), "")

    # 3 / 4. Quote rung — only when a checkable quote AND the FULL opinion text exist.
    # A search SNIPPET is excluded (text_is_full=False): a quote's absence from the
    # opening fragment proves nothing, so refuting on it would be unsound (docs/279 §2).
    if ev.quote.strip():
        text = "\n".join(c.opinion_text for c in carrying if c.opinion_text and c.text_is_full)
        hit = _quote_in_text(ev.quote, text, min_len=policy.quote_min_len)
        if hit is False:
            return CitationVerdict(
                verdict=Citation.RESOLVED_MISMATCH,
                reason=(
                    f"'{ev.cite}' resolves to '{matched_name}' but the quoted holding is "
                    f"NOT in the opinion text (a mis-quote — put words in the court's mouth)"
                ),
                evidence=ev,
                matched_name=matched_name,
            )
        if hit is None:
            # No opinion text or quote too short to witness — citation stands, quote
            # abstains. Honest: we do not claim a match we could not check.
            return CitationVerdict(
                verdict=Citation.RESOLVED_MATCH,
                reason=(
                    f"'{ev.cite}' resolves to '{matched_name}'; quote-fidelity not checkable "
                    f"(no opinion text or quote too short) — existence confirmed only"
                ),
                evidence=ev,
                matched_name=matched_name,
            )

    # 4. RESOLVED_MATCH — existence confirmed; quote matched or not applicable.
    return CitationVerdict(
        verdict=Citation.RESOLVED_MATCH,
        reason=(
            f"'{ev.cite}' resolves to '{matched_name}'"
            + (" and the quoted holding appears in the opinion" if ev.quote.strip() else "")
        ),
        evidence=ev,
        matched_name=matched_name,
    )


# ---------------------------------------------------------------------------
# The boundary reader — the ONLY I/O path (mirrors dos.drivers.ci_status.gather).
# ---------------------------------------------------------------------------


def _http_get_json(url: str, *, token: str = "") -> "tuple[Optional[dict], str]":
    """GET `url` → (parsed-json, "") on success, (None, error-class) else. NEVER raises.

    The single guarded provider seam (the `ci_status._run_gh` discipline). Every failure
    mode — network error, timeout, rate-limit (HTTP 429), auth failure, malformed JSON —
    returns `(None, <short reason>)` so `gather()` degrades to an unreachable evidence
    object → ABSTAIN. This is the one place CourtListener is touched."""
    headers = {"User-Agent": "dos-citation-resolve/0.1 (https://github.com/anthony-chaudhary/dos-kernel)"}
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:  # 4xx/5xx — rate-limit / auth / not found
        if e.code == 429:
            return None, "rate-limited (HTTP 429) — corpus quota exhausted"
        if e.code in (401, 403):
            return None, f"auth failure (HTTP {e.code})"
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:  # network down / DNS / timeout
        return None, f"network error ({getattr(e, 'reason', e)!r})"
    except (TimeoutError, OSError) as e:
        return None, f"network error ({e.__class__.__name__})"
    try:
        return json.loads(raw.decode("utf-8", "replace")), ""
    except (ValueError, TypeError):
        return None, "malformed JSON from resolver"


def _clusters_from_search(data: dict) -> tuple[ResolvedCluster, ...]:
    """Normalize a CourtListener /search/ response into clusters. Tolerant: a missing
    field yields an empty/partial cluster, never a raise (the `ci_status` parse-defensive
    stance). The search result carries `caseName`, `citation` (a list), and sometimes an
    opinion `snippet`/`text`."""
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return ()
    out: list[ResolvedCluster] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        cites = r.get("citation") or []
        if not isinstance(cites, list):
            cites = []
        # Opinion body, when the search result carries it (varies by endpoint version).
        text = ""
        for k in ("snippet", "text", "plain_text"):
            v = r.get(k)
            if isinstance(v, str) and v.strip():
                text = v
                break
        # Some shapes nest opinions; pull their snippets too.
        for op in (r.get("opinions") or []) if isinstance(r.get("opinions"), list) else []:
            if isinstance(op, dict):
                for k in ("snippet", "text"):
                    v = op.get(k)
                    if isinstance(v, str) and v.strip():
                        text = (text + "\n" + v) if text else v
        out.append(ResolvedCluster(
            name=str(r.get("caseName") or "").strip(),
            citations=tuple(str(c).strip() for c in cites if str(c).strip()),
            opinion_text=text,
        ))
    return tuple(out)


def _clusters_from_lookup(data: "dict | list") -> tuple[ResolvedCluster, ...]:
    """Normalize a CourtListener /citation-lookup/ response. The lookup returns a list of
    per-citation results; a `status == 200` entry carries `clusters` (each with
    `case_name` + `citations`). A `status` of 404 means the cite did not resolve → no
    clusters. Tolerant of shape drift."""
    entries = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else None)
    if not isinstance(entries, list):
        return ()
    out: list[ResolvedCluster] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("status") not in (200, "200", None):
            continue
        for cl in (e.get("clusters") or []) if isinstance(e.get("clusters"), list) else []:
            if not isinstance(cl, dict):
                continue
            cites = cl.get("citations") or []
            norm_cites: list[str] = []
            for c in cites if isinstance(cites, list) else []:
                if isinstance(c, str):
                    norm_cites.append(c.strip())
                elif isinstance(c, dict):  # {volume, reporter, page}
                    vol, rep, pg = c.get("volume"), c.get("reporter"), c.get("page")
                    if rep and vol and pg:
                        norm_cites.append(f"{vol} {rep} {pg}")
            out.append(ResolvedCluster(
                name=str(cl.get("case_name") or cl.get("caseName") or "").strip(),
                citations=tuple(c for c in norm_cites if c),
            ))
    return tuple(out)


def gather(
    cite: str,
    *,
    claimed_name: str = "",
    quote: str = "",
    base: str = DEFAULT_BASE,
    token: str = "",
) -> CitationEvidence:
    """Resolve `cite` against CourtListener. Boundary I/O — the ONLY network path.

    Prefers the purpose-built `/citation-lookup/` endpoint when a token is given (the
    reliable resolver); falls back to the unauthenticated `/search/` exact-citation-array
    match otherwise (the docs/279 §2 fitness note: noisier, so the headline benchmark
    number uses the frozen sample, not this). NEVER raises — every failure degrades to an
    unreachable `CitationEvidence`, which `classify()` maps to ABSTAIN, never a fabricated
    RESOLVED."""
    if not (cite or "").strip():
        return CitationEvidence(cite="", claimed_name=claimed_name, quote=quote,
                                reachable=False, detail="no citation string given")

    token = token or os.environ.get(_TOKEN_ENV, "")
    if token:
        # The purpose-built normalized resolver (POST text → parsed cites + clusters).
        url = f"{base.rstrip('/')}/api/rest/v4/citation-lookup/"
        data, err = _http_post_form(url, {"text": cite}, token=token)
        if data is not None:
            clusters = _clusters_from_lookup(data)
            return CitationEvidence(cite=cite, claimed_name=claimed_name, quote=quote,
                                    clusters=clusters, reachable=True,
                                    detail="via /citation-lookup/ (token)")
        # Token path failed — fall through to the search rung (it may still answer).
        detail_prefix = f"citation-lookup failed ({err}); fell back to search — "
    else:
        detail_prefix = ""

    # Unauthenticated /search/ rung: phrase-quote the cite, opinions only.
    q = urllib.parse.urlencode({"q": f'"{cite}"', "type": "o"})
    url = f"{base.rstrip('/')}/api/rest/v4/search/?{q}"
    data, err = _http_get_json(url, token=token)
    if data is None:
        return CitationEvidence(cite=cite, claimed_name=claimed_name, quote=quote,
                                reachable=False, detail=detail_prefix + err)
    clusters = _clusters_from_search(data)
    return CitationEvidence(cite=cite, claimed_name=claimed_name, quote=quote,
                            clusters=clusters, reachable=True,
                            detail=detail_prefix + "via /search/ (unauthenticated)")


def _http_post_form(url: str, fields: dict, *, token: str = "") -> "tuple[Optional[dict], str]":
    """POST form fields → (parsed-json, "") | (None, err). NEVER raises (the GET twin,
    for the token-only /citation-lookup/ endpoint which takes POSTed text)."""
    headers = {"User-Agent": "dos-citation-resolve/0.1", "Content-Type": "application/x-www-form-urlencoded"}
    if token:
        headers["Authorization"] = f"Token {token}"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return None, "rate-limited (HTTP 429)"
        if e.code in (401, 403):
            return None, f"auth failure (HTTP {e.code})"
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"network error ({getattr(e, 'reason', e)!r})"
    except (TimeoutError, OSError) as e:
        return None, f"network error ({e.__class__.__name__})"
    try:
        return json.loads(raw.decode("utf-8", "replace")), ""
    except (ValueError, TypeError):
        return None, "malformed JSON from resolver"


def resolve(
    cite: str,
    *,
    claimed_name: str = "",
    quote: str = "",
    base: str = DEFAULT_BASE,
    token: str = "",
    policy: CitationPolicy = DEFAULT_POLICY,
) -> CitationVerdict:
    """Convenience: gather + classify in one call (the wired entry point). Kept thin so
    the reader and the verdict stay independently testable on frozen fixtures."""
    return classify(
        gather(cite, claimed_name=claimed_name, quote=quote, base=base, token=token),
        policy,
    )


# ---------------------------------------------------------------------------
# The EvidenceSource face — the `dos.evidence_sources` entry-point occupant (docs/265).
# The subject is the citation, optionally "<cite> || <claimed_name> || <quote>" so one
# string carries all three operands through the generic seam.
# ---------------------------------------------------------------------------

_SUBJECT_SEP = "||"


class CitationResolveSource:
    """An `evidence.EvidenceSource` over the legal-citation resolver. `THIRD_PARTY`-tagged.

    The `subject` IS the citation, optionally packing the claimed name + quote as
    "<cite> || <name> || <quote>" (the seam's subject is one opaque string; this is the
    source's chosen encoding). `gather` runs `resolve(...)` at the boundary and maps the
    typed verdict to `EvidenceFacts`:

      * RESOLVED_MATCH     → **ATTESTED**  (a third-party reporter carries the cite + the
                            quote matches — bytes the agent did not author)
      * RESOLVED_MISMATCH  → **REFUTED**   (resolves, but the quote is fabricated)
      * UNRESOLVED         → **REFUTED**   (no reporter carries it — the Mata fabrication;
                            a positive disconfirmation, stronger than "no signal")
      * ABSTAIN            → **NO_SIGNAL** (no corpus access — never a fabricated verdict)

    `accountability` is CLASS-LEVEL and fixed `THIRD_PARTY`: a reporter's citation index
    is infrastructure the agent does not control. So a RESOLVED_MATCH IS eligible to grant
    belief under `believe_under_floor` — and, crucially, an UNRESOLVED is a non-forgeable
    REFUTED that can REDDEN a verify of "I cited this case." Never raises —
    `gather_evidence` wraps it fail-safe and `resolve` degrades every provider failure to
    ABSTAIN on its own. `config` is accepted for Protocol conformance.
    """

    name = "citation_resolve"
    accountability = Accountability.THIRD_PARTY

    def __init__(self, *, base: str = DEFAULT_BASE, token: str = "",
                 policy: CitationPolicy = DEFAULT_POLICY) -> None:
        self._base = base
        self._token = token
        self._policy = policy

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        cite, claimed_name, quote = self._unpack(subject)
        if not cite:
            return EvidenceFacts.no_signal(
                self.name, self.accountability, subject,
                detail="no citation in subject — nothing to resolve",
            )
        v = resolve(cite, claimed_name=claimed_name, quote=quote,
                    base=self._base, token=self._token, policy=self._policy)
        if v.verdict is Citation.RESOLVED_MATCH:
            return EvidenceFacts.attest(self.name, self.accountability, cite, detail=v.reason)
        if v.verdict in (Citation.UNRESOLVED, Citation.RESOLVED_MISMATCH):
            return EvidenceFacts.refute(self.name, self.accountability, cite, detail=v.reason)
        # ABSTAIN — no corpus access. The honest floor; never a fabricated read.
        return EvidenceFacts.no_signal(self.name, self.accountability, cite, detail=v.reason)

    @staticmethod
    def _unpack(subject: str) -> tuple[str, str, str]:
        parts = [p.strip() for p in (subject or "").split(_SUBJECT_SEP)]
        cite = parts[0] if parts else ""
        name = parts[1] if len(parts) > 1 else ""
        quote = parts[2] if len(parts) > 2 else ""
        return cite, name, quote


# ---------------------------------------------------------------------------
# CLI — `python -m dos.drivers.citation_resolve "<cite>" [--name N] [--quote Q]`.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dos.drivers.citation_resolve",
        description=__doc__.splitlines()[0],
    )
    ap.add_argument("cite", help='the reporter citation, e.g. "925 F.3d 1339"')
    ap.add_argument("--name", default="", help="the case name as claimed (collision guard)")
    ap.add_argument("--quote", default="", help="the quoted holding to check against the opinion")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"CourtListener base (default: {DEFAULT_BASE})")
    ap.add_argument("--token", default="", help=f"API token (or set ${_TOKEN_ENV})")
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = ap.parse_args(argv)

    verdict = resolve(args.cite, claimed_name=args.name, quote=args.quote,
                      base=args.base, token=args.token)
    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, default=str))
    else:
        print(f"CITE      {verdict.evidence.cite}")
        if verdict.evidence.claimed_name:
            print(f"CLAIMED   {verdict.evidence.claimed_name}")
        print(f"VERDICT   {verdict.verdict.value}")
        print(f"WHY       {verdict.reason}")

    # Exit map: a clean resolve-and-match is 0; everything that is not is non-zero so a
    # gate can `&&` on it. MISMATCH/UNRESOLVED = 1 (a caught fabrication/mis-quote),
    # ABSTAIN = 3 (could not tell — a human's call), mirroring `dos verify` / `ci_status`.
    return {
        Citation.RESOLVED_MATCH: 0,
        Citation.RESOLVED_MISMATCH: 1,
        Citation.UNRESOLVED: 1,
        Citation.ABSTAIN: 3,
    }[verdict.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
