"""
FastAPI application.

Parsing and embedding happen exactly ONCE per upload and are cached in memory.
Every later request — re-weighting, chat, bias, diff, blind mode — reads the
cached evidence matrix. The recruiter's weight slider never reaches this server
at all; it recomputes in the browser from the sub-scores shipped in /api/analyze.

Blind mode is the one toggle that deserves a note. It is NOT a re-analysis:
identity is stripped at parse time and never reaches the engine, so the scores
are identical whether or not the recruiter can see who they belong to. Flipping
the toggle only changes which name is printed on a row — which is the whole point
of the claim, and the reason it is safe to flip mid-demo.
"""
from __future__ import annotations

import pathlib
import shutil
import tempfile
import time
from dataclasses import asdict

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from backend import config, models
from backend.core import (
    assess as assess_mod, bias_detector, blind, chat, engine as engine_mod,
    enrich as enrich_mod, explainer, feedback as feedback_mod, fusion,
    interview as interview_mod, parser, rampup as rampup_mod, skills, taxonomy,
)

app = FastAPI(title="InterLoom Shortlisting Engine", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_DIR = config.ROOT / "fixtures" / "synthetic"
REAL_JD_DIR = config.ROOT / "data" / "jd"
REAL_RESUME_DIR = config.ROOT / "data" / "resumes"


# ─────────────────────────────────────────────────────────────────────────────
# Session cache
# ─────────────────────────────────────────────────────────────────────────────

class Session:
    """One analysed pool. Built once, read many times."""

    def __init__(self) -> None:
        self.jd: parser.ParsedDoc | None = None
        self.docs: list[parser.ParsedDoc] = []
        self.skills: list[skills.Skill] = []
        self.primitives: list[fusion.Primitives] = []
        self.assessment: assess_mod.Assessment | None = None
        self.bias: bias_detector.BiasReport | None = None
        self.backend_name: str = ""
        self.elapsed_ms: int = 0
        self.enrichment_enabled: bool = False

    @property
    def ready(self) -> bool:
        return bool(self.primitives)

    def candidates(self, alpha: float, gate: bool) -> list[fusion.Candidate]:
        return fusion.score(self.primitives, alpha=alpha, gate=gate)

    def doc(self, doc_id: str) -> parser.ParsedDoc | None:
        return next((d for d in self.docs if d.doc_id == doc_id), None)

    def alias(self, doc_id: str) -> str:
        """Stable pseudonym for blind mode, from pool position rather than name."""
        for i, d in enumerate(self.docs, start=1):
            if d.doc_id == doc_id:
                return blind.alias_for(doc_id, i)
        return "Candidate"


SESSION = Session()

# The embedding model is loaded once per process, not once per request. A cold
# SentenceTransformer costs ~90s; paying that on every upload would be fatal in a demo.
_ENGINE = engine_mod.Engine()


# ─────────────────────────────────────────────────────────────────────────────
# Analysis
# ─────────────────────────────────────────────────────────────────────────────

def _analyse(jd_path: pathlib.Path, resume_paths: list[pathlib.Path],
             alpha: float, gate: bool, blind_mode: bool, enrichment: bool):
    t0 = time.perf_counter()

    # The JD keeps its identity: the bias detector hunts for gendered pronouns and
    # school filters, which are precisely the strings redaction removes.
    jd = parser.extract(jd_path, anonymise=False)
    if not jd.text.strip():
        raise HTTPException(422, "The job description could not be read. "
                                 "It may be a scanned image with no text layer.")

    skill_set = skills.extract_skills(jd.text)
    if not skill_set:
        raise HTTPException(422, "No skills could be extracted from that job description.")

    docs = parser.extract_many(resume_paths)
    subs = _ENGINE.build(docs, jd.text, skill_set)
    primitives = fusion.prepare(subs, skill_set)

    # Integrity, external evidence and claim verification all need the built
    # matrix before they can judge anything, so they run here and hand back
    # multipliers rather than being folded into prepare().
    assessment = assess_mod.build(docs, skill_set, primitives, enrichment_enabled=enrichment)
    fusion.adjust(primitives, assessment.adjustments)

    SESSION.jd = jd
    SESSION.docs = docs
    SESSION.skills = skill_set
    SESSION.primitives = primitives
    SESSION.assessment = assessment
    SESSION.backend_name = _ENGINE.backend_name
    SESSION.enrichment_enabled = enrichment
    SESSION.bias = bias_detector.detect(
        jd.text, primitives, skill_set,
        {d.name: d.text for d in docs}, alpha=alpha,
    )
    SESSION.elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return _payload(alpha, gate, blind_mode)


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation
# ─────────────────────────────────────────────────────────────────────────────

def _cell_out(c: fusion.SkillCell) -> models.SkillCellOut:
    return models.SkillCellOut(
        skill_id=c.skill_id, label=c.label, tier=c.tier, weight=c.weight,
        cluster=c.cluster, lex=c.lex, lex_kind=c.lex_kind,
        sem_raw=c.sem_raw, sem_cal=c.sem_cal, coverage=c.coverage, status=c.status,
        evidence=c.evidence, start=c.start, end=c.end,
        lex_eff=c.lex_eff, section=c.section,
        context_weight=c.context_weight, context_reason=c.context_reason,
        integrity_mult=c.integrity_mult, external_mult=c.external_mult,
        external_verdict=c.external_verdict, external_evidence=c.external_evidence,
        notes=c.notes,
        path=taxonomy.path_labels(c.skill_id, c.label),
    )


def _integrity_out(r) -> models.IntegrityOut | None:
    if r is None:
        return None
    return models.IntegrityOut(
        headline=r.headline, detail=r.detail, orphans=r.orphans,
        supported=r.supported, named_total=r.named_total,
        orphan_ratio=r.orphan_ratio, stuffing_flag=r.stuffing_flag,
        hidden_flag=r.hidden_flag, hidden_samples=r.hidden_samples,
        doc_multiplier=r.doc_multiplier,
    )


def _verification_out(r) -> models.VerificationOut | None:
    if r is None:
        return None
    return models.VerificationOut(
        headline=r.headline, detail=r.detail,
        corroborated=r.corroborated, contradicted=r.contradicted,
        unverifiable=r.unverifiable, unverified=r.unverified,
        trust=r.trust, checked=r.checked, readme_only=r.readme_only,
        verdicts=[models.ClaimVerdictOut(
            skill_id=v.skill_id, label=v.label, tier=v.tier,
            verdict=v.verdict, reason=v.reason, evidence=v.evidence,
        ) for v in r.verdicts],
    )


def _profile_out(p, blind_mode: bool) -> models.ProfileOut:
    # A handle is an identity. Blind mode keeps the evidence and drops the name
    # it belongs to, otherwise the profile link undoes the whole exercise.
    return models.ProfileOut(
        source=p.source,
        handle="" if blind_mode else p.handle,
        url="" if blind_mode else p.url,
        status=p.status, message=p.message,
        skill_count=len(p.skills), skills=sorted(p.skills),
        weight=p.weight(),
        repos=[models.RepoOut(
            name=r.name, description=r.description,
            url="" if blind_mode else r.url,
            stars=r.stars, pushed_at=r.pushed_at,
            languages=r.languages, manifests=r.manifests,
            code_skills=r.code_skills, readme_only=r.readme_only,
        ) for r in p.repos],
    )


def _external_out(e, blind_mode: bool) -> models.ExternalOut | None:
    if e is None:
        return None
    return models.ExternalOut(
        github=_profile_out(e.github, blind_mode),
        linkedin=_profile_out(e.linkedin, blind_mode),
    )


def _rampup_out(r) -> models.RampUpOut:
    return models.RampUpOut(
        doc_id=r.doc_id, name=r.name, rank=r.rank,
        total_weeks=r.total_weeks, total_human=r.total_human,
        headline=r.headline, verdict=r.verdict,
        gaps=[models.GapOut(
            skill_id=g.skill_id, label=g.label, tier=g.tier, status=g.status,
            weeks=g.weeks, human=g.human, springboard=g.springboard,
            springboard_held=g.springboard_held, kind=g.kind, note=g.note,
            path=g.path, pinned=g.pinned,
        ) for g in r.gaps],
    )


def _interview_out(g) -> models.InterviewOut:
    return models.InterviewOut(
        doc_id=g.doc_id, name=g.name, rank=g.rank, headline=g.headline,
        questions=[models.QuestionOut(
            kind=q.kind, skill_id=q.skill_id, skill_label=q.skill_label,
            question=q.question, why=q.why, evidence=q.evidence,
            bridge=q.bridge, path=q.path,
        ) for q in g.questions],
    )


def _roadmap_out(r) -> models.RoadmapOut:
    return models.RoadmapOut(
        doc_id=r.doc_id, name=r.name, rank=r.rank, pool_size=r.pool_size,
        score=r.score, points_from_shortlist=r.points_from_shortlist,
        strengths=r.strengths, total_weeks=r.total_weeks,
        total_human=r.total_human, combined_places=r.combined_places,
        combined_score_gain=r.combined_score_gain,
        headline=r.headline, email=r.email,
        steps=[models.RoadmapStepOut(
            skill_id=s.skill_id, label=s.label, tier=s.tier, status=s.status,
            places_gained=s.places_gained, score_gain=s.score_gain,
            weeks=s.weeks, human_time=s.human_time, project=s.project,
            first_step=s.first_step, springboard=s.springboard, path=s.path,
        ) for s in r.steps],
    )


def _pool_integrity(assessment) -> models.PoolIntegrityOut:
    if assessment is None:
        return models.PoolIntegrityOut()
    integ = assessment.integrity.values()
    ver = assessment.verification.values()
    checked = [v for v in ver if v.checked]
    return models.PoolIntegrityOut(
        stuffing_flagged=sum(1 for i in integ if i.stuffing_flag),
        hidden_text_flagged=sum(1 for i in integ if i.hidden_flag),
        with_contradictions=sum(1 for v in checked if v.contradicted),
        fully_corroborated=sum(1 for v in checked if v.corroborated and not v.contradicted),
        checked=len(checked),
        readme_only_claims=sum(len(v.readme_only) for v in ver),
        profiles_found=sum(1 for e in assessment.enrichment.values()
                           if e.github.ok or e.linkedin.ok),
    )


def _payload(alpha: float, gate: bool, blind_mode: bool) -> models.AnalyzeResponse:
    cands = SESSION.candidates(alpha, gate)
    explanations = {e.doc_id: e for e in explainer.explain_top(cands, n=len(cands))}
    assessment = SESSION.assessment

    ramps = {r.doc_id: r for r in rampup_mod.top(cands)}
    guides = {g.doc_id: g for g in interview_mod.top(
        cands, assessment.enrichment if assessment else None)}

    gaz_clusters = sorted({s.cluster for s in SESSION.skills})

    out_cands = []
    for c in cands:
        exp = explanations.get(c.doc_id)
        doc = SESSION.doc(c.doc_id)
        integ, ext, ver = (assessment.for_doc(c.doc_id)
                           if assessment else (None, None, None))

        display_name = SESSION.alias(c.doc_id) if blind_mode else c.name
        resume_text = doc.text if doc else ""
        if blind_mode and doc:
            # The engine text is already redacted; this additionally removes the
            # portfolio link, which is evidence to the scorer and an identity to
            # a reader.
            resume_text = blind.mask_display(doc.text, name=doc.name)

        out_cands.append(models.CandidateOut(
            doc_id=c.doc_id, name=display_name,
            real_name="" if blind_mode else c.name,
            filename="" if blind_mode else c.filename,
            score=c.score, rank=c.rank, k_score=c.k_score, m_score=c.m_score,
            gate=c.gate, rank_lexical=c.rank_lexical, rank_semantic=c.rank_semantic,
            rank_delta=c.rank_delta, rank_rrf=c.rank_rrf, rrf=c.rrf, flag=c.flag,
            primitives=models.PrimitivesOut(
                bm25_raw=c.primitives.bm25_raw, bm25_norm=c.primitives.bm25_norm,
                docsim_raw=c.primitives.docsim_raw, docsim_norm=c.primitives.docsim_norm,
                lex_cov=c.primitives.lex_cov, lex_cov_raw=c.primitives.lex_cov_raw,
                sem_cov=c.primitives.sem_cov, req_coverage=c.primitives.req_coverage,
                gap_density=c.primitives.gap_density,
                inferred_req_ratio=c.primitives.inferred_req_ratio,
                quality=c.primitives.quality, n_chunks=c.primitives.n_chunks,
                doc_multiplier=c.primitives.doc_multiplier,
                context_discount=c.primitives.context_discount,
                external_proven=c.primitives.external_proven,
                external_contradicted=c.primitives.external_contradicted,
                warnings=list(c.primitives.warnings),
                cells=[_cell_out(x) for x in c.primitives.cells],
            ),
            explanation=models.ExplanationOut(
                headline=exp.headline, bullets=exp.bullets,
                matched=exp.matched, inferred=exp.inferred,
                weak=exp.weak, missing=exp.missing,
            ) if exp else None,
            integrity=_integrity_out(integ),
            verification=_verification_out(ver),
            external=_external_out(ext, blind_mode),
            redaction=models.RedactionOut(
                total=doc.redaction.total, summary=doc.redaction.summary(),
                counts=doc.redaction.counts,
            ) if doc and doc.redaction else None,
            ramp_up=_rampup_out(ramps[c.doc_id]) if c.doc_id in ramps else None,
            interview=_interview_out(guides[c.doc_id]) if c.doc_id in guides else None,
            resume_text=resume_text,
        ))

    title = SESSION.jd.text.split("\n")[1].strip() if SESSION.jd and "\n" in SESSION.jd.text else "Role"

    return models.AnalyzeResponse(
        meta=models.MetaOut(
            alpha=alpha, gate=gate, blind=blind_mode,
            enrichment=SESSION.enrichment_enabled,
            pool_size=len(cands),
            semantic_backend=SESSION.backend_name, device=config.DEVICE,
            elapsed_ms=SESSION.elapsed_ms,
            tau_lo=config.TAU_LO, tau_hi=config.TAU_HI,
            k_weight_bm25=config.K_WEIGHT_BM25, m_weight_docsim=config.M_WEIGHT_DOCSIM,
            gate_floor=config.GATE_FLOOR, gate_span=config.GATE_SPAN,
            rrf_k=config.RRF_K,
            parse_warnings=sum(1 for d in SESSION.docs if d.warnings),
            redactions=sum(d.redaction.total for d in SESSION.docs if d.redaction),
            pool_integrity=_pool_integrity(assessment),
        ),
        job=models.JobOut(
            title=title,
            text=SESSION.jd.text if SESSION.jd else "",
            skills=[models.SkillOut(
                id=s.id, label=s.label, tier=s.tier, weight=s.weight,
                cluster=s.cluster, source=s.source, evidence=s.evidence,
                path=taxonomy.path_labels(s.id, s.label),
            ) for s in SESSION.skills],
            clusters=gaz_clusters,
            bias=models.BiasOut(**asdict(SESSION.bias)) if SESSION.bias else None,
        ),
        candidates=out_cands,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=models.HealthOut)
def health() -> models.HealthOut:
    return models.HealthOut(
        status="ok",
        semantic_backend=SESSION.backend_name or config.SEMANTIC_BACKEND,
        device=config.DEVICE,
        model=config.EMBED_MODEL,
        has_session=SESSION.ready,
        pool_size=len(SESSION.primitives),
    )


@app.post("/api/analyze", response_model=models.AnalyzeResponse)
async def analyze(
    jd: UploadFile = File(...),
    resumes: list[UploadFile] = File(...),
    alpha: float = Form(config.DEFAULT_ALPHA),
    gate: bool = Form(config.GATE_ENABLED_DEFAULT),
    blind_mode: bool = Form(config.BLIND_MODE_DEFAULT),
    enrichment: bool = Form(config.ENRICHMENT_ENABLED_DEFAULT),
) -> models.AnalyzeResponse:
    """Full pipeline: parse -> extract skills -> embed -> judge -> fuse -> explain."""
    if not resumes:
        raise HTTPException(400, "Upload at least one resume.")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="interloom_"))
    try:
        jd_path = tmp / (jd.filename or "jd.pdf")
        jd_path.write_bytes(await jd.read())

        paths = []
        for item in resumes:
            dest = tmp / (item.filename or f"resume_{len(paths)}.pdf")
            dest.write_bytes(await item.read())
            paths.append(dest)

        return _analyse(jd_path, paths, alpha, gate, blind_mode, enrichment)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/api/analyze/sample", response_model=models.AnalyzeResponse)
def analyze_sample(
    alpha: float = config.DEFAULT_ALPHA,
    gate: bool = config.GATE_ENABLED_DEFAULT,
    blind_mode: bool = config.BLIND_MODE_DEFAULT,
    enrichment: bool = config.ENRICHMENT_ENABLED_DEFAULT,
) -> models.AnalyzeResponse:
    """Analyse whichever corpus is on disk.

    Prefers the real corpus in data/ when present, otherwise falls back to the
    synthetic fixture set. Lets the UI open in a working state with one click.
    """
    real_jd = sorted(REAL_JD_DIR.glob("*.pdf"))
    real_resumes = sorted(REAL_RESUME_DIR.glob("*.pdf"))

    if real_jd and real_resumes:
        return _analyse(real_jd[0], real_resumes, alpha, gate, blind_mode, enrichment)

    jd_path = SAMPLE_DIR / "jd_technova.pdf"
    if not jd_path.exists():
        raise HTTPException(
            404,
            "No corpus found. Drop the real PDFs into data/jd and data/resumes, "
            "or run: python scripts/make_synthetic_corpus.py",
        )
    resumes = sorted(p for p in SAMPLE_DIR.glob("*.pdf") if not p.name.startswith("jd_"))
    return _analyse(jd_path, resumes, alpha, gate, blind_mode, enrichment)


@app.get("/api/rank", response_model=models.AnalyzeResponse)
def rank(alpha: float = config.DEFAULT_ALPHA,
         gate: bool = config.GATE_ENABLED_DEFAULT,
         blind_mode: bool = config.BLIND_MODE_DEFAULT):
    """Re-rank the cached pool at a different alpha, or re-render it blind.

    The slider does not need this — it recomputes locally — but the blind toggle
    does, because pseudonymising a pool is a rendering decision the server owns.
    Scores are untouched by `blind_mode`: identity never reached the engine.
    """
    if not SESSION.ready:
        raise HTTPException(409, "Nothing analysed yet. POST /api/analyze first.")
    return _payload(alpha, gate, blind_mode)


@app.post("/api/chat", response_model=models.ChatResponseOut)
def ask(req: models.ChatRequest,
        alpha: float = config.DEFAULT_ALPHA,
        gate: bool = config.GATE_ENABLED_DEFAULT) -> models.ChatResponseOut:
    """Deterministic recruiter QA. No LLM is consulted anywhere in this path."""
    if not SESSION.ready:
        raise HTTPException(409, "Nothing analysed yet. POST /api/analyze first.")
    if not req.query.strip():
        raise HTTPException(400, "Empty query.")

    resp = chat.answer(req.query, SESSION.candidates(alpha, gate), SESSION.skills)
    return models.ChatResponseOut(
        intent=resp.intent, answer=resp.answer, refs=resp.refs, data=resp.data
    )


@app.get("/api/bias", response_model=models.BiasOut)
def bias() -> models.BiasOut:
    if not SESSION.ready or SESSION.bias is None:
        raise HTTPException(409, "Nothing analysed yet. POST /api/analyze first.")
    return models.BiasOut(**asdict(SESSION.bias))


@app.get("/api/feedback", response_model=models.FeedbackResponse)
def candidate_feedback(
    alpha: float = config.DEFAULT_ALPHA,
    gate: bool = config.GATE_ENABLED_DEFAULT,
    shortlist: int = Query(config.RAMPUP_TOP_N, ge=1, le=50),
    limit: int = Query(10, ge=1, le=100),
    doc_id: str | None = None,
) -> models.FeedbackResponse:
    """Upskill roadmaps for candidates below the shortlist cut.

    Its own endpoint because it is the one expensive derived view: each gap is
    priced by re-ranking the whole pool with that gap closed, so cost grows with
    (candidates x gaps). Keeping it out of /api/analyze is what stops the board
    from waiting on advice nobody has asked to see yet.
    """
    if not SESSION.ready:
        raise HTTPException(409, "Nothing analysed yet. POST /api/analyze first.")

    cands = SESSION.candidates(alpha, gate)

    if doc_id:
        target = next((c for c in cands if c.doc_id == doc_id), None)
        if target is None:
            raise HTTPException(404, f"No candidate {doc_id} in the current pool.")
        cut = min(shortlist, len(cands))
        road = feedback_mod.for_candidate(
            target, SESSION.primitives, len(cands),
            cands[cut - 1].score, alpha, gate)
        return models.FeedbackResponse(roadmaps=[_roadmap_out(road)])

    roads = feedback_mod.for_rejected(
        cands, SESSION.primitives, shortlist_size=shortlist,
        alpha=alpha, gate=gate, limit=limit)
    return models.FeedbackResponse(roadmaps=[_roadmap_out(r) for r in roads])


@app.get("/api/taxonomy", response_model=models.TaxonomyPathOut)
def taxonomy_path(skill_id: str, label: str | None = None) -> models.TaxonomyPathOut:
    """The ontological path behind one skill, for the graph explorer."""
    chain = taxonomy.path(skill_id)
    nodes = [models.TaxonomyNodeOut(
        id=skill_id, label=label or skill_id, kind="skill")]
    nodes += [models.TaxonomyNodeOut(id=c, label=taxonomy.label(c), kind="concept")
              for c in chain[1:]]

    node = taxonomy.skills().get(skill_id)
    return models.TaxonomyPathOut(
        skill_id=skill_id, label=label or skill_id,
        path=nodes if chain else [],
        neighbours=taxonomy.neighbours(skill_id),
        difficulty=node["difficulty"] if node else 3,
        known=taxonomy.known(skill_id),
    )


@app.get("/api/taxonomy/relate", response_model=models.RelationOut)
def taxonomy_relate(a: str, b: str) -> models.RelationOut:
    """Why the engine treats two non-identical skills as related, and what the
    walk between them costs."""
    rel = taxonomy.relate(a, b)
    bridge = taxonomy.bridge(b, {a: 1.0}, b)
    return models.RelationOut(
        a=a, b=b, kind=rel.kind, common=rel.common or "",
        common_label=rel.common_label, steps=rel.steps,
        via=[taxonomy.label(v) for v in rel.via],
        weeks=bridge.weeks, human=bridge.human, note=bridge.note,
    )


@app.post("/api/enrich", response_model=models.ExternalOut)
def enrich_one(doc_id: str) -> models.ExternalOut:
    """Fetch external evidence for one candidate on demand.

    Separate from /api/analyze so a recruiter can pull a single profile without
    committing the whole pool to a network round trip — and so a rate limit costs
    one candidate rather than the run.
    """
    if not SESSION.ready:
        raise HTTPException(409, "Nothing analysed yet. POST /api/analyze first.")
    doc = SESSION.doc(doc_id)
    if doc is None:
        raise HTTPException(404, f"No candidate {doc_id} in the current pool.")

    result = enrich_mod.enrich_one(doc_id, doc.raw_text or doc.text, enabled=True)
    if SESSION.assessment is not None:
        SESSION.assessment.enrichment[doc_id] = result
    return _external_out(result, blind_mode=False)
