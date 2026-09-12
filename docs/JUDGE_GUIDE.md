# Judge's Walkthrough & Quick-Start Guide

*For first-time viewers and non-technical evaluators. Read this before you open the app.*

---

## Executive overview (60 seconds)

Given one job description and a batch of resumes, most systems either do
literal keyword matching (misses a candidate who says "Express and MongoDB"
instead of "Node.js") or hand the whole thing to an LLM and ask for a score out
of 100 (fast, but not verifiable, and explicitly disallowed by this brief).

We do neither. For **every skill the job description asks for**, our engine
independently checks two things against every resume:

1. **Does it say so?** — BM25 keyword search plus exact/alias/fuzzy matching.
2. **Does it show so?** — a sentence-embedding model checks whether the resume's
   actual sentences are *semantically* about that skill, even if the word never
   appears.

Those two independent answers are fused, per skill, into one evidence table.
That table is the only thing in the system — it produces the ranked score, the
top-candidate explanations, the chatbot's answers, and every chart on screen.
Nothing is generated after the fact; everything you see is a direct read of
that table.

**No language model scores anything, anywhere, at any point.** The matching is
BM25 (a 1990s-vintage, fully deterministic search algorithm) and a
sentence-transformer embedding model (a fixed, local, 90MB model — not a chat
model, and it never "writes" anything). You can verify this yourself: run
`python scripts/verify.py` and read check #10, which greps the entire backend
for `openai`, `anthropic`, `api_key`, and similar terms and asserts zero hits.

---

## Step-by-step UI tour

Open the app, click **"Analyse the sample corpus"** (or upload a JD + resumes —
name the JD file with "jd" or "job" so it's picked out of the batch), and
you'll land on the main screen: a control rail on the left, the ranked board in
the middle, and an insight column on the right.

### 1. The ranked list (center column)

Every candidate is a card: rank, name, a colored mini-bar under the name
showing the proportion of skills that are **Matched / Inferred / Weak /
Missing**, a required-skill coverage percentage, and the final score on the
right (with the keyword (K) and semantic (M) sub-scores printed underneath it
in small type).

The score is not clamped, but in practice it never exceeds 100 because both
channel scores are bounded to [0,1] and the gate multiplier never exceeds 1.0.
It is a **pool-relative** score — a 92 means "the strongest fit in *this*
batch," not "92% qualified in some absolute sense." That's a deliberate choice
(see `docs/MATH.md`), and it's why the top bar also shows the pool's score
**spread** — a healthy shortlist should show real separation between
candidates, not everyone clustered in the 60s.

### 2. The α weight slider (left rail, top panel)

Labelled **Keyword ↔ Semantic**. Drag it and watch the cards physically
reorder — this happens **instantly**, with no loading spinner, because it
never leaves your browser. The backend already sent every sub-score it needs;
the slider is re-running about 40 floating-point operations per candidate in
JavaScript. Check the browser's network tab if you don't believe it — nothing
fires.

- All the way right (α = 0): pure semantic ranking.
- All the way left (α = 1): pure keyword ranking.
- **Drag it to both extremes and watch the order actually change.** If it
  didn't, one of the two channels would be dead weight — that's exactly what
  we check for internally (`scripts/verify.py`, check #3).

Below the slider is a **Must-have gate** toggle. Off by default. Turning it on
multiplies every score by a penalty proportional to how many *required*
(not preferred) skills that candidate is missing evidence for — it re-ranks
the list rather than deleting anyone, so a strong near-miss doesn't vanish.

### 3. The candidate badges

Two flags can appear on a card, drawn from comparing what the keyword and
semantic channels each independently concluded:

- 🟦 **HIDDEN GEM** — a meaningful share of this candidate's *required* skills
  are demonstrably present in their resume's actual sentences, but the literal
  word never appears. This is the exact scenario the problem statement
  describes: someone who "built REST APIs with Express and MongoDB" being
  relevant to a Node.js role. On the sample corpus, **Priya Nair** is flagged
  this way — she never writes "Node.js," but the model finds it at similarity
  0.556 in the sentence *"Server-side JavaScript runtime handling async I/O for
  a bookmarking service."* Click her card to see it highlighted.
- 🟨 **SURFACE MATCH** — the inverse: the resume names the required skills, but
  the semantic channel finds little supporting substance behind them. On the
  sample corpus this flags a resume that is essentially a keyword list with a
  single one-line internship description.

A candidate with neither flag is a **consensus** case — both channels agree,
which is the common, unremarkable case for most of the pool.

### 4. The evidence drawer

Click any card. A panel slides in from the right with:

- The plain-English explanation (also read out by the chat, see below).
- A bar showing exactly how much of the score came from the keyword channel
  versus the semantic channel at the current slider position.
- Every skill, grouped by status, as a clickable chip.
- **Click a chip.** The resume text below scrolls to and highlights the exact
  sentence that produced that evidence, with a brief pulse animation. The
  highlight is not decorative — it is the literal character span the backend
  used to compute the number you're looking at. If a chip has no evidence
  (nothing found), it isn't clickable.
- A radar chart showing coverage across skill clusters (Frontend, Backend,
  Database, DevOps, etc.).
- The full parsed resume text, with a parse-quality score and any warnings
  (e.g. "no section headers detected — using whole-document chunking") shown
  honestly rather than hidden.

### 5. The bias engine and the skill-gap map

**Bias engine** (right column, bottom panel). Two layers, both visible:

- A **lexicon scan** flags coded or exclusionary phrasing in the job
  description itself (age-coded, gender-coded, elitist, ableist language),
  each with a plain-language reason and a suggested rewrite.
- A **counterfactual impact simulation** — for every hard requirement, we
  re-rank the pool *as if that requirement didn't exist* and report who
  actually moves. This is the sharper of the two: on the sample JD, the
  requirement "5+ years experience" on what is explicitly an internship
  posting is flagged **critical**, because literally 18 of 18 candidates in
  the pool fall short of it — the clause excludes the entire audience the
  role was written for. This isn't a guess; it's measured against the real
  applicant pool using the same scoring engine that produced the ranking.

**Skill-gap map** (right column, above the bias panel). A scatter of every
candidate: horizontal position is match score, vertical position is the share
of required skills *not* covered (lower is better). Two dashed guide lines
mark a 60% score threshold and a 50% gap threshold, splitting the plot into
four informal regions — strong match with few gaps (top talent), strong match
with real gaps (a generalist missing one hard requirement, worth a second
look), weak match with few gaps (a narrow specialist), and weak-match-with-gaps
(a pass). Point color follows the same badge colors above; point size reflects
how much resume text supported the score. Click a point to open that
candidate's drawer.

---

## Rubric cheat sheet — where to look for each criterion

| Rubric criterion | Weight | Where to verify it live |
|---|---|---|
| **Semantic + keyword matching, both genuinely used** | 35% | Drag the **α slider** to both extremes and watch the ranking actually reorder (not just the numbers — the *order*). Open any candidate's drawer and look at the K/M contribution bar. Under the hood: `backend/core/engine.py` computes the two channels completely independently before anything is combined. |
| **Quality and sensibility of the ranking** | 20% | The top-bar **spread** stat — a real shortlist should show real separation, not everyone at 61–68. Scores are normalised per-pool using the 5th/95th percentile (not raw min/max), specifically so one badly-scanned resume can't compress everyone else's score. |
| **Accuracy and clarity of top-3 explanations** | 20% | Open the **top 3 cards'** drawers. Every "matched" or "inferred" claim is clickable and jumps to its literal source sentence — nothing in the explanation text is asserted without a citation you can inspect yourself. (In fact the system generates this same evidence-backed explanation for *every* candidate, not only the top 3 the brief requires.) |
| **Working end-to-end demo** | 15% | The whole flow — upload or sample-load, parse, rank, explain, chat, bias-check — runs in one browser tab against one local backend, no external services. |
| **Bonus features** | 10% | Three, all live: the **bias/narrow-phrasing detector** described above, the **recruiter chat** ("Why is X above Y?", answered instantly and deterministically — see the chat dock in the right column), and a **resilient parser** that degrades gracefully on messy formatting, typos, and even corrupted or scanned-image PDFs instead of failing. |

---

## If a judge asks "how does the matching actually work?"

Hand them `docs/MATH.md` — it's written as a whiteboard script for exactly this
question — or run:

```
python scripts/explain_scoring.py --flag HIDDEN_GEM
```

which prints the complete numeric derivation for a real candidate: both raw
channel scores, the normalisation, the fusion formula with actual numbers
plugged in, and the full skill-by-skill evidence table that produced them.
