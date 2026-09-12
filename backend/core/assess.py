"""
Assembly: turn the evidence matrix into a judged pool.

Three subsystems want to change what a piece of evidence is worth, and all three
need the matrix to exist before they can decide:

    integrity      is this claim supported anywhere in the resume itself?
    enrichment     does public code prove it?
    verification   does the resume claim something the code contradicts?

So the order is fixed and it matters:

    prepare()  ->  integrity + enrichment  ->  verification  ->  adjust()  ->  score()

`adjust()` is the only thing that mutates the matrix, and it is handed a single
`Adjustment` per candidate built here. That keeps every multiplier in the system
visible in one function instead of scattered across the modules that computed
them — when a recruiter asks why a number moved, this is the file that answers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend import config
from backend.core import enrich, integrity, verification
from backend.core.fusion import Adjustment, Primitives
from backend.core.parser import ParsedDoc
from backend.core.skills import Skill


@dataclass(slots=True)
class Assessment:
    """Everything the non-scoring panels need, keyed by doc_id."""
    integrity: dict[str, integrity.IntegrityReport] = field(default_factory=dict)
    enrichment: dict[str, enrich.Enrichment] = field(default_factory=dict)
    verification: dict[str, verification.VerificationReport] = field(default_factory=dict)
    adjustments: dict[str, Adjustment] = field(default_factory=dict)
    enrichment_enabled: bool = False

    def for_doc(self, doc_id: str):
        return (self.integrity.get(doc_id),
                self.enrichment.get(doc_id),
                self.verification.get(doc_id))


def build(docs: list[ParsedDoc], skill_set: list[Skill], primitives: list[Primitives],
          enrichment_enabled: bool = False) -> Assessment:
    """Run every judgement pass and collapse the results into per-candidate multipliers."""
    out = Assessment(enrichment_enabled=enrichment_enabled)

    out.integrity = integrity.analyse_many(docs, skill_set)
    out.enrichment = enrich.enrich_many(docs, enrichment_enabled)
    out.verification = verification.verify_many(primitives, out.enrichment, out.integrity)

    for p in primitives:
        out.adjustments[p.doc_id] = _adjustment_for(
            p,
            out.integrity.get(p.doc_id),
            out.enrichment.get(p.doc_id),
            out.verification.get(p.doc_id),
        )
    return out


def _adjustment_for(p: Primitives,
                    integ: integrity.IntegrityReport | None,
                    ext: enrich.Enrichment | None,
                    ver: verification.VerificationReport | None) -> Adjustment:
    """Collapse three reports into the multipliers for one candidate."""
    adj = Adjustment()

    if integ is not None:
        adj.doc_multiplier = integ.doc_multiplier
        for cell in p.cells:
            mult = integ.penalty_for(cell.skill_id)
            if mult != 1.0:
                adj.integrity_multipliers[cell.skill_id] = mult
                adj.skill_notes.setdefault(cell.skill_id, []).append(
                    "Named only in the skills list — no project or role uses it.")

    if ver is not None:
        for cell in p.cells:
            verdict = ver.verdict_for(cell.skill_id)
            if cell.lex <= 0.0:
                continue
            adj.skill_verdicts[cell.skill_id] = verdict

            mult = ver.multiplier_for(cell.skill_id)
            if mult != 1.0:
                adj.skill_multipliers[cell.skill_id] = mult

            if verdict == verification.CORROBORATED:
                adj.skill_notes.setdefault(cell.skill_id, []).append(
                    "Proven by public code.")
            elif verdict == verification.CONTRADICTED:
                adj.skill_notes.setdefault(cell.skill_id, []).append(
                    "Claimed on the resume but absent from every public repository.")

    if ext is not None:
        for cell in p.cells:
            evidence = ext.evidence_for(cell.skill_id)
            if evidence:
                adj.skill_evidence[cell.skill_id] = [e.as_sentence() for e in evidence[:4]]

            # LinkedIn can only ever support a claim the resume already makes, and
            # only when the stronger source stayed silent. It never creates
            # evidence and never outranks code.
            if (cell.lex > 0.0
                    and cell.skill_id in ext.linkedin.skills
                    and cell.skill_id not in ext.github.skills
                    and cell.skill_id not in adj.skill_multipliers):
                adj.skill_multipliers[cell.skill_id] = config.LINKEDIN_SCORE_MULTIPLIER
                adj.skill_notes.setdefault(cell.skill_id, []).append(
                    "Listed on a supplied LinkedIn profile (self-reported).")

    return adj
