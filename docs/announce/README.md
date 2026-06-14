# Announcement drafts

Launch copy for `dos-kernel`, staged here ready to post. **Nothing here posts
itself** — LinkedIn's API forbids automating promotional posts, and the rest is
your identity to spend. These are drafts you copy, paste, and publish.

*Refreshed 2026-06-14 against v0.26.0. Repo is public, `pip install dos-kernel`
serves 0.26.0, and the scoreboard pages are live — the repo/package launch
(LinkedIn + Show HN) is link-ready now; only the arXiv item still waits on an ID.
Re-check the numbers against the paper before posting — they trace to
`paper/sections/*.html` + `paper/meta.py`.*

## The files

| File | What it is | Where it goes |
|---|---|---|
| [`linkedin.md`](linkedin.md) | The LinkedIn launch post + two shorter variants | LinkedIn (manual / native scheduler) |
| [`hackernews.md`](hackernews.md) | The "Show HN" title + author's first comment | Hacker News (manual; `news.ycombinator.com/submit`) |
| [`blog.md`](blog.md) | A longer launch narrative | Blog / Substack / the repo's own announcement / dev.to |
| [`medium.md`](medium.md) | The Medium launch article — the longer narrative re-voiced for the author's Medium account (the "AI Factory" sequel framing), with Medium-specific posting notes in its header | Medium (manual; anthony-chaudhary.medium.com) |
| [`arxiv-abstract.md`](arxiv-abstract.md) | The paper abstract + tweet-length framings | The "paper is up" post, once arXiv assigns an ID |

## Order to post

The repo and the package come first (the post links to them). A reasonable
sequence:

1. **Repo + PyPI live** — so links resolve.
2. **LinkedIn post** ([`linkedin.md`](linkedin.md)) — the launch. Attach the
   high-res hero poster (`docs/assets/loop-hero.png`, 3600×1920) or the TUI screenshot.
3. **Blog / longer narrative** ([`blog.md`](blog.md)) — for anyone who clicks "read
   more"; link it from the LinkedIn post. For Medium specifically use
   [`medium.md`](medium.md) (same spine, voice-matched to that account) and
   publish it *before* the LinkedIn post so the link resolves.
4. **Show HN** ([`hackernews.md`](hackernews.md)) — once the repo is public and
   runnable (Show HN requires it). Submit the *repo* URL, then post the author's
   first comment immediately. Independent of the others; the paper goes up as its
   own separate HN item later.
5. **arXiv post** ([`arxiv-abstract.md`](arxiv-abstract.md)) — *after* arXiv
   assigns an ID. Fill the `arXiv:XXXX.XXXXX` placeholder.

## The one rule for all of it

**Lead with the fleet angle, and keep every number honest.** The load-bearing,
irreducible result is the *concurrency* one: a referee between agents catches the
failure no single-agent wrapper can reach. We do *not* rest the headline on handing
the verdict *back to the producing agent* — an in-loop active fix is ≈0-to-harmful
at the easy hop (say so). But **concede the wrapper, not the single-agent value**:
do not let the pitch slide into "single-agent is trivial." An *unforgeable* check
(not "re-read your own result") and a loop cheap enough to run every turn are real
work a person wouldn't hand-build, and they pay for a lone agent too — just not as
an active in-loop fix. And concede the store: where shared state offers a
transaction or a compare-and-swap, a wrapper around even one agent can fuse
check-and-act and close the TOCTOU race with no referee — the claim is scoped to
agents acting through tool APIs that offer no such primitive (the paper concedes
this in §positioning; every draft here states it; a prose-tightening pass cut it
once (b5b9729) and it had to be restored — don't cut it again). Don't dress a rate
as a payoff or a frozen replay as a live result. The
canonical "concurrency changes the verdict" wording in `linkedin.md` is
user-approved — reuse it, don't re-write it.

## Genuinely automatable targets (optional, later)

If you want a programmatic cross-post: **Mastodon** (open API, trivial token POST)
and **Bluesky** (AT Protocol) are the real options. **LinkedIn and X are
manual** — paste the relevant draft into their composers.
