"""
Explanations that cannot be wrong.

The explainer reads the SAME evidence matrix the scorer used. It has no access to
a language model and no ability to introduce a fact — every sentence it emits is
assembled from cells that already determined the score. If it claims a skill
matched, that claim came from the cell whose value moved the number, and the
cell carries the character span proving it.

This is what makes explanations faithful by construction rather than by hope.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.fusion import (
    Candidate, SkillCell, MATCHED, INFERRED, WEAK, MISSING,
    HIDDEN_GEM, SURFACE_MATCH, cells_by_status,
)
from backend.core.skills import REQUIRED


@dataclass(slots=True)
class Explanation:
    doc_id: str
    name: str
    rank: int
    score: float
    headline: str
    bullets: list[str] = field(default_factory=list)
    matched: list[dict] = field(default_factory=list)
    inferred: list[dict] = field(default_factory=list)
    weak: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Grammar helpers — small, but they are the difference between output that reads
# as written and output that reads as generated.
# ─────────────────────────────────────────────────────────────────────────────

def oxford(items: list[str], conj: str = "and", limit: int | None = None) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if limit and len(items) > limit:
        # Truncated lists end in "and N more", so the remaining items are joined
        # with plain commas — adding a conjunction too produces "X and Y and 8 more".
        return f"{', '.join(items[:limit])} and {len(items) - limit} more"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return f"{', '.join(items[:-1])} {conj} {items[-1]}"


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return singular if n == 1 else (plural_form or f"{singular}s")


def _cell_dict(c: SkillCell) -> dict:
    return {
        "skill_id": c.skill_id, "label": c.label, "tier": c.tier,
        "status": c.status, "lex": round(c.lex, 3), "lex_kind": c.lex_kind,
        "sem_raw": round(c.sem_raw, 3), "sem_cal": round(c.sem_cal, 3),
        "coverage": round(c.coverage, 3),
        "evidence": c.evidence, "start": c.start, "end": c.end,
    }


def _trim(text: str, limit: int = 150) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ─────────────────────────────────────────────────────────────────────────────
# Explanation
# ─────────────────────────────────────────────────────────────────────────────

def explain(cand: Candidate, pool_size: int) -> Explanation:
    p = cand.primitives

    matched = cells_by_status(p, MATCHED)
    inferred = cells_by_status(p, INFERRED)
    weak = cells_by_status(p, WEAK)
    missing = cells_by_status(p, MISSING)

    req_cells = [c for c in p.cells if c.tier == REQUIRED]
    req_met = [c for c in req_cells if c.status in (MATCHED, INFERRED)]
    req_missing = [c for c in req_cells if c.status == MISSING]

    # --- headline ---
    # Ranked by semantic salience, not alphabetically. Every strong candidate
    # states CSS and Git, so leading with those says nothing about this person;
    # the skills with the richest supporting evidence are what distinguish them.
    top_matched = [
        c.label for c in sorted(
            (c for c in matched if c.tier == REQUIRED),
            key=lambda c: -c.sem_raw,
        )
    ][:3]
    headline = (
        f"Ranked #{cand.rank} of {pool_size} with a match score of {cand.score:.1f}. "
        f"Meets {len(req_met)} of {len(req_cells)} required "
        f"{plural(len(req_cells), 'skill')}"
    )
    headline += f", including {oxford(top_matched)}." if top_matched else "."

    bullets: list[str] = []

    # --- what was stated outright ---
    if matched:
        stated = [c.label for c in matched]
        typo_fixed = [c for c in matched if c.lex_kind == "fuzzy"]
        line = f"States {len(stated)} {plural(len(stated), 'skill')} explicitly: {oxford(stated, limit=8)}."
        if typo_fixed:
            line += (
                f" {oxford([c.label for c in typo_fixed])} "
                f"{plural(len(typo_fixed), 'was', 'were')} matched through a spelling variant."
            )
        bullets.append(line)

    # --- the semantic channel's contribution, quoted ---
    for cell in inferred[:3]:
        bullets.append(
            f"{cell.label} is never named, but the semantic channel found it "
            f"(similarity {cell.sem_raw:.2f}): “{_trim(cell.evidence)}”"
        )

    # --- gaps ---
    if req_missing:
        bullets.append(
            f"No evidence for {len(req_missing)} required "
            f"{plural(len(req_missing), 'skill')}: {oxford([c.label for c in req_missing])}."
        )
    if weak:
        bullets.append(
            f"Only adjacent experience for {oxford([c.label for c in weak], limit=5)}."
        )

    # --- how the two channels saw this candidate ---
    bullets.append(_channel_line(cand))

    # --- parse honesty ---
    if p.quality < 0.5:
        bullets.append(
            f"Parse quality was low ({p.quality:.2f}) — this ranking may understate the "
            f"candidate. Recommend manual review."
        )

    return Explanation(
        doc_id=cand.doc_id, name=cand.name, rank=cand.rank, score=cand.score,
        headline=headline, bullets=bullets,
        matched=[_cell_dict(c) for c in matched],
        inferred=[_cell_dict(c) for c in inferred],
        weak=[_cell_dict(c) for c in weak],
        missing=[_cell_dict(c) for c in missing],
    )


def _channel_line(cand: Candidate) -> str:
    k, m = cand.k_score, cand.m_score
    base = f"Keyword channel {k:.2f}, semantic channel {m:.2f}"

    if cand.flag == HIDDEN_GEM:
        return (
            f"{base}. Flagged HIDDEN GEM: required skills are demonstrably practised "
            f"but not stated in the job description's vocabulary, so a keyword-only "
            f"filter would rank this candidate {abs(cand.rank_delta)} "
            f"{plural(abs(cand.rank_delta), 'place')} lower than the evidence warrants."
        )
    if cand.flag == SURFACE_MATCH:
        return (
            f"{base}. Flagged SURFACE MATCH: the resume names the required skills but "
            f"shows little supporting work, so lexical coverage runs well ahead of "
            f"semantic coverage. Worth probing in interview."
        )
    return f"{base}. Both channels agree (lexical #{cand.rank_lexical}, semantic #{cand.rank_semantic})."


def explain_top(cands: list[Candidate], n: int = 3) -> list[Explanation]:
    """The brief requires explanations for the top 3."""
    return [explain(c, len(cands)) for c in cands[:n]]


# ─────────────────────────────────────────────────────────────────────────────
# Comparison — powers both the diff tool and the chat layer
# ─────────────────────────────────────────────────────────────────────────────

def compare(a: Candidate, b: Candidate) -> dict:
    """Why does A rank above B? Every number read from the matrix."""
    if a.score < b.score:
        a, b = b, a

    a_cells = {c.skill_id: c for c in a.primitives.cells}
    b_cells = {c.skill_id: c for c in b.primitives.cells}
    held = (MATCHED, INFERRED)

    a_only = [c.label for sid, c in a_cells.items()
              if c.status in held and b_cells.get(sid) and b_cells[sid].status not in held]
    b_only = [c.label for sid, c in b_cells.items()
              if c.status in held and a_cells.get(sid) and a_cells[sid].status not in held]

    lines = [
        f"{a.name} ranks above {b.name} by {a.score - b.score:.1f} points "
        f"({a.score:.1f} vs {b.score:.1f})."
    ]

    dk, dm = a.k_score - b.k_score, a.m_score - b.m_score
    lines.append(
        f"Keyword channel: {a.k_score:.2f} vs {b.k_score:.2f} ({dk:+.2f}). "
        f"Semantic channel: {a.m_score:.2f} vs {b.m_score:.2f} ({dm:+.2f})."
    )

    if a_only:
        lines.append(f"{a.name} covers {oxford(a_only, limit=6)}, which {b.name} does not.")
    if b_only:
        lines.append(f"{b.name} covers {oxford(b_only, limit=6)}, which {a.name} does not.")
    if not a_only and not b_only:
        lines.append("Both cover the same skill set; the gap comes from depth of evidence rather than coverage.")

    ar, br = a.primitives.req_coverage, b.primitives.req_coverage
    lines.append(
        f"Required-skill coverage: {ar:.0%} vs {br:.0%}."
    )

    for cand in (a, b):
        if cand.flag == HIDDEN_GEM:
            lines.append(
                f"Note: {cand.name} is flagged HIDDEN GEM — semantic rank "
                f"#{cand.rank_semantic} against lexical rank #{cand.rank_lexical}, "
                f"suggesting relevant experience described in different terms."
            )
        elif cand.flag == SURFACE_MATCH:
            lines.append(
                f"Note: {cand.name} is flagged SURFACE MATCH — skills are named but "
                f"thinly evidenced."
            )

    return {
        "winner": a.doc_id,
        "answer": " ".join(lines),
        "lines": lines,
        "a": {"doc_id": a.doc_id, "name": a.name, "score": a.score,
              "k": a.k_score, "m": a.m_score, "only": a_only},
        "b": {"doc_id": b.doc_id, "name": b.name, "score": b.score,
              "k": b.k_score, "m": b.m_score, "only": b_only},
    }
