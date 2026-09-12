# Problem Statement Alignment & Feature Matrix

*Direct mapping from the InternLoom hackathon brief to what's actually built,
plus everything added beyond the minimum. Use this to write the pitch script.*

---

## Mapping to the core requirements

| Brief requirement | Status | Implementation |
|---|---|---|
| Evaluate every resume against the JD using **both** semantic and keyword search, genuinely factoring both into the result | ✅ Done | Two fully independent channels computed in `backend/core/engine.py` before anything is combined: Channel K (BM25-Okapi + exact/alias/fuzzy per-skill matching) and Channel M (MiniLM chunk embeddings, top-k pooled document similarity + per-skill max-chunk cosine). Fused per-skill via a soft-OR in `fusion.py`. Verified structurally: setting α=1.0 (pure keyword) vs α=0.0 (pure semantic) produces genuinely different rankings — 15 of 18 candidates change position, with swings up to 6 places (`scripts/verify.py`, check #3). |
| Produce a ranked list of **all** candidates, best fit to worst, with a final score each | ✅ Done | `fusion.score()` returns every candidate, sorted, each with a 0–100 `score`. Pool-relative percentile normalisation guarantees real spread rather than clustering (measured: 83.3-point spread on the sample corpus, comfortably clearing our internal ≥35 bar). |
| For the top 3, generate a short explanation: which skills matched, which required skills appear missing | ✅ Done, and exceeded | `explainer.explain()` produces a headline and bullet list read directly from the evidence matrix — matched skills, skills demonstrated without being named, adjacent-only skills, and missing required skills, each traceable to a literal resume citation. The brief asks for the top 3; the shipped system generates this explanation for **every** candidate in the pool, not only the top 3, at negligible extra cost since it's all template realization over data already computed. |
| **Hard constraint:** no LLM scoring; matching and keyword/vector work must be genuinely computed in-house | ✅ Verified, not just claimed | Zero LLM calls, zero API keys, zero network dependency anywhere in the scoring, explanation, or chat path. `scripts/verify.py` check #10 programmatically greps the entire `backend/` tree for `openai`, `anthropic`, `api_key`, `gpt-`, `claude-`, `cohere`, `gemini`, and related terms and asserts zero matches, on every run — not a one-time manual check. |
| Working end-to-end demo | ✅ Done | One command starts the backend, one starts the frontend; a single click ("Analyse the sample corpus") or a drag-and-drop of a real JD + resume batch produces the full ranked, explained, chat-ready, bias-checked result in one browser tab. Cold pipeline measured at ~11–12 seconds for 18 resumes. |

---

## Mapping to the bonus criteria

| Bonus item | Status | Implementation |
|---|---|---|
| Flag potential bias or overly narrow phrasing in the JD | ✅ Done, with a differentiator | Two layers in `bias_detector.py`. **Layer 1**, a curated lexicon scan (`backend/data/bias_lexicon.json`) across gender-coded, age-coded, exclusionary, ableist, elitist, and narrow-phrasing categories, each with a plain-English reason and a suggested rewrite. **Layer 2**, a counterfactual impact simulation — for every hard requirement, re-rank the pool *as if it didn't exist* (`fusion.without_skills()` + `fusion.score()`, reusing already-computed evidence, no re-parsing) and report the measured cost. On the sample JD: the "5+ years experience" clause on an explicitly-labelled internship is flagged **critical** because **18 of 18** candidates in the pool fall short of it — a finding grounded in the real applicant pool, not a word blacklist. |
| A recruiter chat/UI layer answering "why is X ranked above Y?" | ✅ Done, fully deterministic | `chat.py`: normalise → classify intent (regex-based, 6 intents: COMPARE, WHY, MISSING, WHO_HAS, TOP_N, FALLBACK) → resolve named candidates/skills (RapidFuzz fuzzy name matching, so a misspelled candidate name still resolves) → realise a template from the evidence matrix. No language model, no network call, sub-millisecond response. Verified: all 6 intents route and answer correctly (`scripts/verify.py`, check #9). |
| Handle messy or inconsistent resume formatting gracefully | ✅ Done | `parser.py`'s PyMuPDF → pdfplumber cascade never raises; fuzzy section-header detection (RapidFuzz ≥ 85) collapses "Work History"/"Employment"/"Professional Background" to one canonical section, or falls back to whole-document chunking if no headers are found at all; typos are absorbed at the matching layer via an alias map and fuzzy matching rather than by trying to correct the text. Verified against 4 deliberately broken fixtures (corrupted, empty, no text layer, not-a-PDF) plus one deliberately messy, header-free, run-on-prose resume in the synthetic corpus — all degrade gracefully with zero unhandled exceptions. |

---

## Custom innovations beyond the problem statement

### 🎛️ Zero-latency weight rail with FLIP re-ordering
The α slider (Keyword ↔ Semantic) recomputes the entire ranking **client-side**
— the backend ships every alpha-independent sub-score in the initial payload,
and `frontend/src/lib/rescore.js` mirrors `backend/core/fusion.py::score()`
exactly, so dragging the slider triggers zero network requests. Framer
Motion's `layoutId`-based FLIP animation makes the reordering a smooth,
watchable transition rather than a jump-cut — the single strongest moment in
a live demo, because the recruiter *sees* both channels are genuinely
load-bearing rather than being told so.

### 💎 Hidden Gem / Surface Match detection via cross-channel disagreement
```
Δ = rank_lexical − rank_semantic
```
computed as a Reciprocal Rank Fusion sidecar (`RRF = 1/(60+rank_lex) +
1/(60+rank_sem)`) that deliberately does **not** drive the displayed score
(RRF discards magnitude, which would break the per-candidate score the brief
requires and make the slider feel discontinuous). Instead, it's one input
among several — alongside the more reliable, evidence-matrix-derived signals
`inferred_req_ratio` and `lex_cov − sem_cov` — to a flag that surfaces exactly
the scenario the brief's own example describes: a candidate whose required
skills are demonstrably present in their actual sentences but never stated in
the JD's vocabulary. This is presented as a first-class, colour-coded UI
badge, not buried in a tooltip.

### 📊 Skill-gap density scatter
Match score plotted against a weighted density of *missing required skills*,
with two guide lines splitting the plot into four informally-labelled regions
(strong match/few gaps, strong match/real gaps, narrow specialist,
weak-match). Point size encodes how much resume text actually supported the
score (evidence volume), and color reuses the same Hidden Gem / Surface Match
/ Consensus vocabulary as the rest of the app.

### 🔍 Evidence-anchored highlighting via character offsets
Every skill cell in the evidence matrix carries the exact `[start, end)`
character span of the resume sentence that produced it, computed once during
chunking and threaded unchanged through scoring, explanation, and the API
response. Clicking a skill chip in the UI scrolls the candidate's resume text
to that literal span and highlights it — an explanation claim isn't just
plausible-sounding, it's a pointer into real, inspectable source text.

### 🧪 Counterfactual JD impact simulation
Rather than only flagging *phrasing* as potentially biased, the bias detector
measures the **actual cost** of every hard requirement against the real
applicant pool by re-ranking with it removed. This reuses the exact same
scoring engine the shortlist itself is built from (via `without_skills()`),
so the bias report and the ranking are provably using the same underlying
model of what each candidate has — not two disconnected subsystems that could
quietly disagree.

### 🛠️ Numerical parity testing between the JS and Python engines
`scripts/check_parity.py` runs `rescore.js` under Node.js and diffs its
output against the real `fusion.score()` in Python at five α values
(0, 0.25, 0.5, 0.75, 1.0), asserting agreement to **1e-9**. This exists
because nothing else would catch the two engines silently drifting apart —
the slider would keep working, it would just start being quietly wrong.
Currently measured: **exact agreement (0.00e+00 max delta)** at every tested
value. This is the kind of test a team usually skips under time pressure; it
was added specifically because a live demo is the worst possible place to
discover the UI and the backend disagree.

---

## Explicit LLM-constraint compliance

The brief states plainly: *"Simply pasting a resume and JD into an LLM API and
asking it to give this resume a score out of 100 does not meet the
requirement... Your own system needs to genuinely perform the semantic and
keyword matching as part of its logic."*

**How this system complies, concretely:**

- **Keyword matching** is BM25-Okapi (Robertson & Zaragho, a fully classical,
  deterministic information-retrieval algorithm predating LLMs by decades)
  plus exact/alias/fuzzy string matching. No model of any kind is involved.
- **Semantic matching** uses `sentence-transformers/all-MiniLM-L6-v2`, a small
  (~90MB), fixed, locally-hosted **embedding** model — it converts text into
  a 384-number vector and nothing more. It cannot generate text, cannot be
  prompted, and produces no output other than that fixed-length vector. This
  is meaningfully different from a generative LLM: cosine similarity between
  two vectors it produces is exactly as deterministic and inspectable as a
  BM25 score.
- **Fusion, scoring, explanation, chat, and bias detection** are all
  arithmetic and template realization over data those two deterministic
  systems already produced. At no point does any component ask a model "is
  this a good candidate" or "explain why" in natural language and trust the
  answer — every fact in every explanation and every chat response is read
  directly from a value that already existed before the sentence describing
  it was assembled.
- **This is independently verifiable, not just asserted.** Run:
  ```
  grep -riE "openai|anthropic|api[_-]?key|gpt-|claude-" backend/
  ```
  and it returns nothing. `scripts/verify.py` runs this exact check on every
  invocation (check #10) so the guarantee can't silently regress as the
  codebase changes.
