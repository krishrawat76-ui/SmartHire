# Engineering Trade-offs & Problem Resolution Log

*What actually went wrong while building this, and why it's built the way it is
now instead of the way it was first designed. Read this before you "fix"
something that looks odd — there's usually a measured reason.*

---

## Problems named in the original brief, and how we solved them

### Vocabulary mismatch
*"Someone who has built REST APIs with Express and MongoDB is clearly relevant
to a Node.js role, even if the JD never uses the word Node.js."*

**Solved by:** per-skill soft-OR fusion, `coverage = max(lexical, g(semantic))`,
in `fusion.prepare()`. Literal presence and semantic inference are treated as
*alternative* proofs of the same fact, never summed — a candidate who states a
skill and one who only demonstrates it both simply *have* coverage for it. The
semantic half runs per-skill (not just per-document), matching a short
descriptive phrase for the skill against every individual resume chunk, so
"Node.js" can be found in a sentence that never contains those characters.

### Score compression / outlier sensitivity
Raw model outputs (BM25 scores, cosine similarities) don't naturally spread
across a 0–100 range in a way that reads as a meaningful ranking, and one
badly-parsed resume can wreck a naive min-max scale for everyone else.

**Solved by:** percentile-anchored normalisation, `P5`/`P95` instead of true
min/max, in `fusion.robust_norm()`. A near-empty resume (our synthetic corpus
plants exactly one, 36 characters) becomes an outlier below the 5th
percentile and gets clipped rather than dragging the floor down for the
other 17 candidates. Measured result on the sample corpus: an 83.3-point
spread (0.0 to 83.3), comfortably above the ≥35 threshold we treat as the
acceptance bar for "meaningful spread" (`scripts/verify.py`, check #4).

### Zero-latency dynamic re-scoring
The brief's bonus criteria implicitly reward a recruiter being able to adjust
keyword/semantic weighting live without re-running the whole pipeline.

**Solved by:** splitting `fusion.py` into an expensive alpha-independent half
(`prepare()`, run once) and a cheap pure half (`score()`, run on every
re-rank), and mirroring `score()` line-for-line in
`frontend/src/lib/rescore.js`. The backend ships every sub-score `score()`
needs in the initial payload; the slider recomputes ~40 floating-point
operations per candidate in the browser and never calls the network.
`scripts/check_parity.py` runs the JS under Node and diffs it against the
real Python output at five alpha values — current measured maximum
discrepancy: **0.00e+00**, i.e. exact agreement, not just "close enough."

### No-LLM constraint for explanations and chat
Explanations and a recruiter Q&A layer are easy to build badly by piping
resume text into an LLM and asking it to narrate — which is explicitly
disallowed, and also unverifiable even if it were allowed.

**Solved by:** both `explainer.py` and `chat.py` are pure template realization
over the evidence matrix that `fusion.py` already computed for scoring. There
is no generative step anywhere in either module — every fact that appears in
an explanation or a chat answer is a value read directly out of a
`SkillCell`, which is the exact same object the score was computed from. This
makes hallucination structurally impossible rather than merely unlikely, and
it's independently checkable: `scripts/verify.py` check #10 greps the entire
backend for `openai`, `anthropic`, `api_key`, `gpt-`, `claude-`, and related
terms, and check #6 confirms every "matched" claim in a top-3 explanation
resolves to real, verifiable resume text at its cited character offsets.

### Parse failures and messy layouts
Resumes vary wildly in structure, and a single crash on one bad file
shouldn't take down a batch of 18.

**Solved by:** the PyMuPDF → pdfplumber cascade in `parser.py`, wrapped so
`extract()` **never raises** — a total failure produces a `quality=0.0` stub
that still appears in the UI, honestly marked, rather than vanishing. Section
detection falls back to whole-document chunking when no headers are found.
Typos are handled at the matching layer (RapidFuzz fuzzy matching + an alias
map), not by trying to "fix" the text. Verified against four deliberately
broken fixtures (`fixtures/edge/`): a corrupted PDF (truncated mid-stream), an
empty PDF, a PDF with no text layer at all (simulating a scan), and a file
that isn't a PDF. All four degrade to a low- or zero-quality stub with zero
unhandled exceptions.

---

## Problems we found by building it, that weren't in the brief

These weren't anticipated in the design phase — they only became visible once
the system was running against real (synthetic) data with a known expected
ranking baked in. Documented here because the fixes encode real judgment
calls a future contributor could easily undo by accident.

### Document-level similarity rewarded the exact candidate it should have caught
Early calibration (equal 0.5/0.5 weighting between document-level semantic
similarity and per-skill semantic coverage) produced a bug where the planted
"buzzword resume, no substance" candidate scored the **highest** document
similarity in the entire pool (`docsim_norm = 1.000`) — because a resume that
is essentially a bare list of skill names embeds very close to a JD that also
lists skill names. Per-skill coverage correctly rated her `0.306`, but the
document-level score was dragging her average up.

**Fix:** `M_WEIGHT_DOCSIM` was lowered from 0.5 to **0.25** in
`backend/config.py`, so the *requirement-aware* per-skill measure leads and
the whole-document measure only supports it. After the change, that candidate
correctly dropped and picked up the `SURFACE_MATCH` flag. The reasoning is
recorded as a comment directly above the constant, not just here, so anyone
tuning it later sees the measured cause before changing it back.

### The keyword channel had the identical failure in the other direction
`K_WEIGHT_BM25` was also lowered from an initial 0.5 to **0.40**, because raw
BM25 rewards term repetition and document length over actually meeting
requirements — on this corpus, a long, messy, run-on resume scored the pool's
*maximum* BM25 value while covering fewer required skills than three
candidates ranked above it on the final score. Same root cause as the
semantic case: a bag-of-words / whole-document signal is not the same thing
as "does this person meet the requirements," and when the two disagree, the
requirement-aware signal should win the tie.

### Rank-delta alone was both a false positive and a false negative machine
The first version of the Hidden Gem / Surface Match flag used only
`|rank_lexical − rank_semantic|` at a threshold of 4. On an 18-candidate pool
this was too noisy: it flagged a **typo-riddled** resume as a Hidden Gem
(rank churn from typos being fuzzy-matched slightly differently between
channels, not from genuine vocabulary mismatch) while **missing the actual
planted Hidden Gem entirely**, because she was solidly mid-ranked in *both*
channels — a real hidden gem can be #8 lexically and #6 semantically, which
is nowhere near a 4-place swing, and rank-delta alone structurally cannot see
that case.

**Fix:** flags are now driven primarily by the evidence matrix itself —
`inferred_req_ratio` (share of *required* skills that are INFERRED rather
than MATCHED) for Hidden Gem, and `lex_cov − sem_cov` gap for Surface Match —
with rank-delta kept only as a secondary, wider-threshold corroborating
trigger (`RRF_FLAG_DELTA` raised from 4 to 6). A further refinement excludes
low-coverage candidates from the Hidden Gem flag entirely
(`HIDDEN_GEM_MIN_COVERAGE = 0.60`): being hard to find lexically only matters
if the person actually meets the bar semantically — flagging someone who
doesn't meet the requirements as a "gem" would be actively misleading to a
recruiter, not merely imprecise.

### Skill-mining manufactured phantom requirements from fragments of real ones
The n-gram miner in `skills.py`, meant to catch technical terms the curated
gazetteer missed, was initially extracting **"Version"** and **"Unit"** as
standalone skills from JD lines that actually said "Version control with
Git" and "Unit testing with Jest" — both already correctly gazetteer-matched
as `git` and `testing`. The fragments inflated the total skill count and
diluted every candidate's coverage denominator with noise nobody was
actually being evaluated against.

**Fix:** `_known_words()` in `skills.py` pre-computes every individual word
appearing in any gazetteer surface form, and the miner rejects a single-word
candidate term if that word is already part of a known phrase.

### Small, purely cosmetic bugs worth recording so they aren't reintroduced
- `explainer.oxford()` initially produced double conjunctions on truncated
  lists ("X, Y and 8 more" is fine; the bug produced "X and Y and 8 more").
  Fixed by joining truncated lists with plain commas only.
- Explanation headlines initially surfaced the first three REQUIRED skills
  **alphabetically** ("CSS, Full Stack, Git" — true of nearly every strong
  candidate, and therefore useless as a distinguishing headline). Fixed to
  rank by semantic salience (`sem_raw`, descending) instead, so the headline
  now leads with what actually differentiates that candidate.

---

## Key architectural trade-offs

### CPU (Apple Silicon), not GPU, and not a distributed model router
The team had access to three RTX 4060 laptops over LAN in addition to the
primary MacBook. **Decision: pure local execution, CPU by default, MPS
available via `INTERLOOM_DEVICE=mps` but never required.** At a batch size of
15–18 resumes (a few hundred embedding chunks total), a GPU has nothing
meaningful to batch — measured cold-start time for the *entire* pipeline
(parse + embed + score for 18 resumes) is under 12 seconds, and the model
itself loads in under two. A network hop to a GPU router would add real
latency and a real failure mode (a dropped LAN connection mid-demo) to buy
back time that isn't actually being spent on inference. This is a case where
more powerful hardware is simply the wrong tool for the batch size.

### Plain NumPy matrices, not a vector database
Eighteen candidates at 384 dimensions is a trivially small matrix — a single
matrix multiply computes every similarity the app needs, in microseconds,
with zero setup. A vector database (FAISS, Chroma, pgvector) exists to solve
approximate nearest-neighbor search at a scale where brute-force comparison
becomes too slow — a scale this problem is nowhere near. Adding one would be
pure integration risk (a new service, a new failure mode, more install time
in a 3.5-hour build window) for zero benefit.

### A static, curated gazetteer, not live LLM-based skill extraction
Skill and requirement extraction from the JD uses a fixed ~95-entry curated
gazetteer plus a conservative n-gram miner, not a call to an LLM asking "what
skills does this job need." Beyond the hard rule that scoring cannot involve
an LLM, this keeps skill extraction **deterministic and inspectable** — the
same JD always produces the same skill set, every extraction can be traced to
either a specific gazetteer entry or a specific mined term, and there is no
external dependency or latency on the critical path. The explicit trade-off,
covered honestly in `docs/LIMITATIONS_AND_FUTURE_WORK.md`, is coverage: a
hyper-niche or very new technology the gazetteer doesn't know about will not
be recognised as a distinct skill.
