"""
Adversarial resume detection: is this claim backed by anything?

Two attacks, two answers.

**Keyword stuffing.** The cheapest way to beat a keyword matcher is to paste the
job description into a Skills block. The tell is structural and does not require
judgement: a genuine skill leaves a trace in the work — it appears in a project
or a role as well as in the inventory. A stuffed one appears *only* as a tag.
So for every skill the resume names, we ask where else it shows up. A skill that
lives exclusively in the inventory is an **orphan claim**, and its lexical
evidence is discounted rather than deleted — a terse resume is not a dishonest
one, and the recruiter is told which reading we applied.

**Invisible text.** White-on-white or one-point keyword dumps. Detected upstream
in `parser.find_hidden()` by reading span attributes; priced here. This one is
penalised hard, because unlike a thin Skills section there is no innocent reason
for it.

Everything this module concludes is reported with the evidence that produced it.
"Flagged as gaming the ATS" is a serious thing to put next to a person's name,
and it is never the model's opinion — it is a list of skills and where they were
and were not found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from backend import config
from backend.core.parser import ParsedDoc
from backend.core.skills import Skill

# Sections where a claim is merely declared.
INVENTORY_SECTIONS = frozenset({"SKILLS", "INTERESTS", "SUMMARY"})
# Sections where a claim is demonstrated.
EVIDENCE_SECTIONS = frozenset({"EXPERIENCE", "PROJECTS", "ACHIEVEMENTS", "CERTIFICATIONS"})


@dataclass(slots=True)
class SkillSupport:
    """Where one named skill actually appears in one resume."""
    skill_id: str
    label: str
    inventory_hits: int = 0
    evidence_hits: int = 0
    other_hits: int = 0
    sections: list[str] = field(default_factory=list)

    @property
    def named(self) -> bool:
        return (self.inventory_hits + self.evidence_hits + self.other_hits) > 0

    @property
    def orphan(self) -> bool:
        """Declared in the inventory and nowhere that counts as work."""
        return self.inventory_hits > 0 and self.evidence_hits == 0 and self.other_hits == 0


@dataclass(slots=True)
class IntegrityReport:
    """One candidate's integrity picture, and the numbers behind it."""
    doc_id: str
    support: dict[str, SkillSupport] = field(default_factory=dict)
    orphans: list[str] = field(default_factory=list)      # labels, for display
    supported: list[str] = field(default_factory=list)
    named_total: int = 0
    orphan_ratio: float = 0.0
    stuffing_flag: bool = False
    hidden_flag: bool = False
    hidden_samples: list[str] = field(default_factory=list)
    doc_multiplier: float = 1.0                            # applied to the whole candidate
    headline: str = ""
    detail: list[str] = field(default_factory=list)

    def penalty_for(self, skill_id: str) -> float:
        """Lexical discount for one skill on this resume."""
        s = self.support.get(skill_id)
        if s and s.orphan and config.INTEGRITY_ENABLED:
            return config.ORPHAN_SKILL_PENALTY
        return 1.0

    @property
    def clean(self) -> bool:
        return not self.stuffing_flag and not self.hidden_flag


# ─────────────────────────────────────────────────────────────────────────────
# Matching
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=2048)
def _form_pattern(form: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(form)}(?![A-Za-z0-9])")


def _mentions(chunk_canonical: str, skill: Skill) -> bool:
    return any(_form_pattern(f).search(chunk_canonical)
               for f in dict.fromkeys(skill.surface_forms) if f)


def _bucket(section: str) -> str:
    if section in INVENTORY_SECTIONS:
        return "inventory"
    if section in EVIDENCE_SECTIONS:
        return "evidence"
    return "other"


# ─────────────────────────────────────────────────────────────────────────────
# Analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyse(doc: ParsedDoc, skill_set: list[Skill]) -> IntegrityReport:
    """Locate every named skill in one resume and judge whether it is supported."""
    report = IntegrityReport(doc_id=doc.doc_id)

    for skill in skill_set:
        support = SkillSupport(skill_id=skill.id, label=skill.label)
        for chunk in doc.chunks:
            if not _mentions(chunk.canonical, skill):
                continue
            bucket = _bucket(chunk.section)
            if bucket == "inventory":
                support.inventory_hits += 1
            elif bucket == "evidence":
                support.evidence_hits += 1
            else:
                support.other_hits += 1
            if chunk.section not in support.sections:
                support.sections.append(chunk.section)
        report.support[skill.id] = support

    named = [s for s in report.support.values() if s.named]
    orphans = [s for s in named if s.orphan]

    report.named_total = len(named)
    report.orphans = [s.label for s in orphans]
    report.supported = [s.label for s in named if not s.orphan]
    report.orphan_ratio = round(len(orphans) / len(named), 3) if named else 0.0

    # A resume with no sections at all cannot be judged this way: everything lands
    # in "other" and no claim can be called an orphan. Staying silent is correct —
    # the messy-format candidate is disorganised, not dishonest.
    has_inventory = any(s.inventory_hits for s in named)

    report.stuffing_flag = bool(
        config.INTEGRITY_ENABLED
        and has_inventory
        and len(orphans) >= config.ORPHAN_FLAG_MIN_COUNT
        and report.orphan_ratio >= config.ORPHAN_FLAG_MIN_RATIO
    )

    report.hidden_flag = bool(doc.hidden)
    report.hidden_samples = [f"{h.text[:90]} ({h.detail})" for h in doc.hidden[:3]]
    if report.hidden_flag and config.INTEGRITY_ENABLED:
        report.doc_multiplier = config.HIDDEN_TEXT_PENALTY

    report.headline, report.detail = _narrate(report, orphans)
    return report


def _narrate(report: IntegrityReport, orphans: list[SkillSupport]) -> tuple[str, list[str]]:
    """Say what was found, in the order a reviewer would want to hear it."""
    detail: list[str] = []

    if report.hidden_flag:
        headline = "Invisible text found in the PDF"
        detail.append(
            f"{len(report.hidden_samples)} span(s) of text are present in the file but "
            f"cannot be seen when it is read — white-on-white or sub-legible type. "
            f"This is the classic ATS keyword injection and the score is penalised "
            f"by {int((1 - config.HIDDEN_TEXT_PENALTY) * 100)}%."
        )
        detail.extend(f"Hidden: “{s}”" for s in report.hidden_samples)
    elif report.stuffing_flag:
        headline = f"{len(orphans)} of {report.named_total} named skills have no supporting work"
        detail.append(
            f"{', '.join(report.orphans[:8])}"
            f"{' and more' if len(report.orphans) > 8 else ''} appear only in the skills "
            f"inventory — never in a project, a role or an achievement. Their keyword "
            f"evidence is discounted to {int(config.ORPHAN_SKILL_PENALTY * 100)}%."
        )
    elif orphans:
        headline = "Mostly well-supported claims"
        detail.append(
            f"{len(orphans)} of {report.named_total} named skills sit only in the "
            f"inventory ({', '.join(report.orphans[:5])}); the rest are backed by "
            f"project or role text."
        )
    elif report.named_total:
        headline = "Every named skill is backed by worked evidence"
        detail.append(
            f"All {report.named_total} skills this resume names also appear in a "
            f"project, role or achievement."
        )
    else:
        headline = "No named skills to verify"
        detail.append("This resume names none of the skills the JD asks for.")

    return headline, detail


def analyse_many(docs: list[ParsedDoc], skill_set: list[Skill]) -> dict[str, IntegrityReport]:
    return {d.doc_id: analyse(d, skill_set) for d in docs}
