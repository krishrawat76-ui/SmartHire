"""
The API contract.

Written first and frozen, because four people build against it in parallel and
the frontend's client-side rescore depends on every sub-score being present in
the payload. If a field here changes, frontend/src/lib/rescore.js changes with it.

Two rules that keep the payload honest:

  * Every number the UI displays as a judgement ships with the reason next to it.
    `lex` and `lex_eff` travel together; a verdict travels with the evidence that
    produced it. The frontend is never asked to explain something it was only
    handed the conclusion of.

  * The expensive derived views — upskill roadmaps, which re-rank the pool once
    per gap — are their own endpoints rather than inline, so /api/analyze stays
    fast enough to feel instant.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Evidence
# ─────────────────────────────────────────────────────────────────────────────

class SkillCellOut(BaseModel):
    skill_id: str
    label: str
    tier: str                       # REQUIRED | PREFERRED
    weight: float
    cluster: str
    lex: float                      # 0.0 | 0.8 fuzzy | 1.0 exact/alias — what the text says
    lex_kind: str                   # exact | alias | fuzzy | none
    sem_raw: float                  # raw cosine
    sem_cal: float                  # g(sem_raw)
    coverage: float                 # max(lex_eff, sem_cal)
    status: str                     # MATCHED | INFERRED | WEAK | MISSING
    evidence: str = ""              # the resume sentence behind this cell
    start: int = -1                 # char span into CandidateOut.resume_text
    end: int = -1

    # What the claim is worth, and why it differs from what it says.
    lex_eff: float = 0.0
    section: str = "UNKNOWN"
    context_weight: float = 1.0
    context_reason: str = ""
    integrity_mult: float = 1.0
    external_mult: float = 1.0
    external_verdict: str = ""
    external_evidence: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    path: list[str] = Field(default_factory=list)   # ontological path, for the explorer


class PrimitivesOut(BaseModel):
    """Alpha-independent sub-scores. The client recomputes ranking from these alone."""
    bm25_raw: float
    bm25_norm: float
    docsim_raw: float
    docsim_norm: float
    lex_cov: float
    lex_cov_raw: float = 0.0
    sem_cov: float
    req_coverage: float
    gap_density: float
    inferred_req_ratio: float
    quality: float
    n_chunks: int
    doc_multiplier: float = 1.0
    context_discount: float = 1.0
    external_proven: int = 0
    external_contradicted: int = 0
    warnings: list[str] = Field(default_factory=list)
    cells: list[SkillCellOut] = Field(default_factory=list)


class ExplanationOut(BaseModel):
    headline: str
    bullets: list[str] = Field(default_factory=list)
    matched: list[dict] = Field(default_factory=list)
    inferred: list[dict] = Field(default_factory=list)
    weak: list[dict] = Field(default_factory=list)
    missing: list[dict] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Integrity, verification, external evidence
# ─────────────────────────────────────────────────────────────────────────────

class IntegrityOut(BaseModel):
    headline: str = ""
    detail: list[str] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    supported: list[str] = Field(default_factory=list)
    named_total: int = 0
    orphan_ratio: float = 0.0
    stuffing_flag: bool = False
    hidden_flag: bool = False
    hidden_samples: list[str] = Field(default_factory=list)
    doc_multiplier: float = 1.0


class ClaimVerdictOut(BaseModel):
    skill_id: str
    label: str
    tier: str
    verdict: str                    # CORROBORATED | CONTRADICTED | UNVERIFIABLE | UNVERIFIED
    reason: str
    evidence: list[str] = Field(default_factory=list)


class VerificationOut(BaseModel):
    headline: str = ""
    detail: list[str] = Field(default_factory=list)
    corroborated: int = 0
    contradicted: int = 0
    unverifiable: int = 0
    unverified: int = 0
    trust: float = 0.5
    checked: bool = False
    readme_only: list[str] = Field(default_factory=list)
    verdicts: list[ClaimVerdictOut] = Field(default_factory=list)


class RepoOut(BaseModel):
    name: str
    description: str = ""
    url: str = ""
    stars: int = 0
    pushed_at: str = ""
    languages: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    code_skills: list[str] = Field(default_factory=list)
    readme_only: list[str] = Field(default_factory=list)


class ProfileOut(BaseModel):
    source: str                     # github | linkedin
    handle: str = ""
    url: str = ""
    status: str = "disabled"        # ok | empty | not_found | unavailable | disabled
    message: str = ""
    skill_count: int = 0
    skills: list[str] = Field(default_factory=list)
    repos: list[RepoOut] = Field(default_factory=list)
    weight: float = 0.0             # how much this source is allowed to count


class ExternalOut(BaseModel):
    github: ProfileOut
    linkedin: ProfileOut


class RedactionOut(BaseModel):
    """What blind screening removed before the engine saw the document."""
    total: int = 0
    summary: str = ""
    counts: dict[str, int] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Derived recruiter views
# ─────────────────────────────────────────────────────────────────────────────

class GapOut(BaseModel):
    skill_id: str
    label: str
    tier: str
    status: str
    weeks: float
    human: str
    springboard: str
    springboard_held: str
    kind: str
    note: str
    path: list[str] = Field(default_factory=list)
    pinned: bool = False


class RampUpOut(BaseModel):
    doc_id: str
    name: str
    rank: int
    gaps: list[GapOut] = Field(default_factory=list)
    total_weeks: float = 0.0
    total_human: str = ""
    headline: str = ""
    verdict: str = ""


class QuestionOut(BaseModel):
    kind: str                       # GAP | CLAIM | INFERRED | PROJECT
    skill_id: str = ""
    skill_label: str = ""
    question: str
    why: str
    evidence: str = ""
    bridge: str = ""
    path: list[str] = Field(default_factory=list)


class InterviewOut(BaseModel):
    doc_id: str
    name: str
    rank: int
    headline: str = ""
    questions: list[QuestionOut] = Field(default_factory=list)


class RoadmapStepOut(BaseModel):
    skill_id: str
    label: str
    tier: str
    status: str
    places_gained: int
    score_gain: float
    weeks: float
    human_time: str
    project: str
    first_step: str
    springboard: str = ""
    path: list[str] = Field(default_factory=list)


class RoadmapOut(BaseModel):
    doc_id: str
    name: str
    rank: int
    pool_size: int
    score: float
    points_from_shortlist: float
    steps: list[RoadmapStepOut] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    total_weeks: float = 0.0
    total_human: str = ""
    combined_places: int = 0
    combined_score_gain: float = 0.0
    headline: str = ""
    email: str = ""


class FeedbackResponse(BaseModel):
    roadmaps: list[RoadmapOut] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomy explorer
# ─────────────────────────────────────────────────────────────────────────────

class TaxonomyNodeOut(BaseModel):
    id: str
    label: str
    kind: str                       # skill | concept


class TaxonomyPathOut(BaseModel):
    skill_id: str
    label: str
    path: list[TaxonomyNodeOut] = Field(default_factory=list)
    neighbours: list[str] = Field(default_factory=list)
    difficulty: int = 3
    known: bool = True


class RelationOut(BaseModel):
    """Why the engine treated two non-identical skills as related."""
    a: str
    b: str
    kind: str
    common: str = ""
    common_label: str = ""
    steps: int = 0
    via: list[str] = Field(default_factory=list)
    weeks: float = 0.0
    human: str = ""
    note: str = ""


class TaxonomyResponse(BaseModel):
    nodes: list[TaxonomyNodeOut] = Field(default_factory=list)
    paths: list[TaxonomyPathOut] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate
# ─────────────────────────────────────────────────────────────────────────────

class CandidateOut(BaseModel):
    doc_id: str
    name: str                       # pseudonym when blind mode is on
    real_name: str = ""             # empty while blind mode is on
    filename: str
    score: float
    rank: int
    k_score: float
    m_score: float
    gate: float
    rank_lexical: int
    rank_semantic: int
    rank_delta: int
    rank_rrf: int = 0
    rrf: float
    flag: str                       # HIDDEN_GEM | SURFACE_MATCH | CONSENSUS
    primitives: PrimitivesOut
    explanation: ExplanationOut | None = None
    integrity: IntegrityOut | None = None
    verification: VerificationOut | None = None
    external: ExternalOut | None = None
    redaction: RedactionOut | None = None
    ramp_up: RampUpOut | None = None        # shortlist only
    interview: InterviewOut | None = None   # shortlist only
    resume_text: str = ""           # redacted; evidence spans index this


# ─────────────────────────────────────────────────────────────────────────────
# Job description
# ─────────────────────────────────────────────────────────────────────────────

class SkillOut(BaseModel):
    id: str
    label: str
    tier: str
    weight: float
    cluster: str
    source: str                     # gazetteer | mined
    evidence: str = ""
    path: list[str] = Field(default_factory=list)


class BiasFindingOut(BaseModel):
    category: str
    label: str
    severity: str                   # critical | high | medium | low
    matched_text: str
    start: int
    end: int
    line: str
    suggestion: str
    why: str
    impact: str = ""
    impact_count: int = 0


class BiasOut(BaseModel):
    score: int                      # 0-100 inclusivity
    summary: str
    is_junior_role: bool
    findings: list[BiasFindingOut] = Field(default_factory=list)


class JobOut(BaseModel):
    title: str
    text: str
    skills: list[SkillOut] = Field(default_factory=list)
    clusters: list[str] = Field(default_factory=list)
    bias: BiasOut | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Envelope
# ─────────────────────────────────────────────────────────────────────────────

class PoolIntegrityOut(BaseModel):
    """Pool-level headline numbers for the integrity and evidence panels."""
    stuffing_flagged: int = 0
    hidden_text_flagged: int = 0
    with_contradictions: int = 0
    fully_corroborated: int = 0
    checked: int = 0
    readme_only_claims: int = 0
    profiles_found: int = 0


class MetaOut(BaseModel):
    alpha: float
    gate: bool
    blind: bool = False
    enrichment: bool = False
    pool_size: int
    semantic_backend: str
    device: str
    elapsed_ms: int
    tau_lo: float
    tau_hi: float
    k_weight_bm25: float
    m_weight_docsim: float
    gate_floor: float
    gate_span: float
    rrf_k: int = 60
    parse_warnings: int
    redactions: int = 0
    pool_integrity: PoolIntegrityOut | None = None


class AnalyzeResponse(BaseModel):
    meta: MetaOut
    job: JobOut
    candidates: list[CandidateOut] = Field(default_factory=list)


class ChatRequest(BaseModel):
    query: str


class ChatResponseOut(BaseModel):
    intent: str
    answer: str
    refs: list[str] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str
    semantic_backend: str
    device: str
    model: str
    has_session: bool
    pool_size: int
