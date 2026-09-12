"""
Where a skill appears, and when — because "React" is not one claim.

A tool driven in an internship that ended last month and the same six letters
sitting in a comma-separated inventory from a first-year lab are both a literal
match, and a keyword engine scores them identically. That is the single biggest
reason ATS ranking feels arbitrary to candidates and useless to recruiters.

This module prices the difference, as a multiplier in (0, 1] on each chunk:

    section    Worked evidence (EXPERIENCE, PROJECTS) outranks a self-declared
               inventory (SKILLS) outranks an interest.
    shape      A bare tag inside a delimited run carries no context at all, and
               that is true wherever in the document the run happens to sit.
    recency    A dated mention decays towards a floor, never to zero — old
               experience is weaker evidence, not absent evidence.

Applied to the LEXICAL channel only. The semantic channel reads the surrounding
prose already and prices context itself; charging the multiplier there too would
bill a candidate twice for one weakness.

Nothing here reads a date that is not written down. An undated chunk decays not
at all — punishing a candidate for a layout we could not parse would invent a
signal rather than measure one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend import config
from backend.core.normalizer import Chunk

# ─────────────────────────────────────────────────────────────────────────────
# Dates
# ─────────────────────────────────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_PRESENT_RE = re.compile(r"\b(present|current|ongoing|now|to date)\b", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
_MONTH_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(19[89]\d|20[0-4]\d)\b",
    re.IGNORECASE,
)


def _months_in(text: str) -> list[tuple[int, int]]:
    """Every (year, month) the text states. Month defaults to mid-year when absent."""
    found: list[tuple[int, int]] = []
    for m in _MONTH_YEAR_RE.finditer(text):
        found.append((int(m.group(2)), _MONTHS[m.group(1).lower()[:3]]))
    if not found:
        found = [(int(y), 6) for y in _YEAR_RE.findall(text)]
    return found


def _latest(text: str, horizon: tuple[int, int]) -> tuple[int, int] | None:
    """The most recent point in time this text refers to.

    "Present" is not a date but it is a claim about one, and it is the strongest
    recency signal a resume carries — so it resolves to the document horizon.
    """
    if _PRESENT_RE.search(text):
        return horizon
    stamps = _months_in(text)
    return max(stamps) if stamps else None


# ─────────────────────────────────────────────────────────────────────────────
# Shape
# ─────────────────────────────────────────────────────────────────────────────

_SPLIT_RE = re.compile(r"[,;|/•·]+")

# Past-tense and gerund forms that mark a line as a description of work rather
# than an inventory. Kept explicit rather than inferred from -ed/-ing endings,
# which would classify "Advanced", "Testing" and "Debugging" as verbs and let a
# skills list masquerade as a sentence.
_WORK_VERBS = frozenset("""
built building build developed developing develop implemented implementing implement
designed designing design created creating create wrote writing write led leading lead
deployed deploying deploy migrated migrating migrate optimised optimized optimising
optimizing refactored refactoring shipped shipping integrated integrating integrate
automated automating automate architected maintained maintaining maintain reduced
improved increased delivered launched scaled debugged analysed analyzed configured
collaborated owned managed presented published researched trained tested benchmarked
achieved contributed engineered prototyped rewrote ported instrumented profiled
""".split())


def is_bare_list(text: str) -> bool:
    """True when the line is an inventory of tags rather than a statement about work.

    Shape-based on purpose: a comma-run inside PROJECTS is still a comma-run, and
    a prose sentence inside SKILLS is still evidence.
    """
    words = re.findall(r"[a-z][a-z+#.]*", text.lower())
    if sum(w in _WORK_VERBS for w in words) > config.BARE_LIST_MAX_VERBS:
        return False

    # Drop a leading "Languages:" style label before counting items.
    body = text.split(":", 1)[1] if ":" in text[:32] else text
    items = [p.strip() for p in _SPLIT_RE.split(body) if p.strip()]
    if len(items) < config.BARE_LIST_MIN_ITEMS:
        return False

    # Every item short enough to be a tag rather than a clause.
    return all(len(item.split()) <= 3 for item in items)


# ─────────────────────────────────────────────────────────────────────────────
# Weighting
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class ChunkContext:
    """Why this chunk is worth what it is worth. Rendered verbatim in the drawer."""
    section: str
    weight: float               # the multiplier actually applied
    section_weight: float
    recency_weight: float
    bare_list: bool
    year: int | None            # the date this chunk speaks from, if it states one
    inherited: bool             # the date came from the entry header, not this line
    reason: str

    @property
    def discounted(self) -> bool:
        return self.weight < 0.995


def horizon(text: str) -> tuple[int, int]:
    """The document's own 'now': the latest date it mentions anywhere.

    Anchoring to the resume rather than the wall clock keeps a two-year-old CV
    internally consistent — its most recent role is still its most recent role,
    and every other entry is aged relative to that.
    """
    stamps = _months_in(text)
    if not stamps:
        return (config.CURRENT_YEAR_FALLBACK, 6)
    return max(stamps)


def recency_weight(stamp: tuple[int, int] | None, ref: tuple[int, int]) -> float:
    """Exponential decay to a floor. An undated chunk does not decay."""
    if stamp is None:
        return 1.0
    age = max(0, (ref[0] - stamp[0]) * 12 + (ref[1] - stamp[1]))
    if age <= config.RECENCY_FULL_MONTHS:
        return 1.0
    over = age - config.RECENCY_FULL_MONTHS
    decayable = 1.0 - config.RECENCY_FLOOR
    return config.RECENCY_FLOOR + decayable * (0.5 ** (over / config.RECENCY_HALFLIFE_MONTHS))


def annotate(chunks: list[Chunk], full_text: str) -> list[ChunkContext]:
    """Price every chunk of one document. Returned aligned with `chunks`."""
    ref = horizon(full_text)

    # A dated entry header ("Acme Corp — Jun 2024 to Present") governs the bullets
    # beneath it until the next dated header or the next section boundary.
    carried: tuple[int, int] | None = None
    carried_section: str | None = None

    out: list[ChunkContext] = []
    for chunk in chunks:
        if chunk.section != carried_section:
            carried, carried_section = None, chunk.section

        own = _latest(chunk.text, ref)
        if own is not None:
            carried = own
        stamp = own if own is not None else carried

        sec_w = config.SECTION_CONTEXT_WEIGHT.get(
            chunk.section, config.SECTION_CONTEXT_WEIGHT["UNKNOWN"])
        rec_w = recency_weight(stamp, ref)
        bare = is_bare_list(chunk.text)

        weight = sec_w * rec_w
        if bare:
            weight *= config.BARE_LIST_PENALTY

        out.append(ChunkContext(
            section=chunk.section,
            weight=round(min(max(weight, 0.0), 1.0), 4),
            section_weight=sec_w,
            recency_weight=round(rec_w, 4),
            bare_list=bare,
            year=stamp[0] if stamp else None,
            inherited=own is None and stamp is not None,
            reason=_reason(chunk.section, sec_w, rec_w, bare, stamp, ref),
        ))
    return out


def _reason(section: str, sec_w: float, rec_w: float,
            bare: bool, stamp: tuple[int, int] | None, ref: tuple[int, int]) -> str:
    """One sentence a recruiter can read without knowing the formula."""
    parts: list[str] = []

    if section == "SKILLS":
        parts.append("in the self-declared skills list")
    elif section == "EDUCATION":
        parts.append("in coursework")
    elif section in ("EXPERIENCE", "PROJECTS"):
        parts.append(f"in worked {section.lower()}")
    elif section != "UNKNOWN":
        parts.append(f"in {section.lower()}")

    if bare:
        parts.append("as a bare tag with no surrounding context")

    if stamp is not None:
        age_months = max(0, (ref[0] - stamp[0]) * 12 + (ref[1] - stamp[1]))
        if age_months <= config.RECENCY_FULL_MONTHS:
            parts.append(f"dated {stamp[0]}, within the last year of this resume")
        else:
            parts.append(f"dated {stamp[0]}, about {age_months // 12} "
                         f"{'year' if age_months // 12 == 1 else 'years'} back")

    if not parts:
        return "No section or date context available; counted at face value."

    verdict = "counted in full" if sec_w * rec_w >= 0.995 and not bare else "discounted"
    return f"Found {', '.join(parts)} — {verdict}."
