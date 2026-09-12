# System Architecture & Layer-by-Layer Mechanics

*For the team member(s) working on the backend engine, or anyone extending it.*

---

## Data flow, end to end

```
PDF (JD)  ──┐
            ├──▶ parser.extract()         PyMuPDF fast path, pdfplumber fallback
PDF (×N)  ──┘         │                   + find_hidden(): invisible-text spans
                       ▼
              normalizer.repair_text()    NFKC, ligatures, hyphen-unwrap, bullets
                       │
              blind.redact()              PII stripped BEFORE anything is matched
                       │                  (resumes only — never the JD)
              parser.segment()            fuzzy section headers → {name: (start,end)}
                       │
              normalizer.chunk_text()     line/sentence chunks, EACH CARRYING A SPAN
                       │
              context.annotate()          per-chunk weight: section, shape, recency
                       │
         ┌─────────────┴──────────────┐
         ▼                             ▼
  skills.extract_skills(jd)     engine.Engine.build(docs, jd, skills)
  (only needs the JD's text)      │
         │                        ├── Channel K: BM25Okapi over the pool +
         │                        │   per-skill exact/alias/fuzzy matching
         │                        └── Channel M: SentenceTransformer chunk
         │                            embeddings, top-k pooled doc similarity +
         │                            per-skill max-chunk cosine
         │                        │
         └──────────┬─────────────┘
                     ▼
          fusion.prepare(sub_scores, skills)
          — pool-wide P5/P95 normalisation
          — per-skill soft-OR fusion → THE EVIDENCE MATRIX
          — this step runs ONCE per uploaded batch
                     │
                     ▼
          assess.build(docs, skills, primitives, enrichment)
          — integrity.py     unsupported claims, invisible text
          — enrich.py        GitHub / LinkedIn evidence (off by default)
          — verification.py  resume claims vs the candidate's own code
          — collapses all three into one Adjustment per candidate
                     │
                     ▼
          fusion.adjust(primitives, adjustments)
          — the ONLY thing that mutates the matrix
          — re-runs fusion.aggregate(), the single accumulation
                     │
                     ▼
          fusion.score(primitives, alpha, gate)
          — pure function of (primitives, alpha, gate)
          — this step runs on EVERY re-rank (cheap: ~40 flops/candidate)
                     │
   ┌──────────┬──────┴───────┬───────────┬────────────┬──────────────┐
   ▼          ▼              ▼           ▼            ▼              ▼
explainer  chat.py    bias_detector  rampup.py   interview.py   feedback.py
(reads    (reads      (re-runs       (taxonomy   (gaps, claims  (re-ranks the
 cells)    cells +     fusion.score   bridge      and repos      pool once per
           routes      with skills    cost per    → questions)   gap — its own
           intent)     removed)       gap)                       endpoint)
```

**Order is load-bearing.** `assess.build()` runs integrity *before*
verification, because verification asks the resume whether it stands behind a
claim before it is willing to call that claim contradicted — without that gate a
genuine internship achievement reads as a lie, since an employer's repository is
private and always will be.

**Parity survives all of it.** Only `fusion.score()` is mirrored in
`rescore.js`; every judgement pass above runs server-side and alpha-independent,
so `scripts/check_parity.py` still proves agreement to 1e-9 at every alpha. The
one exception is the whole-candidate `doc_multiplier`, which is applied inside
`score()` and therefore mirrored in JavaScript too.

**The one rule that makes the whole system explainable:** parsing and
embedding happen exactly once per uploaded batch
(`fusion.prepare`/`Engine.build`), and everything downstream — re-weighting,
explaining, chatting, the bias simulation — reads the same frozen evidence
matrix. Nothing downstream can introduce a fact that wasn't already in that
matrix, because nothing downstream re-parses or re-embeds anything.

---

## Layer 1 — Ingestion: `parser.py`, `normalizer.py`

**Contract: `parser.extract()` never raises.** A resume that can't be read
becomes a `quality=0.0` stub that still flows through scoring and still shows
up in the UI, flagged for manual review — a vanished candidate is a worse
failure than a visibly-broken one.

Cascade: PyMuPDF (`fitz`/`pymupdf`) is tried first because it's fast and
C-backed. If the yield is under 200 characters (`MIN_CHARS_FOR_FAST_PATH`) or
the ratio of alphanumeric-to-total characters is under 0.5
(`MIN_ALPHA_RATIO` — catches garbled font-encoding extraction that returns
plenty of bytes but no real text), it retries with `pdfplumber`, which is
slower but layout-aware. Whichever engine returns more usable text wins.

`normalizer.repair_text()` runs NFKC unicode normalisation, expands ligatures
(`ﬁ` → `fi`), rejoins hyphenated line-wraps (`develop-\nment` → `development`)
**before** newline collapsing (while the break is still visually present),
and strips bullet glyphs. This produces the **display text** — the exact
string every character-offset span in the system indexes into.

`parser.segment()` finds section boundaries by fuzzy-matching short lines
against a synonym table (`EXPERIENCE` / `Work History` / `Employment` /
`Professional Background` all collapse to one canonical `EXPERIENCE` label,
via RapidFuzz at a similarity ≥ 85). A resume with no detectable headers
returns `{}`, and chunking falls back to treating the whole document as one
unsectioned block — degraded, never broken.

> **Known gap vs. the original design brief:** the brief described
> "multi-column interleave detection via x-coordinate clustering" as a
> resilience feature. This was **not implemented** — the shipped parser uses
> pdfplumber's default text-extraction order for its fallback path with no
> manual column reconstruction. See `docs/LIMITATIONS_AND_FUTURE_WORK.md`.

`normalizer.chunk_text()` splits the display text into chunks (roughly one
bullet or sentence each — long lines are further split on sentence
boundaries), and **every chunk carries `(start, end)` as an offset into the
display text it came from.** This is the single design decision that makes
evidence-anchored highlighting possible in the UI: nothing is re-searched for
at render time, the span was computed once, here, and threaded through every
downstream structure unchanged.

---

## Layer 2 — JD understanding: `skills.py`

Two extraction passes over the JD text:

1. **Gazetteer lookup** — `backend/data/skill_gazetteer.json` holds ~95
   curated technical terms, each with a canonical id, a label, a cluster
   (Frontend/Backend/Database/DevOps/Fundamentals/Data/Mobile/Design — these
   clusters are the radar chart's axes), and a `phrase`: a short descriptive
   sentence that is what's actually fed to the embedding model for that
   skill (a bare word like "React" embeds poorly on its own; "React library
   for building component based user interfaces with hooks, props and state"
   gives the model real context to match against resume sentences).
2. **N-gram mining** — capitalised, technical-shaped terms (contains a digit,
   `+`, `#`, or a dot, or is a single unusual capitalised word) that the
   gazetteer missed. Filtered against a stopword list and against every
   individual word already appearing in a known gazetteer surface form, so
   mining doesn't manufacture phantom requirements out of fragments of skills
   already found (e.g. "Version control with Git" being gazetteer-matched as
   `git` must not also mine a bogus skill called "Version").

**Tiering** tracks which requirement block ("Required Skills" vs "Preferred
Skills" headers) each line belongs to, with inline hedge-word overrides
(`"strong plus"`, `"nice to have"` downgrade a line to PREFERRED even inside a
Required block; `"must have"`, `"required"` upgrade one). REQUIRED skills get
weight 1.0, PREFERRED get 0.5 — this weight is what makes a missing
"strongly preferred" skill hurt a candidate's score far less than a missing
required one.

---

## Layer 3 — Dual-channel retrieval: `engine.py`

Both channels are computed **completely independently**; neither can see the
other's output before fusion. This independence is what the 35%-weighted
rubric criterion is actually asking for, and it's what makes the
cross-channel disagreement flags (Hidden Gem / Surface Match) meaningful
rather than circular.

**Channel K (lexical).** `BM25Okapi(k1=1.5, b=0.75)` over the tokenised corpus
(canonicalised — aliases folded, stopwords removed). Per-skill lexical
matching runs separately: exact/alias match scores 1.0, a RapidFuzz
`token_set_ratio` fuzzy match at ≥ 88 scores 0.8 (typo tolerance —
`"Javscript"`, `"Node JS"`, `"Mongo DB"` all resolve), matched against
individual chunk tokens and bigrams rather than whole sentences (comparing a
short skill name against a long sentence via fuzzy-ratio produces
misleadingly high scores).

**Channel M (semantic).** `sentence-transformers/all-MiniLM-L6-v2`
(384-dimension, ~90MB, CPU by default — see the hardware decision below),
encoding every resume **chunk** (not whole documents — see
`docs/PROBLEM_SOLVING_AND_DECISIONS.md` for why this matters) plus every
skill's descriptive phrase plus the JD text as a whole. Document-level fit is
a **top-k pooled** cosine similarity (`DOCSIM_TOP_K = 3`: the mean of the
three highest-similarity chunks against the JD, not a mean over all chunks —
asks "what is the best evidence this person fits" rather than diluting a
strong section with irrelevant ones). Per-skill fit is the **max**-similarity
chunk for that specific skill phrase, which is also recorded as that cell's
evidence citation.

A `TfidfSvdBackend` (scikit-learn `TfidfVectorizer` + `TruncatedSVD`) exists
as an offline fallback if the embedding model can't be loaded (dead network,
missing cache), selected automatically or via `INTERLOOM_SEMANTIC=tfidf_svd`.
Degraded quality, but the pipeline never hard-fails for lack of a model.

---

## Layer 4 — Fusion & gating: `fusion.py`

This module is split deliberately into an expensive, alpha-independent half
and a cheap, pure half:

**`prepare(sub_scores, skills)`** — runs once per batch. Normalises both raw
channel outputs across the **pool** (not per-candidate, not against a fixed
scale) using `robust_norm()`:

```
norm(x) = clip( (x − P5) / (P95 − P5), 0, 1 )
```

5th/95th percentile rather than true min/max, specifically so one
pathologically bad resume (a near-empty scan yielding 40 characters) can't
define the floor and compress every real candidate's score toward the top of
the range — exactly the "all scores look the same" failure mode the rubric
penalises.

For every (skill, candidate) pair, fuses the two independent evidence signals
with a **soft-OR**, not a sum:

```
coverage = max( lexical_score, calibrate(semantic_cosine) )
```

Literal presence and semantic inference are *alternative* ways of proving the
same fact, not two things that should stack. `calibrate()` (`g(x)` in
`docs/MATH.md`) linearly rescales raw cosine similarity from the empirically
measured discriminative band (`TAU_LO=0.20` to `TAU_HI=0.55`) into [0,1]
before it's compared against anything — see `backend/config.py` for the
measured percentile data behind those two numbers. Each cell is then labelled
`MATCHED` / `INFERRED` / `WEAK` / `MISSING`, and **this label is the only
vocabulary the rest of the system speaks** — the explainer, the chat, and the
UI all read status labels, never raw numbers, when describing what a
candidate does or doesn't have.

**`score(primitives, alpha, gate)`** — runs on every re-rank, including every
slider drag. A **pure function**: same inputs, same output, always, with no
hidden state. This is what lets `frontend/src/lib/rescore.js` mirror it
exactly and re-rank client-side.

```
K = k_weight_bm25 · norm(BM25) + (1 − k_weight_bm25) · LexicalCoverage
M = m_weight_docsim · norm(DocSim) + (1 − m_weight_docsim) · SemanticCoverage
Gate = gate_floor + gate_span · RequiredCoverage     (if the gate is enabled)
S = 100 · [ α·K + (1−α)·M ] · Gate
```

Ranks are computed twice more, independently, purely for the disagreement
signal: `rank_lexical` (sort by K alone) and `rank_semantic` (sort by M
alone). `Δ = rank_lexical − rank_semantic` and a Reciprocal Rank Fusion score
(`RRF = 1/(60+rank_lex) + 1/(60+rank_sem)`) are computed but **do not drive
the displayed score** — RRF discards magnitude, which would break both the
"a final score for each candidate" requirement and the continuous slider. It
exists purely as a robustness cross-check and as an input (alongside the
evidence-matrix-based `inferred_req_ratio` and `lex_cov − sem_cov` gap, which
are the *primary* triggers) to the Hidden Gem / Surface Match flag logic in
`_flag()`.

`without_skills(primitives, drop_set)` re-derives the pool's primitives as if
a given set of skills had never been required, by re-aggregating the
**already-computed** cells with those skills excluded — no re-parsing, no
re-embedding, pure arithmetic. This one function is what makes the bias
detector's counterfactual simulation (`bias_detector.simulate_skill_impact`)
cost almost nothing: "what if this requirement didn't exist?" is answered by
re-running `fusion.score()` on a filtered view of data already in memory.

---

## Layer 4.5 — Judgement: `context.py`, `integrity.py`, `enrich.py`, `verification.py`, `assess.py`

These decide what a piece of evidence is *worth*, as multipliers on lexical
evidence. They are documented feature by feature, with the decisions and the
bugs that shaped them, in **[FEATURES.md](FEATURES.md)** — including why status
never moves when a claim is discounted, why a contradiction needs three gates
and a corroboration needs one, and why LinkedIn is capped at 0.35.

The ontology they share (`taxonomy.py`, `data/taxonomy.json`) is what makes a
semantic credit inspectable: 42 concepts, 89 skills, and a lowest common ancestor
for any pair.

---

## Layer 5 — Reasoning: `explainer.py`, `bias_detector.py`, `chat.py`

**`explainer.py`** builds a headline and bullet list by reading `cells_by_status()`
groupings off a candidate's `Primitives` — nothing here is generated freeform;
every sentence is templated around facts (skill names, similarity scores,
literal quoted evidence text) pulled directly from cells that already
determined the score. `explain()` is called for every candidate in
`app.py` (not only the top 3 the brief requires), and `compare(a, b)` powers
both the diff panel and the chat's `COMPARE` intent by diffing two candidates'
cell sets directly.

**`bias_detector.py`** — Layer 1 (`scan_lexicon`) runs curated regex patterns
from `backend/data/bias_lexicon.json` across five categories (gender-coded,
age-coded, exclusionary, ableist, elitist) plus a narrow-phrasing category.
`check_seniority_contradiction` specifically looks for a multi-year experience
threshold co-occurring with intern/junior/entry-level language in the same
JD. Layer 2 (`simulate_skill_impact`) is the counterfactual: for every
REQUIRED skill, call `fusion.without_skills()` to drop it, re-rank with
`fusion.score()`, and report how many candidates were excluded and who newly
enters the top N as a result. `_dedupe_spans()` collapses findings whose
character spans overlap (the same clause can trip two different regex
categories) so the same phrase never gets reported twice under different
labels.

**`chat.py`** — a four-stage deterministic pipeline, described fully in
`docs/PROBLEM_STATEMENT_ALIGNMENT.md`. No network calls, no model inference,
just regex intent classification, RapidFuzz entity resolution against
candidate names and the skill gazetteer, and template realization from the
same evidence cells `explainer.py` uses.

---

## Layer 6 — API: `app.py`, `models.py`

`models.py` is the frozen contract (Pydantic schemas) — every alpha-independent
sub-score that `rescore.js` needs is present in `CandidateOut.primitives`, by
design; this is what the entire client-side-rescoring feature depends on.

`app.py` holds one process-lifetime `Session` object: the parsed docs, the
extracted skills, the prepared `Primitives`, and the bias report, all built
exactly once per `/api/analyze` call and reused by every subsequent
`/api/chat`, `/api/bias`, or `/api/rank` request. The `SentenceTransformer`
model itself is loaded once **per process** (`_ENGINE = Engine()` at module
scope), not once per request — a cold load costs roughly 90 seconds, which
would be fatal to pay on every upload during a live demo.

`POST /api/analyze/sample` prefers a real corpus dropped into `data/jd/` and
`data/resumes/` over the bundled synthetic fixtures — no flag, no code
change, it just checks whether those directories are non-empty first.
