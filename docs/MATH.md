# How the matching actually works

**A whiteboard script.** The judges will ask this. Aim for ninety seconds, one
diagram, and be ready to go deeper on any line. Run
`python scripts/explain_scoring.py --flag HIDDEN_GEM` to rehearse against real numbers.

---

## The one-sentence version

> For every skill the job description asks for, we compute **two independent
> pieces of evidence** — does the resume *say* it, and does the resume *show* it —
> then fuse them per skill into one evidence matrix. That matrix produces the
> score, the explanations, and the chatbot's answers.

---

## Why not just embed both documents and take a cosine?

Because it fails three ways at once, and saying so out loud demonstrates we
understood the problem rather than reached for the first tool:

1. **Scores cluster.** Raw cosine on full resumes lands everything in a narrow
   band. The brief explicitly penalises "all-similar scores".
2. **It cannot explain itself.** One number per resume can't say *which* skill
   matched, which is 20% of the rubric.
3. **It isn't keyword matching.** That's 35% of the rubric gone.

So we build a matrix, not a number.

---

## Step 1 — What does the JD actually require?

A curated gazetteer (~95 skills, each with aliases and a descriptive phrase)
intersected with the JD text, then tiered by scanning which requirement block
each line falls in:

```
"Must have experience with Node.js"      -> REQUIRED   w = 1.0
"TypeScript is a strong plus"            -> PREFERRED  w = 0.5
```

Inline hedges beat the surrounding block, so a "nice to have" inside a Required
section still lands as preferred.

---

## Step 2 — Channel K, the keyword channel

Two measures, because each covers the other's blind spot.

**BM25-Okapi** across the 18-resume pool. Corpus-aware IDF means a term every
candidate has contributes little, while a rare must-have discriminates hard.

```
BM25(R) = Σ IDF(t) · f(t,R)(k₁+1) / [ f(t,R) + k₁(1 − b + b·|R|/avgdl) ]
          k₁ = 1.5, b = 0.75
```

**Weighted lexical coverage** — requirement-aware, bounded, interpretable:

```
lexᵢ(R) = 1.00  exact or alias match       ("Mongo DB" -> mongodb)
        = 0.80  RapidFuzz ratio ≥ 88       (unanticipated typos)
        = 0.00  otherwise

LexCov(R) = Σ wᵢ·lexᵢ / Σ wᵢ

K(R) = 0.40 · norm(BM25) + 0.60 · LexCov
```

> **Why 0.40, not 0.50?** Measured, not guessed. BM25 rewards verbosity: on our
> corpus the messy run-on resume scores the pool maximum on BM25 while covering
> fewer requirements than three candidates above it. The task is "does this
> person meet these requirements", not "does this document resemble that
> document", so the requirement-aware half leads.

---

## Step 3 — Channel M, the semantic channel

`all-MiniLM-L6-v2`, 384 dimensions, running locally on CPU.

**We embed chunks, not documents.** This is the single most important
implementation decision. A whole-resume embedding averages a weekend hobby
project into a three-year internship and washes out every signal worth having.

```
DocSim(R) = mean( top-3 { cos(emb(cⱼ), emb(JD)) } )
```

Top-k pooling rather than mean-pooling: mean-pooling punishes a candidate for
having a rich resume with sections irrelevant to *this* JD. Top-3 asks "what is
the best evidence this person fits" — what a recruiter actually does.

```
semᵢ(R) = max_j cos( emb(skill_phraseᵢ), emb(cⱼ) )

g(x)    = clip( (x − 0.20) / (0.55 − 0.20), 0, 1 )

SemCov(R) = Σ wᵢ·g(semᵢ) / Σ wᵢ

M(R) = 0.25 · norm(DocSim) + 0.75 · SemCov
```

> **Why is DocSim only 0.25?** Because whole-document similarity is partly a
> proxy for lexical overlap: a resume that is a bare *list of skill names*
> embeds very close to a JD that *lists skill names*. On our corpus the planted
> keyword-stuffer scores `docsim_norm = 1.000`, the pool maximum, while
> per-skill `SemCov` correctly rates her `0.306`. At parity, the semantic
> channel would be fooled by exactly the candidate it exists to catch.

> **Where did τ = 0.20 / 0.55 come from?** Measured across 306 (skill ×
> candidate) pairs on the corpus, split by whether the lexical channel agreed:
>
> | population | p10 | p50 | p90 |
> |---|---|---|---|
> | lexically MATCHED (n=131) | 0.268 | 0.447 | 0.670 |
> | lexically ABSENT (n=175) | 0.068 | 0.218 | 0.370 |
>
> They separate cleanly but overlap between ~0.25 and ~0.50. The band spans
> exactly that discriminative region.

---

## Step 4 — The keystone: per-skill fusion

```
covᵢ(R) = max( lexᵢ(R), g(semᵢ(R)) )
```

**A soft-OR, not a sum.** Literal presence and semantic inference are
*alternative* forms of evidence for the same claim. A candidate who says "React"
and one who demonstrably builds React components both have React; adding the two
signals would double-count.

Each cell gets a label, and the label is what the whole reasoning layer consumes:

| Status | Condition | Meaning |
|---|---|---|
| **MATCHED** | `lexᵢ ≥ 0.8` | stated outright |
| **INFERRED** | `lex = 0` and `g(sem) > 0.55` | practised but never named |
| **WEAK** | `0.25 < g(sem) ≤ 0.55` | adjacent experience |
| **MISSING** | otherwise | a genuine gap |

`INFERRED` is the problem statement's own example, made visible as a UI state.

---

## Step 5 — Normalisation, and the trade-off we accept

```
norm(x) = clip( (x − P₅) / (P₉₅ − P₅), 0, 1 )
```

P₅/P₉₅ rather than min/max is deliberate: one scanned resume yielding forty
characters would otherwise define the floor and compress every real candidate
into the top sliver of the range.

**Say this before a judge asks.** Pool-relative scores are *comparative*, not
absolute — 92 means "best in this pool", not "92% qualified". For a shortlisting
task that is the right semantics, and we hedge by showing absolute required-skill
coverage alongside the relative match score.

---

## Step 6 — Final score

```
S(R) = 100 · [ α·K(R) + (1−α)·M(R) ] · Gate(R)

Gate(R) = 0.60 + 0.40 · ReqCoverage(R)    when enabled
        = 1.0                              default
```

The gate is floored at 0.60 so it re-ranks rather than annihilates — a recruiter
should still be able to see a strong near-miss.

α is the recruiter's slider. Every term above is α-independent, which is why the
browser can re-rank the whole pool in ~40 floating-point operations per candidate
with no network call. `scripts/check_parity.py` proves the JS and Python agree
to 1e-9.

---

## Step 7 — Where RRF actually earns its place

```
RRF(R) = 1/(60 + rank_K) + 1/(60 + rank_M)
Δ(R)   = rank_K − rank_M
```

**RRF does not drive the score, on purpose.** It is scale-free and robust, which
makes it excellent at *detecting disagreement* — but it discards magnitude, which
would break both the per-candidate score the brief requires and the continuous
slider. We use each algorithm for what it is good at.

So the two channels' disagreement becomes a *flag*, driven primarily by the
evidence matrix rather than pool position:

- **HIDDEN GEM** — ≥15% of required skills are INFERRED (and the candidate
  clears 60% coverage). Right experience, wrong vocabulary.
- **SURFACE MATCH** — lexical coverage runs ≥0.40 ahead of semantic. Names the
  skills, shows no work.

> We tried rank-delta alone first. At 18 candidates a 4-place swing is often just
> churn in the middle of the pack — it flagged the *typo* candidate as a gem and
> missed the real one. A candidate can be mid-ranked in **both** channels and
> still be a genuine hidden gem, which rank delta structurally cannot see.
> Rank delta is retained as a corroborating trigger at a wider threshold.

---

## The line to close on

> Nothing here consults a language model. BM25 and a sentence-transformer, fused
> per skill, with every claim carrying the character offsets of the sentence that
> produced it. `grep -ri "openai\|anthropic\|api_key" backend/` returns nothing,
> and `scripts/verify.py` checks that on every run.
