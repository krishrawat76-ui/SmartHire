"""
Recruiter QA layer — 100% deterministic. No LLM, no API key, no network.

    query -> normalise -> classify intent -> resolve entities -> realise template

Every number in every answer is read straight out of the evidence matrix. Nothing
is generated, so nothing can be invented. When a judge asks "how does your
matching actually work", this module answers that question inside the product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from backend.core import explainer
from backend.core.fusion import (
    Candidate, MATCHED, INFERRED, WEAK, MISSING,
    HIDDEN_GEM, SURFACE_MATCH, cells_by_status,
)
from backend.core.skills import Skill, REQUIRED

COMPARE, WHY, MISSING_INTENT, WHO_HAS, TOP_N, FALLBACK = (
    "COMPARE", "WHY", "MISSING", "WHO_HAS", "TOP_N", "FALLBACK"
)

_NAME_MATCH_CUTOFF = 82
_SKILL_MATCH_CUTOFF = 86


@dataclass(slots=True)
class ChatResponse:
    intent: str
    answer: str
    refs: list[str] = field(default_factory=list)     # doc_ids the answer cites
    data: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Intent patterns. Order matters: the first match wins, so the most specific
# phrasings are listed before the general ones.
# ─────────────────────────────────────────────────────────────────────────────

_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (COMPARE, re.compile(
        r"\b(compare|versus|vs\.?)\b|"
        r"\bwhy is .+ (above|over|ahead of|better than|higher than|ranked higher)\b|"
        r"\b(difference|differ) between\b", re.I)),
    (MISSING_INTENT, re.compile(
        r"\b(missing|lack(s|ing)?|gap(s)?|weakness(es)?|short on|doesn'?t have|does not have|"
        r"what.{0,12}(need|lack))\b", re.I)),
    (WHO_HAS, re.compile(
        r"\bwho\b.{0,24}\b(has|have|knows?|can|with|experience)\b|"
        r"\b(which|any) candidates?\b|\bwho else\b", re.I)),
    (TOP_N, re.compile(
        r"\btop\s*\d*\b|\bbest\b|\bshortlist\b|\branking\b|\bstrongest\b|"
        r"\bwho should (i|we)\b", re.I)),
    (WHY, re.compile(
        r"\bwhy\b|\bexplain\b|\bjustif(y|ication)\b|\breason\b|\bhow did\b|"
        r"\btell me about\b", re.I)),
]


def classify(query: str) -> str:
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(query):
            return intent
    return FALLBACK


# ─────────────────────────────────────────────────────────────────────────────
# Entity resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_candidates(query: str, cands: list[Candidate]) -> list[Candidate]:
    """Find candidates named in the query, in the order they are mentioned.

    Matches full names, first names and surnames, so "why is Priya above Rohan"
    resolves without the recruiter typing anything formal.
    """
    hits: list[tuple[int, Candidate]] = []
    lowered = query.lower()

    for cand in cands:
        parts = [cand.name.lower()] + cand.name.lower().split()
        best_pos = None
        for part in parts:
            if len(part) < 3:
                continue
            m = re.search(rf"(?<![a-z]){re.escape(part)}(?![a-z])", lowered)
            if m and (best_pos is None or m.start() < best_pos):
                best_pos = m.start()
        if best_pos is not None:
            hits.append((best_pos, cand))
            continue

        # Fuzzy fallback for misspelled names.
        tokens = [t for t in re.findall(r"[a-z]{3,}", lowered)]
        if tokens:
            match = process.extractOne(
                cand.name.split()[0].lower(), tokens,
                scorer=fuzz.ratio, score_cutoff=_NAME_MATCH_CUTOFF,
            )
            if match:
                hits.append((lowered.find(match[0]), cand))

    hits.sort(key=lambda h: h[0])
    seen, out = set(), []
    for _, cand in hits:
        if cand.doc_id not in seen:
            seen.add(cand.doc_id)
            out.append(cand)
    return out


def resolve_skill(query: str, skills: list[Skill]) -> Skill | None:
    lowered = query.lower()
    best: tuple[float, Skill] | None = None

    for skill in skills:
        for form in skill.surface_forms:
            if len(form) < 2:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", lowered):
                # Longest literal match wins: "rest api" beats "api".
                if best is None or len(form) > best[0]:
                    best = (len(form), skill)
    if best:
        return best[1]

    tokens = re.findall(r"[a-z0-9.+#]{3,}", lowered)
    if not tokens:
        return None
    for skill in skills:
        match = process.extractOne(
            skill.label.lower(), tokens, scorer=fuzz.ratio, score_cutoff=_SKILL_MATCH_CUTOFF
        )
        if match:
            return skill
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Realisation
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_PHRASE = {
    MATCHED: "states it explicitly",
    INFERRED: "shows it without naming it",
    WEAK: "has only adjacent experience",
    MISSING: "shows no evidence",
}


def answer(query: str, cands: list[Candidate], skills: list[Skill]) -> ChatResponse:
    if not cands:
        return ChatResponse(FALLBACK, "No candidates have been analysed yet. "
                                      "Upload a job description and a batch of resumes first.")

    intent = classify(query)
    named = resolve_candidates(query, cands)

    if intent == COMPARE:
        return _do_compare(named, cands)
    if intent == MISSING_INTENT:
        return _do_missing(named, cands)
    if intent == WHO_HAS:
        return _do_who_has(query, cands, skills)
    if intent == TOP_N:
        return _do_top(query, cands)
    if intent == WHY:
        return _do_why(named, cands)
    return _do_fallback(named, cands)


def _do_compare(named: list[Candidate], cands: list[Candidate]) -> ChatResponse:
    if len(named) < 2:
        if len(named) == 1:
            return ChatResponse(
                COMPARE,
                f"I found {named[0].name} in that question but not a second candidate to "
                f"compare against. Try “why is {named[0].name} above "
                f"{cands[min(named[0].rank, len(cands) - 1)].name}?”",
                refs=[named[0].doc_id],
            )
        return ChatResponse(
            COMPARE,
            "Name two candidates to compare — for example "
            f"“why is {cands[0].name} ranked above {cands[1].name}?”",
        )

    result = explainer.compare(named[0], named[1])
    return ChatResponse(COMPARE, result["answer"],
                        refs=[named[0].doc_id, named[1].doc_id], data=result)


def _do_why(named: list[Candidate], cands: list[Candidate]) -> ChatResponse:
    if not named:
        return ChatResponse(
            WHY,
            f"Which candidate? For example “why did {cands[0].name} rank first?”",
        )
    exp = explainer.explain(named[0], len(cands))
    body = " ".join([exp.headline] + exp.bullets)
    return ChatResponse(WHY, body, refs=[named[0].doc_id],
                        data={"headline": exp.headline, "bullets": exp.bullets})


def _do_missing(named: list[Candidate], cands: list[Candidate]) -> ChatResponse:
    if not named:
        return ChatResponse(
            MISSING_INTENT,
            f"Which candidate's gaps? For example “what is {cands[0].name} missing?”",
        )
    cand = named[0]
    missing = [c for c in cells_by_status(cand.primitives, MISSING) if c.tier == REQUIRED]
    weak = cells_by_status(cand.primitives, WEAK)
    inferred = cells_by_status(cand.primitives, INFERRED)

    parts = []
    if missing:
        parts.append(
            f"{cand.name} shows no evidence of "
            f"{explainer.oxford([c.label for c in missing])} "
            f"({len(missing)} required {explainer.plural(len(missing), 'skill')})."
        )
    else:
        parts.append(f"{cand.name} has no outright gaps on the required skills.")

    if weak:
        parts.append(
            f"Adjacent-only experience for {explainer.oxford([c.label for c in weak], limit=5)}."
        )
    if inferred:
        parts.append(
            f"Worth noting: {explainer.oxford([c.label for c in inferred], limit=4)} "
            f"{explainer.plural(len(inferred), 'is', 'are')} demonstrated in the resume "
            f"without being named, so a keyword filter would have scored "
            f"{explainer.plural(len(inferred), 'it', 'them')} as missing."
        )
    parts.append(f"Required-skill coverage: {cand.primitives.req_coverage:.0%}.")

    return ChatResponse(MISSING_INTENT, " ".join(parts), refs=[cand.doc_id],
                        data={"missing": [c.label for c in missing],
                              "weak": [c.label for c in weak],
                              "inferred": [c.label for c in inferred]})


def _do_who_has(query: str, cands: list[Candidate], skills: list[Skill]) -> ChatResponse:
    skill = resolve_skill(query, skills)
    if not skill:
        return ChatResponse(
            WHO_HAS,
            "I could not tell which skill you meant. Try naming one from the job "
            f"description, such as “who knows {skills[0].label}?”" if skills else
            "No skills have been extracted yet.",
        )

    stated, shown = [], []
    for cand in cands:
        cell = next((c for c in cand.primitives.cells if c.skill_id == skill.id), None)
        if not cell:
            continue
        if cell.status == MATCHED:
            stated.append(cand)
        elif cell.status == INFERRED:
            shown.append(cand)

    if not stated and not shown:
        return ChatResponse(WHO_HAS, f"No candidate shows evidence of {skill.label}.",
                            data={"skill": skill.label})

    parts = []
    if stated:
        parts.append(
            f"{len(stated)} {explainer.plural(len(stated), 'candidate')} "
            f"{explainer.plural(len(stated), 'names', 'name')} {skill.label} outright: "
            + explainer.oxford([f"{c.name} (#{c.rank})" for c in stated], limit=8) + "."
        )
    if shown:
        parts.append(
            f"{explainer.oxford([f'{c.name} (#{c.rank})' for c in shown], limit=6)} "
            f"{explainer.plural(len(shown), 'demonstrates', 'demonstrate')} it without "
            f"using the word — found by the semantic channel only."
        )
    return ChatResponse(WHO_HAS, " ".join(parts),
                        refs=[c.doc_id for c in stated + shown],
                        data={"skill": skill.label,
                              "stated": [c.name for c in stated],
                              "inferred": [c.name for c in shown]})


def _do_top(query: str, cands: list[Candidate]) -> ChatResponse:
    m = re.search(r"\btop\s*(\d+)", query, re.I)
    n = int(m.group(1)) if m else 3
    n = max(1, min(n, len(cands)))
    picked = cands[:n]

    lines = [f"Top {n} of {len(cands)}:"]
    for c in picked:
        note = ""
        if c.flag == HIDDEN_GEM:
            note = " — hidden gem, skills shown but not named"
        elif c.flag == SURFACE_MATCH:
            note = " — surface match, skills named but thinly evidenced"
        lines.append(
            f"{c.rank}. {c.name} — {c.score:.1f} "
            f"(keyword {c.k_score:.2f}, semantic {c.m_score:.2f}, "
            f"{c.primitives.req_coverage:.0%} of required skills){note}"
        )
    return ChatResponse(TOP_N, "\n".join(lines), refs=[c.doc_id for c in picked])


def _do_fallback(named: list[Candidate], cands: list[Candidate]) -> ChatResponse:
    # A named candidate with no clear intent almost always means "tell me about them".
    if named:
        return _do_why(named, cands)

    a, b = cands[0].name, cands[1].name if len(cands) > 1 else cands[0].name
    return ChatResponse(
        FALLBACK,
        "I can answer questions about this shortlist directly from the scoring data. Try:\n"
        f"  • Why is {a} ranked above {b}?\n"
        f"  • Why did {a} rank first?\n"
        f"  • What is {b} missing?\n"
        "  • Who knows Docker?\n"
        "  • Show me the top 5",
    )
