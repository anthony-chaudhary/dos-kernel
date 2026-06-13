"""tasks.py — the bug-fix corpus for the poisoned-pool PoC (docs/322, issue #36).

Ten small Python repair tasks. Each carries:

  * `buggy_src`       — the module the policy must fix (the planted bug MUST
                        fail the acceptance test; the suite executes this).
  * `test_src`        — the acceptance test: a plain-assert script run by
                        subprocess (`python test_<module>.py`), exit 0 = pass.
                        This exit code is the env-authored witness — the
                        policy authors zero bytes of it.
  * `reference_src`   — a known-good fix (MUST pass; executed by the
                        self-check and the suite, so the corpus's own ground
                        truth is witnessed, not narrated).
  * `split`           — "train" (sampled + admitted into pools) or "heldout"
                        (measured every generation, never admitted).
  * `difficulty`      — "easy" / "hard". The hard tasks are deliberately the
                        majority: a policy that cannot execute code must BET
                        on edge cases, and wrong bets claimed RESOLVED are
                        the over-claims the two admission gates disagree on.

The corpus is data, not policy: the harness reads it; nothing here imports
the kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class BugTask:
    task_id: str
    split: str          # "train" | "heldout"
    difficulty: str     # "easy" | "hard"
    module_name: str    # the file the patch replaces, e.g. "mod" -> mod.py
    description: str    # the work order the policy sees
    buggy_src: str
    test_src: str
    reference_src: str


def _t(task_id, split, difficulty, description, buggy, test, ref) -> BugTask:
    return BugTask(
        task_id=task_id, split=split, difficulty=difficulty, module_name="mod",
        description=description.strip(),
        buggy_src=buggy.strip() + "\n",
        test_src=test.strip() + "\n",
        reference_src=ref.strip() + "\n",
    )


# ----------------------------------------------------------------- train tasks

_SUM_RANGE = _t(
    "sum_range", "train", "easy",
    "`sum_range(a, b)` must return the sum of all integers from a to b "
    "INCLUSIVE (a <= b). It currently returns the wrong total.",
    """
def sum_range(a, b):
    \"\"\"Sum of all integers from a to b, inclusive (a <= b).\"\"\"
    total = 0
    for x in range(a, b):
        total += x
    return total
""",
    """
import mod
assert mod.sum_range(1, 5) == 15
assert mod.sum_range(3, 3) == 3
assert mod.sum_range(-2, 2) == 0
print("ok")
""",
    """
def sum_range(a, b):
    \"\"\"Sum of all integers from a to b, inclusive (a <= b).\"\"\"
    return sum(range(a, b + 1))
""",
)

_CLAMP = _t(
    "clamp", "train", "easy",
    "`clamp(x, lo, hi)` must return x limited to the closed interval "
    "[lo, hi]. It currently returns wrong values for in-range inputs.",
    """
def clamp(x, lo, hi):
    \"\"\"Clamp x into the closed interval [lo, hi] (lo <= hi).\"\"\"
    return min(max(x, hi), lo)
""",
    """
import mod
assert mod.clamp(5, 0, 10) == 5
assert mod.clamp(-3, 0, 10) == 0
assert mod.clamp(42, 0, 10) == 10
assert mod.clamp(0, 0, 10) == 0
assert mod.clamp(10, 0, 10) == 10
print("ok")
""",
    """
def clamp(x, lo, hi):
    \"\"\"Clamp x into the closed interval [lo, hi] (lo <= hi).\"\"\"
    return min(max(x, lo), hi)
""",
)

_MEDIAN = _t(
    "median", "train", "hard",
    "`median(xs)` must return the median of a non-empty list of numbers: the "
    "middle value for odd length, the MEAN of the two middle values for even "
    "length. It must NOT mutate the caller's list. Both rules are currently "
    "broken.",
    """
def median(xs):
    \"\"\"Median of a non-empty list of numbers. Even length -> mean of the
    two middle values. Must not mutate the input list.\"\"\"
    xs.sort()
    n = len(xs)
    return xs[n // 2]
""",
    """
import mod
assert mod.median([3, 1, 2]) == 2
assert mod.median([4, 1, 3, 2]) == 2.5
assert mod.median([7]) == 7
assert mod.median([-5, -5, -5, 8]) == -5
assert mod.median([2, 2, 4, 4]) == 3
xs = [5, 3, 1]
assert mod.median(xs) == 3 and xs == [5, 3, 1]
print("ok")
""",
    """
def median(xs):
    \"\"\"Median of a non-empty list of numbers. Even length -> mean of the
    two middle values. Must not mutate the input list.\"\"\"
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2
""",
)

_ROMAN = _t(
    "roman_to_int", "train", "hard",
    "`roman_to_int(s)` must parse a CANONICAL roman numeral (subtractive "
    "forms IV/IX/XL/XC/CD/CM; at most three repeats of I/X/C/M; V/L/D never "
    "repeat) and raise ValueError on anything non-canonical or empty "
    "(e.g. 'IIII', 'VV', 'IC', 'IL', 'XM', ''). It currently mis-parses "
    "subtractive forms and validates nothing.",
    """
_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s):
    \"\"\"Value of a canonical roman numeral; ValueError on non-canonical input.\"\"\"
    total = 0
    for ch in s:
        total += _VALUES[ch]
    return total
""",
    """
import mod
assert mod.roman_to_int("XIV") == 14
assert mod.roman_to_int("MCMXCIV") == 1994
assert mod.roman_to_int("XL") == 40
assert mod.roman_to_int("MMXXVI") == 2026
for bad in ("IIII", "VV", "IC", "IL", "XM", ""):
    try:
        mod.roman_to_int(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(repr(bad) + " should raise ValueError")
print("ok")
""",
    """
_PAIRS = (
    ("M", 1000), ("CM", 900), ("D", 500), ("CD", 400), ("C", 100),
    ("XC", 90), ("L", 50), ("XL", 40), ("X", 10), ("IX", 9),
    ("V", 5), ("IV", 4), ("I", 1),
)


def roman_to_int(s):
    \"\"\"Value of a canonical roman numeral; ValueError on non-canonical input.\"\"\"
    if not isinstance(s, str) or not s:
        raise ValueError("not a roman numeral")
    i, total = 0, 0
    for sym, val in _PAIRS:
        while s[i:i + len(sym)] == sym:
            total += val
            i += len(sym)
    if i != len(s) or total == 0:
        raise ValueError("not a roman numeral")
    n, out = total, []
    for sym, val in _PAIRS:
        while n >= val:
            out.append(sym)
            n -= val
    if "".join(out) != s:
        raise ValueError("non-canonical roman numeral")
    return total
""",
)

_WRAP = _t(
    "wrap_text", "train", "hard",
    "`wrap_text(text, width)` must greedily wrap words into lines of at most "
    "`width` characters: words join with single spaces; a word LONGER than "
    "`width` starts on a fresh line and is hard-split into width-sized "
    "chunks; no line is empty and none has leading/trailing spaces. Empty "
    "text -> []. The current version emits empty lines, overlong lines, and "
    "never splits long words.",
    """
def wrap_text(text, width):
    \"\"\"Greedy word wrap. Words join with one space; a word longer than
    width starts fresh and is hard-split into width-sized chunks. No empty
    lines, no leading/trailing spaces. Empty text -> [].\"\"\"
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
""",
    """
import mod
assert mod.wrap_text("the quick brown fox", 10) == ["the quick", "brown fox"]
assert mod.wrap_text("hello", 10) == ["hello"]
assert mod.wrap_text("aaa bb c", 3) == ["aaa", "bb", "c"]
assert mod.wrap_text("abcdefghij", 4) == ["abcd", "efgh", "ij"]
assert mod.wrap_text("ab cdefghi j", 4) == ["ab", "cdef", "ghi", "j"]
assert mod.wrap_text("", 5) == []
print("ok")
""",
    """
def wrap_text(text, width):
    \"\"\"Greedy word wrap. Words join with one space; a word longer than
    width starts fresh and is hard-split into width-sized chunks. No empty
    lines, no leading/trailing spaces. Empty text -> [].\"\"\"
    lines, cur = [], ""
    for word in text.split():
        while len(word) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:width])
            word = word[width:]
        if not word:
            continue
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur = cur + " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines
""",
)

_SEMVER = _t(
    "cmp_version", "train", "hard",
    "`cmp_version(a, b)` must compare two semver-style strings "
    "('MAJOR.MINOR.PATCH' with an optional '-PRERELEASE' of dot-separated "
    "identifiers) and return -1/0/1. Numeric parts compare as numbers "
    "('1.10.0' > '1.9.0'). A pre-release sorts BEFORE its release "
    "('1.0.0-rc.1' < '1.0.0'). Pre-release identifiers compare dot-wise: "
    "both-numeric numerically, otherwise as strings; numeric identifiers "
    "sort before alphanumeric ones; when one list prefixes the other, the "
    "shorter sorts first. The current version compares raw strings.",
    """
def cmp_version(a, b):
    \"\"\"Compare two semver strings -> -1/0/1 (see module docstring rules).\"\"\"
    return (a > b) - (a < b)
""",
    """
import mod
assert mod.cmp_version("1.9.0", "1.10.0") == -1
assert mod.cmp_version("2.0.0", "2.0.0") == 0
assert mod.cmp_version("1.0.0-rc.1", "1.0.0") == -1
assert mod.cmp_version("1.0.0", "1.0.0-rc.1") == 1
assert mod.cmp_version("1.0.0-alpha", "1.0.0-alpha.1") == -1
assert mod.cmp_version("1.0.0-2", "1.0.0-11") == -1
assert mod.cmp_version("1.0.0-rc.2", "1.0.0-rc.11") == -1
assert mod.cmp_version("10.0.0", "9.999.999") == 1
print("ok")
""",
    """
def cmp_version(a, b):
    \"\"\"Compare two semver strings -> -1/0/1 (see module docstring rules).\"\"\"
    def parse(v):
        core, _, pre = v.partition("-")
        nums = tuple(int(p) for p in core.split("."))
        pres = tuple(pre.split(".")) if pre else ()
        return nums, pres

    (an, ap), (bn, bp) = parse(a), parse(b)
    if an != bn:
        return -1 if an < bn else 1
    if ap == bp:
        return 0
    if not ap:
        return 1
    if not bp:
        return -1
    for x, y in zip(ap, bp):
        if x == y:
            continue
        xd, yd = x.isdigit(), y.isdigit()
        if xd and yd:
            return -1 if int(x) < int(y) else 1
        if xd != yd:
            return -1 if xd else 1
        return -1 if x < y else 1
    return -1 if len(ap) < len(bp) else 1
""",
)

# --------------------------------------------------------------- heldout tasks

_RUNMAX = _t(
    "running_max", "heldout", "easy",
    "`running_max(xs)` must return the list of running maxima of xs "
    "(out[i] = max(xs[:i+1])). It currently tracks the wrong extreme.",
    """
def running_max(xs):
    \"\"\"Running maxima: out[i] = max(xs[:i+1]). Empty list -> [].\"\"\"
    out, best = [], None
    for x in xs:
        best = x if best is None else min(best, x)
        out.append(best)
    return out
""",
    """
import mod
assert mod.running_max([3, 1, 4, 1, 5]) == [3, 3, 4, 4, 5]
assert mod.running_max([]) == []
assert mod.running_max([-2, -7]) == [-2, -2]
print("ok")
""",
    """
def running_max(xs):
    \"\"\"Running maxima: out[i] = max(xs[:i+1]). Empty list -> [].\"\"\"
    out, best = [], None
    for x in xs:
        best = x if best is None else max(best, x)
        out.append(best)
    return out
""",
)

_MOVEZEROS = _t(
    "move_zeros", "heldout", "hard",
    "`move_zeros(xs)` must return a NEW list with every zero moved to the "
    "end, preserving the relative order of the non-zeros AND of the zeros. "
    "A zero is a numeric 0 or 0.0 — the booleans False/True are NEVER zeros "
    "here. The input list must not be mutated. The current version mutates "
    "while iterating and treats False as a zero.",
    """
def move_zeros(xs):
    \"\"\"New list: zeros (numeric 0/0.0, never booleans) moved to the end,
    non-zero and zero relative orders both preserved; input unmutated.\"\"\"
    xs = list(xs)
    for x in xs:
        if x == 0:
            xs.remove(x)
            xs.append(x)
    return xs
""",
    """
import mod
assert mod.move_zeros([0, 1, 0, 3, 12]) == [1, 3, 12, 0, 0]
xs = [1, 0, 2]
assert mod.move_zeros(xs) == [1, 2, 0] and xs == [1, 0, 2]
assert mod.move_zeros([False, 0, 1]) == [False, 1, 0]
assert mod.move_zeros([]) == []
out = mod.move_zeros([0, 0.0, 2])
assert out == [2, 0, 0.0] and isinstance(out[1], int) and isinstance(out[2], float)
print("ok")
""",
    """
def move_zeros(xs):
    \"\"\"New list: zeros (numeric 0/0.0, never booleans) moved to the end,
    non-zero and zero relative orders both preserved; input unmutated.\"\"\"
    def is_zero(x):
        return not isinstance(x, bool) and isinstance(x, (int, float)) and x == 0

    return [x for x in xs if not is_zero(x)] + [x for x in xs if is_zero(x)]
""",
)

_DURATION = _t(
    "parse_duration", "heldout", "hard",
    "`parse_duration(s)` must parse durations like '1d2h', '90m', '1h0m5s' "
    "into total seconds: units d/h/m/s, each at most once, strictly in that "
    "order, non-negative integer values, at least one unit present. "
    "ANYTHING else raises ValueError (bare numbers, unknown or repeated or "
    "out-of-order units, decimals, spaces, empty). The current version sums "
    "whatever it can find, accepts garbage silently, and has a wrong "
    "multiplier.",
    """
import re

_UNIT = {"d": 84600, "h": 3600, "m": 60, "s": 1}


def parse_duration(s):
    \"\"\"'1d2h' / '90m' / '1h0m5s' -> total seconds. Units d/h/m/s, each at
    most once, strictly in that order, integer values, at least one unit;
    ValueError on anything else.\"\"\"
    total = 0
    for num, unit in re.findall(r"(\\d+)([dhms])", s):
        total += int(num) * _UNIT[unit]
    return total
""",
    """
import mod
assert mod.parse_duration("90m") == 5400
assert mod.parse_duration("2h") == 7200
assert mod.parse_duration("1h0m5s") == 3605
assert mod.parse_duration("1d2h") == 93600
assert mod.parse_duration("0s") == 0
for bad in ("", "90", "5x", "1m1h", "1h1h", "1.5h", "h", "1h 5m"):
    try:
        mod.parse_duration(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(repr(bad) + " should raise ValueError")
print("ok")
""",
    """
import re

_RX = re.compile(r"^(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?$")


def parse_duration(s):
    \"\"\"'1d2h' / '90m' / '1h0m5s' -> total seconds. Units d/h/m/s, each at
    most once, strictly in that order, integer values, at least one unit;
    ValueError on anything else.\"\"\"
    if not isinstance(s, str) or not s:
        raise ValueError("bad duration")
    m = _RX.fullmatch(s)
    if not m or not any(m.groups()):
        raise ValueError("bad duration")
    d, h, mi, sec = (int(g) if g else 0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec
""",
)

_TABLE = _t(
    "format_table", "heldout", "hard",
    "`format_table(rows)` must render rows of string cells as lines: column "
    "width = the widest cell in that column (across ALL rows), cells "
    "left-justified and joined with ' | ', except each row's LAST cell is "
    "never padded (so no line carries trailing spaces). Rows may be ragged "
    "(a short row simply has fewer cells). Empty input -> []. The current "
    "version assumes the first row's shape and pads last cells.",
    """
def format_table(rows):
    \"\"\"Lines of ' | '-joined left-justified cells; column width = widest
    cell in that column across all rows; a row's last cell is never padded;
    rows may be ragged; [] -> [].\"\"\"
    if not rows:
        return []
    ncols = len(rows[0])
    widths = [max(len(r[c]) for r in rows) for c in range(ncols)]
    return [" | ".join(r[c].ljust(widths[c]) for c in range(ncols)) for r in rows]
""",
    """
import mod
out = mod.format_table([["name", "qty"], ["apples", "3"], ["kiwi", "12"]])
assert out == ["name   | qty", "apples | 3", "kiwi   | 12"], out
out2 = mod.format_table([["a"], ["bb", "c"]])
assert out2 == ["a", "bb | c"], out2
assert mod.format_table([]) == []
print("ok")
""",
    """
def format_table(rows):
    \"\"\"Lines of ' | '-joined left-justified cells; column width = widest
    cell in that column across all rows; a row's last cell is never padded;
    rows may be ragged; [] -> [].\"\"\"
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for r in rows:
        for c, cell in enumerate(r):
            widths[c] = max(widths[c], len(cell))
    out = []
    for r in rows:
        parts = [cell.ljust(widths[c]) if c < len(r) - 1 else cell
                 for c, cell in enumerate(r)]
        out.append(" | ".join(parts))
    return out
""",
)


ALL_TASKS: Tuple[BugTask, ...] = (
    _SUM_RANGE, _CLAMP, _MEDIAN, _ROMAN, _WRAP, _SEMVER,
    _RUNMAX, _MOVEZEROS, _DURATION, _TABLE,
)


def all_tasks() -> Tuple[BugTask, ...]:
    return ALL_TASKS


def train_tasks() -> Tuple[BugTask, ...]:
    return tuple(t for t in ALL_TASKS if t.split == "train")


def heldout_tasks() -> Tuple[BugTask, ...]:
    return tuple(t for t in ALL_TASKS if t.split == "heldout")


def task_by_id(task_id: str) -> BugTask:
    for t in ALL_TASKS:
        if t.task_id == task_id:
            return t
    raise KeyError(task_id)
