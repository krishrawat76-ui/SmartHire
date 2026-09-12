"""
JD bias and narrow-phrasing detection, in two layers.

Layer 1  Pattern scan against a curated lexicon. Fast, explainable, no deps.
         Finds coded language a human reviewer would flag.

Layer 2  Impact simulation. For each hard requirement, re-rank the pool without
         it and measure who actually moves. This is the layer that matters: it
         replaces "this phrasing might exclude people" with "this clause costs
         you these four candidates, and three of them are top-five otherwise."

Layer 2 reuses the scoring engine, so its marginal cost is arithmetic.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from functools import lru_cache

from backend import config
from backend.core import fusion
from backend.core.fusion import Primitives, MISSING
from backend.core.skills import Skill, REQUIRED

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(slots=True)
class Finding:
    category: str
    label: str
    severity: str
    matched_text: str
    start: int
    end: int
    line: str
    suggestion: str
    why: str
    impact: str = ""            # human-readable measured consequence
    impact_count: int = 0       # candidates affected


@dataclass(slots=True)
class BiasReport:
    score: int                  # 0 = riddled with problems, 100 = clean
    findings: list[dict] = field(default_factory=list)
    summary: str = ""
    is_junior_role: bool = False


@lru_cache(maxsize=1)
def _lexicon() -> dict:
    return json.loads(config.BIAS_LEXICON_PATH.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Experience estimation — supports the seniority-contradiction check
# ─────────────────────────────────────────────────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}

_RANGE_PATTERNS = [
    # Jun 2025 - Aug 2025 | Jan 2025 – Present
    re.compile(r"([a-z]{3})[a-z]*\.?\s+(\d{4})\s*[-–—to]+\s*([a-z]{3})[a-z]*\.?\s+(\d{4})", re.I),
    re.compile(r"([a-z]{3})[a-z]*\.?\s+(\d{4})\s*[-–—to]+\s*(present|current|now)", re.I),
    # 01/2024 - 06/2024
    re.compile(r"(\d{1,2})/(\d{4})\s*[-–—to]+\s*(\d{1,2})/(\d{4})"),
    # 2023 - 2024  (bare years)
    re.compile(r"\b(\d{4})\s*[-–—]\s*(\d{4})\b"),
]

_MAX_PLAUSIBLE_MONTHS = 48      # longer than this is almost always a degree span


def estimate_experience_months(text: str) -> int:
    """Rough professional-experience estimate from date ranges.

    Deliberately conservative: ranges longer than four years are dropped as
    almost certainly degree durations rather than jobs. This is an approximation
    and the report says so — it exists to size the impact of an experience
    threshold, not to score anyone.
    """
    total = 0
    lowered = text.lower()

    for pat in _RANGE_PATTERNS[:2]:
        for m in pat.finditer(lowered):
            g = m.groups()
            m1 = _MONTHS.get(g[0][:3].lower())
            if not m1:
                continue
            y1 = int(g[1])
            if len(g) == 3:                       # "- present"
                y2, m2 = 2026, 9
            else:
                m2 = _MONTHS.get(g[2][:3].lower())
                if not m2:
                    continue
                y2 = int(g[3])
            months = (y2 - y1) * 12 + (m2 - m1)
            if 0 < months <= _MAX_PLAUSIBLE_MONTHS:
                total += months

    for m in _RANGE_PATTERNS[2].finditer(lowered):
        mo1, y1, mo2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        months = (y2 - y1) * 12 + (mo2 - mo1)
        if 0 < months <= _MAX_PLAUSIBLE_MONTHS:
            total += months

    return total


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — lexicon scan
# ─────────────────────────────────────────────────────────────────────────────

def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)].strip()


def scan_lexicon(jd_text: str) -> list[Finding]:
    lex = _lexicon()
    findings: list[Finding] = []
    seen: set[tuple[int, int]] = set()

    for cat_id, cat in lex["categories"].items():
        for term in cat["terms"]:
            for m in re.finditer(term["pattern"], jd_text, re.IGNORECASE):
                span = (m.start(), m.end())
                if span in seen:
                    continue
                seen.add(span)
                findings.append(Finding(
                    category=cat_id,
                    label=cat["label"],
                    severity=cat["severity"],
                    matched_text=m.group(0),
                    start=m.start(),
                    end=m.end(),
                    line=_line_at(jd_text, m.start()),
                    suggestion=term["suggestion"],
                    why=cat["why"],
                ))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — impact simulation
# ─────────────────────────────────────────────────────────────────────────────

def simulate_skill_impact(
    primitives: list[Primitives],
    skills: list[Skill],
    alpha: float = config.DEFAULT_ALPHA,
    top_n: int = 5,
) -> list[Finding]:
    """Which single required skill is costing the most qualified candidates?"""
    if not primitives or len(primitives) < 3:
        return []

    baseline = fusion.score(primitives, alpha=alpha)
    baseline_top = {c.doc_id for c in baseline[:top_n]}
    findings: list[Finding] = []

    for skill in skills:
        if skill.tier != REQUIRED:
            continue

        excluded = [p for p in primitives
                    if any(c.skill_id == skill.id and c.status == MISSING for c in p.cells)]
        if not excluded:
            continue

        without = fusion.score(fusion.without_skills(primitives, {skill.id}), alpha=alpha)
        new_top = {c.doc_id for c in without[:top_n]}
        entrants = new_top - baseline_top
        if not entrants:
            continue

        by_id = {c.doc_id: c for c in without}
        names = [by_id[d].name for d in sorted(entrants, key=lambda d: by_id[d].rank)]

        findings.append(Finding(
            category="narrow_phrasing",
            label="Requirement with measurable exclusion cost",
            severity="high" if len(excluded) > len(primitives) // 2 else "medium",
            matched_text=skill.label,
            start=-1, end=-1,
            line=skill.evidence,
            suggestion=f"Consider moving “{skill.label}” to preferred, or accepting "
                       f"demonstrably adjacent experience.",
            why=f"{len(excluded)} of {len(primitives)} candidates show no evidence of "
                f"{skill.label}. Dropping this requirement would move "
                f"{', '.join(names)} into the top {top_n}.",
            impact=f"{len(excluded)}/{len(primitives)} candidates excluded; "
                   f"{len(entrants)} would enter the top {top_n}",
            impact_count=len(excluded),
        ))

    findings.sort(key=lambda f: -f.impact_count)
    return findings


def check_seniority_contradiction(
    jd_text: str,
    experience_months: dict[str, int] | None = None,
    pool_size: int = 0,
) -> list[Finding]:
    """An internship demanding multi-year experience excludes its own audience.

    The single most common way a junior advert accidentally rules out everyone
    it was written for.
    """
    lex = _lexicon()
    lowered = jd_text.lower()
    if not any(marker in lowered for marker in lex["intern_role_markers"]):
        return []

    findings: list[Finding] = []
    for m in re.finditer(r"(\d+)\+?\s*years?(?:\s+of)?\s+experience", jd_text, re.IGNORECASE):
        years = int(m.group(1))
        if years < 2:
            continue

        impact, count = "", 0
        if experience_months:
            below = [n for n, mo in experience_months.items() if mo < years * 12]
            count = len(below)
            impact = (f"{count} of {pool_size} candidates fall below this threshold "
                      f"on estimated experience")

        findings.append(Finding(
            category="narrow_phrasing",
            label="Seniority contradiction",
            severity="critical",
            matched_text=m.group(0),
            start=m.start(), end=m.end(),
            line=_line_at(jd_text, m.start()),
            suggestion=f"An internship cannot require {years} years. Replace with "
                       f"“familiarity with” or “equivalent project work”.",
            why=f"This role is advertised as junior or an internship, yet demands "
                f"{years}+ years of professional experience. Every candidate the "
                f"role is aimed at is excluded by its own text.",
            impact=impact,
            impact_count=count,
        ))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

_SEVERITY_PENALTY = {"critical": 30, "high": 12, "medium": 6, "low": 2}


def detect(
    jd_text: str,
    primitives: list[Primitives] | None = None,
    skills: list[Skill] | None = None,
    resume_texts: dict[str, str] | None = None,
    alpha: float = config.DEFAULT_ALPHA,
) -> BiasReport:
    """Full two-layer analysis. Layers 2 runs only when pool data is supplied."""
    findings = scan_lexicon(jd_text)

    exp_months = (
        {name: estimate_experience_months(text) for name, text in resume_texts.items()}
        if resume_texts else None
    )
    findings += check_seniority_contradiction(
        jd_text, exp_months, len(resume_texts) if resume_texts else 0
    )

    if primitives and skills:
        findings += simulate_skill_impact(primitives, skills, alpha=alpha)

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.impact_count))
    findings = _dedupe_spans(findings)

    penalty = sum(_SEVERITY_PENALTY.get(f.severity, 2) for f in findings)
    score = max(0, 100 - penalty)

    lowered = jd_text.lower()
    is_junior = any(m in lowered for m in _lexicon()["intern_role_markers"])

    return BiasReport(
        score=score,
        findings=[asdict(f) for f in findings],
        summary=_summarise(findings, score),
        is_junior_role=is_junior,
    )


def _dedupe_spans(findings: list[Finding]) -> list[Finding]:
    """One finding per span of JD text.

    The same clause can trip several patterns — "5+ years experience" on an
    internship is both a seniority contradiction and generic narrow phrasing.
    Reporting it twice makes the panel look padded and buries the sharper
    finding, so the most severe reading wins. Input must already be sorted by
    severity. Findings with no span (skill-level simulations) always survive.
    """
    kept: list[Finding] = []
    claimed: set[tuple[int, int]] = set()
    for f in findings:
        if f.start < 0:
            kept.append(f)
            continue
        if any(f.start < end and start < f.end for start, end in claimed):
            continue
        claimed.add((f.start, f.end))
        kept.append(f)
    return kept


def _summarise(findings: list[Finding], score: int) -> str:
    if not findings:
        return "No biased or overly narrow phrasing detected in this job description."

    critical = sum(1 for f in findings if f.severity == "critical")
    high = sum(1 for f in findings if f.severity == "high")

    parts = [f"{len(findings)} issue{'' if len(findings) == 1 else 's'} found "
             f"(inclusivity score {score}/100)."]
    if critical:
        parts.append(f"{critical} critical.")
    if high:
        parts.append(f"{high} high severity.")

    worst = findings[0]
    parts.append(f"Most serious: {worst.label.lower()} — “{worst.matched_text}”.")
    return " ".join(parts)
