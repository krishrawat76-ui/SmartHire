"""
JD understanding: which skills does this role actually ask for, and how badly?

Two passes over the JD:
  1. Gazetteer lookup  — curated terms with aliases and embedding phrases.
  2. N-gram mining     — capitalised technical terms the gazetteer doesn't know,
                         so an unusual stack still produces requirements.

Tiering is line-driven. We track which requirement block each line belongs to
("Required Skills" vs "Preferred Skills") and also honour inline hedges, so
"TypeScript is a strong plus" lands as PREFERRED even inside a required block.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from backend import config
from backend.core import normalizer

REQUIRED = "REQUIRED"
PREFERRED = "PREFERRED"


@dataclass(slots=True)
class Skill:
    id: str                      # canonical key, matches the gazetteer
    label: str                   # display form
    tier: str                    # REQUIRED | PREFERRED
    weight: float                # 1.0 | 0.5
    cluster: str                 # radar chart axis
    phrase: str                  # the text actually embedded for the semantic channel
    aliases: list[str] = field(default_factory=list)
    source: str = "gazetteer"    # gazetteer | mined
    evidence: str = ""           # the JD line this came from

    @property
    def surface_forms(self) -> list[str]:
        """Every literal string that counts as an explicit mention."""
        return [self.id, self.label.lower(), *[a.lower() for a in self.aliases]]


# ─────────────────────────────────────────────────────────────────────────────
# Gazetteer
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_gazetteer() -> list[dict]:
    raw = json.loads(config.GAZETTEER_PATH.read_text(encoding="utf-8"))
    return raw["skills"]


@lru_cache(maxsize=1)
def _gazetteer_patterns() -> list[tuple[re.Pattern[str], dict]]:
    """One boundary-safe regex per gazetteer entry, longest surface form first."""
    compiled = []
    for entry in load_gazetteer():
        forms = {entry["id"], entry["label"].lower(), *[a.lower() for a in entry.get("aliases", [])]}
        forms = sorted((f for f in forms if f), key=len, reverse=True)
        pattern = re.compile(
            r"(?<![A-Za-z0-9])(" + "|".join(re.escape(f) for f in forms) + r")(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        compiled.append((pattern, entry))
    return compiled


# ─────────────────────────────────────────────────────────────────────────────
# Requirement-block detection
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_HEADER = re.compile(
    r"^\s*(required|must[- ]have|essential|minimum|core|key)\b.*$|"
    r"^\s*(requirements|skills|qualifications|what you.ll need|responsibilities)\s*:?\s*$",
    re.IGNORECASE,
)
_PREFERRED_HEADER = re.compile(
    r"^\s*(preferred|nice[- ]to[- ]have|desirable|bonus|good[- ]to[- ]have|optional|plus(es)?)\b.*$",
    re.IGNORECASE,
)
_OTHER_HEADER = re.compile(
    r"^\s*(about|overview|company|benefits|perks|location|compensation|salary|how to apply|"
    r"our team|who we are|why join)\b.*$",
    re.IGNORECASE,
)

# Inline hedges that downgrade a single line regardless of its block.
_INLINE_PREFERRED = re.compile(
    r"\b(preferred|nice to have|a plus|strong plus|bonus|desirable|optional|"
    r"familiarity with|exposure to|good to have|would be great|advantageous)\b",
    re.IGNORECASE,
)
# Inline intensifiers that upgrade a line.
_INLINE_REQUIRED = re.compile(
    r"\b(must have|required|is required|essential|mandatory|strong proficiency|"
    r"solid understanding|proven|demonstrated)\b",
    re.IGNORECASE,
)


def _line_tiers(jd_text: str) -> list[tuple[str, str | None]]:
    """Annotate every JD line with the tier it implies, or None if it's not a requirement."""
    out: list[tuple[str, str | None]] = []
    block: str | None = None

    for line in jd_text.split("\n"):
        probe = line.strip()
        if not probe:
            out.append((line, block))
            continue

        # Headers switch the active block and are not themselves requirements.
        if _PREFERRED_HEADER.match(probe) and len(probe) < 60:
            block = PREFERRED
            out.append((line, None))
            continue
        if _REQUIRED_HEADER.match(probe) and len(probe) < 60:
            block = REQUIRED
            out.append((line, None))
            continue
        if _OTHER_HEADER.match(probe) and len(probe) < 60:
            block = None
            out.append((line, None))
            continue

        # Inline hedges beat the surrounding block.
        if _INLINE_PREFERRED.search(probe):
            out.append((line, PREFERRED))
        elif _INLINE_REQUIRED.search(probe):
            out.append((line, REQUIRED))
        else:
            out.append((line, block))

    return out


# ─────────────────────────────────────────────────────────────────────────────
# N-gram mining
# ─────────────────────────────────────────────────────────────────────────────

_CAP_TERM = re.compile(r"\b([A-Z][A-Za-z0-9+#.]{1,}(?:\.[a-z]{2,})?(?: [A-Z][A-Za-z0-9+#.]{1,}){0,2})\b")
_MINE_STOP = frozenset("""
The A An And Or But We You Your Our This That These Those With For From Into About
Must Should Will Would Can May Required Preferred Bonus Nice Strong Solid Experience
Familiarity Exposure Understanding Knowledge Proficiency Ability Skills Role Team
Work Working Build Building Develop Developing Years Year Job Company Office Bangalore
India Candidates Candidate Tier Plus Good Great Responsibilities Requirements About
""".split())


@lru_cache(maxsize=1)
def _known_words() -> frozenset[str]:
    """Every word appearing inside any gazetteer surface form.

    Guards against mining fragments of skills we already know: "Version control"
    and "Unit testing" are aliases of git and testing, so bare "Version" and
    "Unit" are not new requirements — they are noise that would dilute the
    coverage denominator and put phantom gaps in explanations.
    """
    words: set[str] = set()
    for entry in load_gazetteer():
        forms = [entry["id"], entry["label"].lower(), *[a.lower() for a in entry.get("aliases", [])]]
        for form in forms:
            words.update(re.split(r"[^a-z0-9+#]+", form))
    return frozenset(w for w in words if w)


def _mine_terms(line: str, known: set[str]) -> list[str]:
    """Capitalised technical-looking terms the gazetteer missed."""
    found = []
    known_words = _known_words()
    for match in _CAP_TERM.finditer(line):
        term = match.group(1).strip()
        words = term.split()
        if any(w in _MINE_STOP for w in words):
            continue
        if len(term) < 3 or term.lower() in known:
            continue
        # A single word that is already part of a known skill phrase is a fragment.
        if len(words) == 1 and term.lower() in known_words:
            continue
        # Require a technical shape: contains a digit, dot, +, #, or is a single
        # capitalised word that isn't ordinary English prose.
        if not (re.search(r"[0-9+#.]", term) or len(words) == 1):
            continue
        found.append(term)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_skills(jd_text: str, mine: bool = True) -> list[Skill]:
    """Extract the weighted skill set this JD is really asking for."""
    lines = _line_tiers(jd_text)
    patterns = _gazetteer_patterns()

    found: dict[str, Skill] = {}
    known_surface: set[str] = set()
    for entry in load_gazetteer():
        known_surface.add(entry["id"])
        known_surface.add(entry["label"].lower())
        known_surface.update(a.lower() for a in entry.get("aliases", []))

    for line, tier in lines:
        if not line.strip():
            continue
        canon_line = normalizer.canonicalize(line)

        for pattern, entry in patterns:
            if not (pattern.search(line) or pattern.search(canon_line)):
                continue

            sid = entry["id"]
            # A skill mentioned anywhere in the JD counts. Tier resolution:
            # an explicit REQUIRED mention always beats a PREFERRED one, and a
            # tiered mention always beats an untiered one.
            effective = tier or REQUIRED
            if sid in found:
                if found[sid].tier == PREFERRED and effective == REQUIRED and tier is not None:
                    found[sid].tier = REQUIRED
                    found[sid].weight = config.WEIGHT_REQUIRED
                    found[sid].evidence = line.strip()
                continue

            found[sid] = Skill(
                id=sid,
                label=entry["label"],
                tier=effective,
                weight=config.WEIGHT_REQUIRED if effective == REQUIRED else config.WEIGHT_PREFERRED,
                cluster=entry.get("cluster", "Other"),
                phrase=entry.get("phrase", entry["label"]),
                aliases=entry.get("aliases", []),
                source="gazetteer",
                evidence=line.strip(),
            )

    if mine:
        for line, tier in lines:
            if tier is None or not line.strip():
                continue
            for term in _mine_terms(line, known_surface):
                sid = term.lower()
                if sid in found:
                    continue
                found[sid] = Skill(
                    id=sid, label=term, tier=tier,
                    weight=config.WEIGHT_REQUIRED if tier == REQUIRED else config.WEIGHT_PREFERRED,
                    cluster="Other", phrase=term, aliases=[],
                    source="mined", evidence=line.strip(),
                )

    # REQUIRED first, then alphabetical — stable ordering for the UI.
    return sorted(found.values(), key=lambda s: (s.tier != REQUIRED, s.label.lower()))


def summarise(skills: list[Skill]) -> str:
    req = [s.label for s in skills if s.tier == REQUIRED]
    pref = [s.label for s in skills if s.tier == PREFERRED]
    return (
        f"{len(skills)} skills  ({len(req)} required, {len(pref)} preferred)\n"
        f"  REQUIRED : {', '.join(req)}\n"
        f"  PREFERRED: {', '.join(pref)}"
    )
