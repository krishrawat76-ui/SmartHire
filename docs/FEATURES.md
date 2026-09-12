# Features: what each one decides, and why it decides it that way

Every feature here reads the same evidence matrix. None of them calls a language
model. This document records the decision behind each, including the ones that
were wrong first — those are the parts worth reading.

---

## 1. Contextual recency and depth decay

**Module:** `backend/core/context.py` · **Config:** `SECTION_CONTEXT_WEIGHT`,
`BARE_LIST_*`, `RECENCY_*`

A keyword engine scores "React" in a recent internship identically to "React" in
a comma-separated list from a first-year lab. That single fact is most of why
ATS ranking feels arbitrary to candidates and useless to recruiters.

Three signals multiply into one weight on **lexical** evidence:

| Signal | Rule |
|---|---|
| Section | EXPERIENCE 1.00 · PROJECTS 0.95 · EDUCATION 0.60 · SKILLS 0.55 · INTERESTS 0.40 |
| Shape | A bare tag inside a delimited run × 0.75, detected by line shape not section |
| Recency | Decay to a floor of 0.55, half-life 30 months, nothing inside 12 months |

**Decisions worth defending.**

*Lexical only.* The semantic channel already reads surrounding prose and prices
context itself. Applying the multiplier there too would bill a candidate twice
for one weakness.

*Undated chunks do not decay.* Punishing someone for a layout we failed to parse
would invent a signal rather than measure one.

*The document's own horizon, not the wall clock.* A two-year-old CV is aged
relative to its own most recent date, so its latest role is still its latest role.

*Status never moves.* Whether the resume says a word is a fact about the
document. Only `lex_eff` — what the claim is worth — changes, so the UI can say
"stated, but discounted to 41%, and here is why" instead of silently reporting a
stated skill as missing.

**Measured:** the planted keyword-stuffer's claims are worth 0.20 of face value;
genuine candidates sit around 0.90.

---

## 2. Skill ontology and the graph explorer

**Module:** `backend/core/taxonomy.py` · **Data:** `backend/data/taxonomy.json`

42 concepts, 89 skills, every one hanging off a chain terminating at
`software-engineering`. Any two skills therefore have a lowest common ancestor
and a structural distance, with no model in the loop and nothing to take on faith.

```
FastAPI → Python Backend → Backend Engineering → Software Engineering
Postgres → Relational Databases → Data Persistence → Software Engineering
```

**Relation kinds**, in the order they are tested: an explicit `also` edge →
`sibling`; a common ancestor that is the root → `discipline`; same immediate
parent → `sibling`; within one level *and* a common ancestor at depth ≥ 2 →
`family`; otherwise `domain`.

**Two bugs this shape fixed.**

*Root-level siblings.* `full stack` hangs directly off the root, so before the
root check ran first it read as a sibling of anything else that does — producing
"Agile, one week away from Full Stack".

*Shallow families.* Git and AWS share `devops`, so a plain "within one level"
rule priced Git → AWS like Flask → FastAPI. Requiring a *specific* common
ancestor (depth ≥ 2) separates a change of tool from a change of subject.

---

## 3. Bridge time

**Module:** `backend/core/rampup.py` · **Config:** `BASE_WEEKS`,
`DIFFICULTY_SCALE`, `RAMPUP_*`

```
weeks = BASE_WEEKS[relation] × DIFFICULTY_SCALE[difficulty] × (1.25 − 0.35 × strength)
```

`BASE_WEEKS`: sibling 1.0 · family 1.5 · domain 2.5 · discipline 6.0 ·
unrelated 9.0. Difficulty is intrinsic to the skill (1 = a lookup table,
5 = a mental model). Strength is how firmly the springboard is held.

**Pinned bridges are authoritative.** `taxonomy.json` carries 13 measured pairs,
and a pin wins over any cheaper computed walk. Without that rule Linux quietly
undercut the deliberately-pinned Docker → Kubernetes figure, because tree shape
said "same family" and the pin said "four weeks".

**Aggregation decays, it does not sum.** `combined_weeks` pays the largest gap in
full and each subsequent one at 0.6ⁱ. A naive sum turns a strong candidate with
three small gaps into a fictional six-month project.

| Scenario | Result |
|---|---|
| React → Vue | ~1 week (pinned) |
| Python → Node.js | 2–3 weeks (pinned) |
| Docker → Kubernetes | 3–4 weeks (pinned) |
| Frontend-only → System Design | 2+ months (computed, discipline) |
| Nothing adjacent → Kubernetes | 3+ months (computed, unrelated) |

---

## 4. Adversarial detection

**Module:** `backend/core/integrity.py` · **Detection:** `parser.find_hidden()`

**Keyword stuffing.** A genuine skill leaves a trace in the work. For every named
skill the engine asks where else it appears; one living only in the inventory is
an *orphan claim*, discounted to 0.55 rather than deleted — a terse resume is not
a dishonest one. The pool-level flag needs ≥ 3 orphans *and* ≥ 45% of all named
skills.

A resume with no detectable sections stays silent: everything lands in "other"
and nothing can be called an orphan. The messy-format candidate is disorganised,
not dishonest.

**Invisible text.** PDF span colour and size are read directly, so white-on-white
and 1pt keyword dumps are visible to us precisely because they are invisible to
everyone else. Penalty ×0.40 on the whole candidate. Best-effort by design — a
white rectangle drawn over black text is not modelled, and silence from this
function is never treated as a clean bill of health.

**Measured:** the planted stuffer scores orphan ratio 1.00; genuine candidates
0.00–0.37.

---

## 5. External evidence: GitHub and LinkedIn

**Module:** `backend/core/enrich.py` · **Data:** `backend/data/library_map.json`

136 packages, 18 imports, 37 file patterns and 23 languages map to 73 of the 89
gazetteer skills. The remaining 16 are concepts no manifest can prove — system
design, Agile, OOP — and they are never penalised for that.

Per repository: one tree call (recursive), one languages call, up to four
manifests, one README. GitHub's own language stats below 5% of a repo are
ignored — that is a stray config file, not a language used.

**Weights.** GitHub 1.00 with a ×1.35 score multiplier; LinkedIn 0.35 with ×1.08
and the hard rule that it may only *support* a claim the resume already makes.

**On LinkedIn, honestly.** There is no public profile API for this and automated
access is blocked and prohibited by their terms. The provider reads a supplied
export at `fixtures/profiles/linkedin/<handle>.json` and otherwise reports
`unavailable` with the reason. That is the ceiling of what this source can
lawfully contribute, and the low weight reflects that even a complete profile
here is self-reported.

**Three hard rules:** off by default, never blocks (every call timeout-bounded,
every failure degrading to "no external evidence"), cached to disk.

> Unauthenticated GitHub allows 60 requests/hour — not enough for one pool.
> `GITHUB_TOKEN` lifts it to 5000. `fixtures/profiles/` exists so the channel
> demonstrates offline; those profiles are synthetic and say so.

---

## 6. Claim verification — README against imports

**Module:** `backend/core/verification.py`

| Verdict | Meaning | Effect |
|---|---|---|
| CORROBORATED | Resume says it, code proves it | ×1.35 |
| CONTRADICTED | Unsupported claim, absent from substantial public code | ×0.50 |
| UNVERIFIABLE | No manifest could ever prove it | none |
| UNVERIFIED | No code, too little code, or the resume backs it itself | none |

**The asymmetry is the design.** Corroboration needs one positive fact.
Contradiction needs a fact to be *missing from where it should have been*, which
is far weaker — so it is gated three ways: `VERIFY_MIN_REPOS`, a detectable
skill, and a claim the resume does not already support.

**The bug that produced the third gate.** A candidate whose bullet read
*"containerised the service with Docker and deployed to AWS ECS"* was marked as
contradicting their own CV, because an employer's repository is private and
always will be. Absence of private work from a public profile is not evidence of
dishonesty. Now only an unsupported claim — a bare tag, no project, no code —
can be contradicted.

**Effect:** the honest top candidate went from 4 contradictions to 2 (both
genuine bare-list-only claims); the stuffer kept 10.

**README-only claims** are tracked separately: a README advertising Kubernetes
over a repo whose only manifest is Express and Mongoose is a claim about a
project, not a capability.

---

## 7. Reciprocal Rank Fusion inspector

**Module:** `backend/core/fusion.py::score` · **UI:** `Views.jsx::FusionInspector`

$$\text{RRF}(d) = \frac{1}{k + r_{\text{keyword}}(d)} + \frac{1}{k + r_{\text{semantic}}(d)}$$

with `RRF_K = 60`. Both channel ranks, the RRF score, the RRF rank and its
disagreement with the blended rank are all shown.

RRF is reported *alongside* the blended score, not instead of it. It uses only
positions, so no channel's score distribution can dominate — and it is blind to
how far apart candidates actually are, which the blended score knows. Neither is
correct alone.

---

## 8. Blind screening

**Module:** `backend/core/blind.py` · **Config:** `REDACT_BEFORE_SCORING`

Redaction runs at parse time, always, before the engine sees anything. PII
carries no skill signal, so removing it costs nothing and buys the claim: **the
ranking is blind by construction, not by policy.** The toggle changes only which
name is printed on a row.

*Verified:* blind and non-blind responses produce byte-identical scores and
identical ordering.

**Removed:** names, emails, phones, addresses, universities (matched on the
*shape* of an institution name, so it generalises past one corpus), graduation
years, gender markers, personal details.

**Deliberately kept for matching:** the GitHub link — the code behind it is
exactly the signal we want. It is masked from *display* in blind mode, because a
readable handle would make the exercise theatre.

**Graduation years are section-aware.** A bare "2022 – 2026" is stripped inside
EDUCATION only. The identical shape in EXPERIENCE is an employment date, and the
entire recency model is built on those — stripping them globally would trade one
fairness gain for a worse one.

**The JD is never redacted.** The bias detector hunts for gendered pronouns and
school filters, which are precisely the strings redaction removes.

---

## 9. Targeted interview questions

**Module:** `backend/core/interview.py` · **Data:** `backend/data/coaching.json`

Four kinds, in priority order: **GAP** (required skill missing — asks how they
would transfer from what they have), **CLAIM** (unsupported or contradicted),
**INFERRED** (demonstrated but never named — if it holds, every keyword filter
underrates them), **PROJECT** (grounded in a repository they pushed).

**Commodity filter.** An orphan "HTML" or "Git" is technically an unsupported
claim and tells an interviewer nothing — everyone lists them. CLAIM questions
require difficulty ≥ 3, unless the candidate's own code contradicts the claim,
in which case any skill earns one question. Before this filter, the top
candidate's guide was three questions about CSS, HTML and Git.

Claim questions are deliberately open — *"tell me about the last thing you built
with X"* — not accusations. A real project produces a specific answer instantly;
a padded line does not.

---

## 10. "What if" feedback for rejected candidates

**Module:** `backend/core/feedback.py` · **Endpoint:** `GET /api/feedback`

Ranking missing skills by weight answers the wrong question: a skill the whole
pool lacks costs nobody anything. So each gap is **simulated** — closed, the
whole pool re-ranked, and the movement measured. Only the target candidate's
matrix changes, so the counterfactual is exactly what is claimed.

Because one skill rarely moves anyone in a tight pool, the top gaps are also
simulated *together*, and the headline reports whichever is true:

- "…together would have moved them 1 place, to #4."
- "…together are worth 4.3 points — not enough to clear this pool, but the
  largest gains available to them."

The draft rejection note deliberately omits rank and score: a number is a
comparison against people the candidate cannot see and cannot act on. The gaps,
the time to close them and the project to build are about them.

Its own endpoint because it is the one expensive derived view — cost grows with
(candidates × gaps) — and nobody needs it until they ask.

---

## Pipeline order

Fixed, and the order is load-bearing:

```
prepare()  →  integrity + enrichment  →  verification  →  adjust()  →  score()
```

`adjust()` is the only thing that mutates the matrix, and it is handed one
`Adjustment` per candidate built in `assess.py` — so every multiplier in the
system is visible in one function. Integrity must precede verification, because
verification asks the resume whether it stands behind a claim before it is
willing to call that claim contradicted.

`fusion.aggregate()` is the single accumulation, called by `prepare()`,
`adjust()` and `without_skills()`. Two copies would drift and a coverage number
would start disagreeing with the cells shown underneath it.

**Parity.** Only `score()` is mirrored in `rescore.js`, and everything above
runs server-side, so `scripts/check_parity.py` still proves agreement to 1e-9 at
every alpha. The whole-candidate `doc_multiplier` is applied inside `score()` and
mirrored in JS — if the browser skipped it, the slider would quietly hand a
penalised candidate their points back.
