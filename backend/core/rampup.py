"""
Bridge time: how long until this candidate's gaps stop mattering?

A shortlist that says "missing Docker" has told the recruiter almost nothing. The
question they actually have is whether that gap is a fortnight of onboarding or a
reason not to interview — and for an internship, where nobody arrives complete,
that is the whole decision.

So for every gap on a shortlisted candidate we find the nearest thing they can
already do, and price the walk between the two using the ontology in
`taxonomy.py`. "Missing Vue, knows React, about a week" is an actionable
sentence. "Missing Vue" is not.

Two things keep this honest:

  * The springboard is always named. The estimate is never a bare number — it
    comes with the skill it was measured from and the reason, so a recruiter who
    disagrees can see exactly which assumption to argue with.

  * Gaps are aggregated with decay, not summed. Someone picking up three adjacent
    tools does the second and third faster than the first, and a naive sum would
    turn a strong candidate with three small gaps into a fictional six-month
    project.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend import config
from backend.core import taxonomy
from backend.core.fusion import Candidate, INFERRED, MATCHED, MISSING, WEAK
from backend.core.skills import REQUIRED


@dataclass(slots=True)
class Gap:
    """One missing skill, and what it would take to close it."""
    skill_id: str
    label: str
    tier: str
    status: str                 # WEAK | MISSING
    weeks: float
    human: str                  # "~1 week", "2-3 weeks", "2+ months"
    springboard: str            # skill they already have, as a label
    springboard_held: str       # how firmly: "stated" | "demonstrated" | "none"
    kind: str                   # sibling | family | domain | discipline | unrelated
    note: str
    path: list[str] = field(default_factory=list)      # ontological walk, for the UI
    pinned: bool = False


@dataclass(slots=True)
class RampUp:
    """One candidate's full ramp-up picture."""
    doc_id: str
    name: str
    rank: int
    gaps: list[Gap] = field(default_factory=list)
    total_weeks: float = 0.0
    total_human: str = "already there"
    headline: str = ""
    verdict: str = ""           # ready | short | moderate | long

    @property
    def productive_from(self) -> str:
        return self.total_human


# How firmly a candidate holds a skill, as a number the bridge model can use.
# A demonstrated skill is a better launch pad than a merely-stated one.
_HOLD_STRENGTH = {
    MATCHED: 1.0,
    INFERRED: 0.75,
    WEAK: 0.4,
}

_HOLD_LABEL = {
    MATCHED: "stated and evidenced",
    INFERRED: "demonstrated without being named",
    WEAK: "adjacent experience only",
}


def _held_skills(cand: Candidate) -> dict[str, float]:
    """Everything this candidate can build from, with how firmly they hold it."""
    held: dict[str, float] = {}
    for cell in cand.primitives.cells:
        strength = _HOLD_STRENGTH.get(cell.status)
        if strength is None:
            continue
        # An evidence cell is about the JD's skills, so a candidate's wider stack
        # is invisible here — which understates springboards but never invents
        # one. Erring towards a longer estimate is the safe direction.
        held[cell.skill_id] = max(held.get(cell.skill_id, 0.0), strength)
    return held


def _hold_label(cand: Candidate, skill_id: str) -> str:
    for cell in cand.primitives.cells:
        if cell.skill_id == skill_id:
            return _HOLD_LABEL.get(cell.status, "held")
    return "held"


def for_candidate(cand: Candidate) -> RampUp:
    """Estimate ramp-up for every gap this candidate has."""
    out = RampUp(doc_id=cand.doc_id, name=cand.name, rank=cand.rank)
    held = _held_skills(cand)

    # Worst gaps first: required before preferred, then by how large the hole is.
    holes = [
        c for c in cand.primitives.cells
        if c.status in (MISSING, WEAK)
    ]
    holes.sort(key=lambda c: (c.tier != REQUIRED, -c.weight, c.coverage, c.label.lower()))

    for cell in holes[:config.RAMPUP_MAX_GAPS]:
        # Don't offer the gap itself as its own springboard.
        springboards = {k: v for k, v in held.items() if k != cell.skill_id}
        bridge = taxonomy.bridge(cell.skill_id, springboards, cell.label)

        out.gaps.append(Gap(
            skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
            status=cell.status,
            weeks=bridge.weeks, human=bridge.human,
            springboard=_label_for(cand, bridge.springboard),
            springboard_held=(_hold_label(cand, bridge.springboard)
                              if bridge.springboard else "none"),
            kind=bridge.kind, note=bridge.note,
            path=taxonomy.path_labels(cell.skill_id, cell.label),
            pinned=bridge.pinned,
        ))

    out.total_weeks = taxonomy.combined_weeks(
        [taxonomy.Bridge(target=g.skill_id, target_label=g.label, springboard=None,
                         springboard_label="", kind=g.kind, weeks=g.weeks, note="")
         for g in out.gaps])
    out.total_human = taxonomy.humanise_weeks(out.total_weeks)
    out.headline, out.verdict = _narrate(out, cand)
    return out


def _label_for(cand: Candidate, skill_id: str | None) -> str:
    if not skill_id:
        return "no adjacent experience"
    for cell in cand.primitives.cells:
        if cell.skill_id == skill_id:
            return cell.label
    return skill_id


def _narrate(ramp: RampUp, cand: Candidate) -> tuple[str, str]:
    required_gaps = [g for g in ramp.gaps if g.tier == REQUIRED]

    if not ramp.gaps:
        return ("Meets every requirement the JD states — no ramp-up needed.", "ready")

    weeks = ramp.total_weeks
    if weeks <= 2.0:
        verdict = "short"
        shape = "productive on the full stack almost immediately"
    elif weeks <= 6.0:
        verdict = "moderate"
        shape = "productive on their strengths from day one, fully ramped within the internship"
    else:
        verdict = "long"
        shape = "would spend a meaningful part of the internship learning rather than shipping"

    # Name the gaps the count is actually counting, or the sentence contradicts
    # itself: "1 required gap (Responsive Design, Agile, AWS)".
    shown = required_gaps or ramp.gaps
    tier_word = "required" if required_gaps else "preferred"
    gap_names = ", ".join(g.label for g in shown[:3])
    if len(shown) > 3:
        gap_names += f" and {len(shown) - 3} more"

    lead = f"{len(shown)} {tier_word} {'gap' if len(shown) == 1 else 'gaps'}"

    return (
        f"{lead} ({gap_names}) — about {ramp.total_human} to bridge, {shape}.",
        verdict,
    )


def top(candidates: list[Candidate], n: int | None = None) -> list[RampUp]:
    """Ramp-up for the shortlist. Defaults to the top 3 the brief asks for."""
    limit = config.RAMPUP_TOP_N if n is None else n
    return [for_candidate(c) for c in candidates[:limit]]
