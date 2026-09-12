"""
"What if": turn a rejection into something the candidate can act on.

Most candidates hear nothing. The ones who hear something hear "we decided to
move forward with other applicants", which contains no information at all. This
module runs the evaluation engine backwards to produce the letter that would
actually have helped: which two or three gaps cost you this role, how far you
really were, and what to build next.

The key decision is how "cost you the most" is computed. The obvious approach —
rank the missing skills by weight — answers a different question, because a
heavily weighted skill everyone else is also missing costs nothing in a ranked
pool. So instead each gap is *simulated*: close it, re-rank the whole pool, and
measure how many places the candidate actually moves. What comes back is
competitive, not notional — "Docker would have moved you eleven places" is a
true statement about this pool, and it is the statement worth sending.

Everything here is templated from `backend/data/coaching.json` and the ontology.
No language model writes any of it, which matters more here than anywhere else in
the system: this text goes to a person, about their career, in the company's name.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from backend import config
from backend.core import taxonomy
from backend.core.fusion import (
    Candidate, MISSING, Primitives, SkillCell, WEAK, score,
)
from backend.core.skills import REQUIRED


@lru_cache(maxsize=1)
def _coaching() -> dict:
    return json.loads(config.COACHING_PATH.read_text(encoding="utf-8"))


def _entry(skill_id: str, cluster: str) -> dict:
    return (_coaching()["skills"].get(skill_id)
            or _coaching()["clusters"].get(cluster)
            or _coaching()["clusters"]["Other"])


@dataclass(slots=True)
class RoadmapStep:
    """One gap, what closing it is worth, and what to do about it."""
    skill_id: str
    label: str
    tier: str
    status: str
    places_gained: int          # measured by re-ranking the pool with this gap closed
    score_gain: float
    weeks: float
    human_time: str
    project: str
    first_step: str
    springboard: str            # the thing they already have to build from
    path: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Roadmap:
    doc_id: str
    name: str
    rank: int
    pool_size: int
    score: float
    points_from_shortlist: float
    steps: list[RoadmapStep] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    total_weeks: float = 0.0
    total_human: str = ""
    combined_places: int = 0        # places gained if EVERY step were completed
    combined_score_gain: float = 0.0
    headline: str = ""
    email: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

def _clone_with_skill_closed(primitives: list[Primitives], doc_id: str,
                             skill_ids: set[str]) -> list[Primitives]:
    """The pool as it would be if one candidate had these skills too.

    Only the target candidate's matrix changes, so the comparison is exactly the
    counterfactual the roadmap claims: everyone else stayed where they were.
    """
    from backend.core.fusion import aggregate

    out: list[Primitives] = []
    for p in primitives:
        if p.doc_id != doc_id:
            out.append(p)
            continue

        cells = []
        for c in p.cells:
            if c.skill_id in skill_ids:
                cells.append(SkillCell(
                    skill_id=c.skill_id, label=c.label, tier=c.tier,
                    weight=c.weight, cluster=c.cluster,
                    lex=config.LEX_EXACT_SCORE, lex_kind="exact",
                    sem_raw=c.sem_raw, sem_cal=c.sem_cal,
                    coverage=1.0, status="MATCHED",
                    evidence=c.evidence, start=c.start, end=c.end,
                    lex_eff=config.LEX_EXACT_SCORE, section=c.section,
                ))
            else:
                cells.append(c)

        twin = Primitives(
            doc_id=p.doc_id, name=p.name, filename=p.filename,
            bm25_raw=p.bm25_raw, bm25_norm=p.bm25_norm,
            docsim_raw=p.docsim_raw, docsim_norm=p.docsim_norm,
            lex_cov=0.0, sem_cov=0.0, req_coverage=0.0,
            gap_density=0.0, inferred_req_ratio=0.0,
            quality=p.quality, n_chunks=p.n_chunks, warnings=list(p.warnings),
            cells=cells, doc_multiplier=p.doc_multiplier,
        )
        aggregate(twin)
        out.append(twin)
    return out


def _simulate(primitives: list[Primitives], doc_id: str, skill_ids: set[str],
              alpha: float, gate: bool, baseline_rank: int,
              baseline_score: float) -> tuple[int, float]:
    """Places and points gained if these gaps were closed."""
    twin_pool = score(_clone_with_skill_closed(primitives, doc_id, skill_ids),
                      alpha=alpha, gate=gate)
    for c in twin_pool:
        if c.doc_id == doc_id:
            return baseline_rank - c.rank, round(c.score - baseline_score, 2)
    return 0, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Roadmap
# ─────────────────────────────────────────────────────────────────────────────

def for_candidate(cand: Candidate, primitives: list[Primitives],
                  pool_size: int, shortlist_score: float,
                  alpha: float = config.DEFAULT_ALPHA, gate: bool = False,
                  max_steps: int = 3) -> Roadmap:
    """Build an upskilling roadmap for one candidate who did not make the cut."""
    road = Roadmap(
        doc_id=cand.doc_id, name=cand.name, rank=cand.rank,
        pool_size=pool_size, score=cand.score,
        points_from_shortlist=round(max(shortlist_score - cand.score, 0.0), 1),
    )

    held = {c.skill_id: 1.0 for c in cand.primitives.cells
            if c.status in ("MATCHED", "INFERRED")}
    road.strengths = [c.label for c in cand.primitives.cells
                      if c.status == "MATCHED" and c.tier == REQUIRED][:5]

    gaps = [c for c in cand.primitives.cells if c.status in (MISSING, WEAK)]

    # Measure, don't assume. A required skill that the whole pool is missing
    # costs this candidate nothing, and telling them to go and learn it would be
    # advice that does not survive contact with the competition.
    measured: list[tuple[int, float, SkillCell]] = []
    for cell in gaps:
        places, points = _simulate(primitives, cand.doc_id, {cell.skill_id},
                                   alpha, gate, cand.rank, cand.score)
        measured.append((places, points, cell))

    measured.sort(key=lambda t: (-t[0], -t[1], t[2].tier != REQUIRED))

    for places, points, cell in measured[:max_steps]:
        if places <= 0 and points <= 0.01:
            continue
        springboards = {k: v for k, v in held.items() if k != cell.skill_id}
        bridge = taxonomy.bridge(cell.skill_id, springboards, cell.label)
        entry = _entry(cell.skill_id, cell.cluster)

        road.steps.append(RoadmapStep(
            skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
            status=cell.status, places_gained=places, score_gain=points,
            weeks=bridge.weeks, human_time=bridge.human,
            project=entry["project"], first_step=entry["first_step"],
            springboard=_label_of(cand, bridge.springboard),
            path=taxonomy.path_labels(cell.skill_id, cell.label),
        ))

    # What the whole roadmap is worth together. One skill rarely moves anyone in a
    # pool this tight — the honest, and far more motivating, number is what
    # closing the set does, so it is measured rather than added up from the parts.
    if road.steps:
        road.combined_places, road.combined_score_gain = _simulate(
            primitives, cand.doc_id, {s.skill_id for s in road.steps},
            alpha, gate, cand.rank, cand.score)

    road.total_weeks = taxonomy.combined_weeks([
        taxonomy.Bridge(target=s.skill_id, target_label=s.label, springboard=None,
                        springboard_label="", kind="", weeks=s.weeks, note="")
        for s in road.steps
    ])
    road.total_human = taxonomy.humanise_weeks(road.total_weeks)
    road.headline = _headline(road)
    road.email = compose_email(road)
    return road


def _label_of(cand: Candidate, skill_id: str | None) -> str:
    if not skill_id:
        return ""
    for c in cand.primitives.cells:
        if c.skill_id == skill_id:
            return c.label
    return skill_id


def _headline(road: Roadmap) -> str:
    if not road.steps:
        return (f"Ranked {road.rank} of {road.pool_size}. No single skill would have "
                f"changed the outcome — the gap here is breadth of evidence rather "
                f"than one missing tool.")

    lead = (f"Ranked {road.rank} of {road.pool_size}, {road.points_from_shortlist} points "
            f"off the shortlist.")
    names = _oxford([s.label for s in road.steps])

    # In a tight pool a single skill usually moves nobody. Saying "would have moved
    # them 0 places" is true and useless; the set is the actionable unit.
    if road.combined_places > 0:
        return (f"{lead} {names} together would have moved them "
                f"{road.combined_places} "
                f"{'place' if road.combined_places == 1 else 'places'}, to "
                f"#{road.rank - road.combined_places}.")
    if road.combined_score_gain > 0:
        return (f"{lead} {names} together are worth "
                f"{road.combined_score_gain:.1f} points — not enough to clear this "
                f"particular pool, but the largest gains available to them.")
    return (f"{lead} No combination of missing skills would have closed this gap; "
            f"the issue is depth of evidence rather than coverage.")


# ─────────────────────────────────────────────────────────────────────────────
# The letter
# ─────────────────────────────────────────────────────────────────────────────

def compose_email(road: Roadmap, role: str = "this role") -> str:
    """A rejection that is worth receiving.

    Deliberately does not quote the candidate's rank or score. A number is a
    comparison against other people they cannot see and cannot act on; the gaps
    and the time to close them are about them, and those are the parts that help.
    """
    lines: list[str] = []
    lines.append(f"Hello{' ' + road.name.split()[0] if road.name else ''},")
    lines.append("")
    lines.append(
        f"Thank you for applying for {role}. We are not taking your application "
        f"forward this time, but your profile was assessed against the same "
        f"criteria as every other applicant, and we would rather tell you what "
        f"those came down to than leave you guessing.")
    lines.append("")

    if road.strengths:
        lines.append(
            f"What already stands up: {_oxford(road.strengths)}. "
            f"That part of your profile was not the issue.")
        lines.append("")

    if road.steps:
        lines.append("What made the difference, in order of how much it mattered:")
        lines.append("")
        for i, step in enumerate(road.steps, start=1):
            lines.append(f"  {i}. {step.label} — about {step.human_time} to pick up"
                         + (f", building on your {step.springboard}."
                            if step.springboard else "."))
            lines.append(f"     Try this: {step.project}")
            lines.append("")
        if road.total_weeks:
            lines.append(
                f"Realistically that is around {road.total_human} of focused work in "
                f"total, and it would make a genuine difference to how this profile "
                f"reads for similar roles.")
            lines.append("")
    else:
        lines.append(
            "There was no single missing skill here — the profile reads as early "
            "rather than mismatched. Depth on one or two projects will do more "
            "than adding more tools to the list.")
        lines.append("")

    lines.append("We would be glad to see an application from you again.")
    lines.append("")
    lines.append("Best regards")
    return "\n".join(lines)


def _oxford(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"


def for_rejected(candidates: list[Candidate], primitives: list[Primitives],
                 shortlist_size: int | None = None,
                 alpha: float = config.DEFAULT_ALPHA, gate: bool = False,
                 limit: int | None = None) -> list[Roadmap]:
    """Roadmaps for everyone below the shortlist cut."""
    cut = config.RAMPUP_TOP_N if shortlist_size is None else shortlist_size
    if not candidates:
        return []

    shortlist_score = candidates[min(cut, len(candidates)) - 1].score
    rejected = candidates[cut:]
    if limit is not None:
        rejected = rejected[:limit]

    return [
        for_candidate(c, primitives, len(candidates), shortlist_score, alpha, gate)
        for c in rejected
    ]
