# Team Reference: Frontend Architecture & UI Component Map

React 19 + Vite, plain CSS, no component library. `framer-motion` is used for
exactly one thing: animating candidate rows as they reorder under the weight
slider, because seeing a candidate move is the point of the slider.

---

## Design language

Warm-neutral paper, ink typography, one restrained accent. Colour is spent on
one thing only — the four evidence states a recruiter learns once — so when
something *is* coloured it means something.

| Token group | Purpose |
|---|---|
| `--paper`, `--surface`, `--surface-2/3` | Ground and panel fills |
| `--ink` … `--ink-4` | Type, four levels of emphasis |
| `--accent` | The interactive spine only: slider, focus, active tab |
| `--matched` / `--inferred` / `--weak` / `--missing` | The evidence legend |

**The four status colours are semantic, not decorative.** Green = MATCHED,
blue = INFERRED, amber = WEAK, red = MISSING, used identically on rows, chips,
the evidence bar, the matrix and the ontology paths. Changing one changes the
whole application's legend.

Light is the default. `[data-theme="dark"]` swaps the same token names, and the
OS preference is followed until the user picks a side. Every colour has its
definition on bare `:root` first, so no token exists only inside a media query.

> `html` carries the background, not `body`. A body background is propagated to
> the viewport canvas, and that propagated value does not repaint reliably when
> the custom property behind it changes on theme switch.

---

## Directory layout

```
src/
  index.css              tokens, reset, shared atoms — retheme from here
  App.css                layout and components
  App.jsx                shell: state, tabs, the two toggles
  lib/
    rescore.js           client-side mirror of fusion.score()
    ui.js                theme hook, formatting, the status vocabulary
  components/
    Board.jsx            the ranked rows
    Detail.jsx           the per-candidate inspector (5 views) + <Path>
    Views.jsx            whole-pool views (4)
```

---

## `App.jsx` — shell and data flow

Two pieces of state behave differently **on purpose**:

**`alpha` never touches the network.** The backend ships every alpha-independent
sub-score, so dragging the weight slider re-ranks in the browser through
`rescore.js`. `scripts/check_parity.py` proves that arithmetic matches
`fusion.py` to 1e-9 at every alpha.

**`blind` does round-trip, and cannot change a score.** Identity is stripped at
parse time and never reaches the engine, so the server only re-renders which
name goes on a row and re-masks the resume text. That is the whole claim, and
the reason it is safe to flip mid-demo: the numbers do not move.

Five top-level tabs: Shortlist, Fusion inspector, Skill ontology, JD audit,
Candidate feedback. Selecting a candidate opens the right-hand inspector; on
narrow viewports the inspector replaces the control rail rather than squeezing it.

---

## `lib/rescore.js` — the client-side scoring engine

Mirrors `backend/core/fusion.py::score()` line for line.

```js
score = 100 × (α·K + (1−α)·M) × gate × doc_multiplier
```

`doc_multiplier` is the whole-candidate integrity penalty (today: invisible
text). It is computed server-side and alpha-independent, but it **must** be
applied here too — if the browser skipped it, the slider would quietly hand a
penalised candidate their points back.

Python's `round()` is banker's rounding and JS `toFixed` is half-away-from-zero;
`round()` here matches Python explicitly so the parity test checks the formula
rather than a rounding accident.

> **Contract:** this file and `fusion.py` must produce identical numbers. If you
> change one, change the other, and run `scripts/check_parity.py`.

Flags (`HIDDEN_GEM`, `SURFACE_MATCH`) derive from K and M, both
alpha-independent, so they never change as the slider moves and are carried
straight through from the payload.

---

## `lib/ui.js`

The status vocabulary lives here and nowhere else — `STATUS_LABEL`,
`STATUS_HELP`, `cls()`. That single definition is what stops the rows, the
matrix, the chips and the ontology view from slowly disagreeing about what amber
means.

`useTheme()` writes `data-theme` onto `document.documentElement` and persists to
`localStorage`, wrapped in try/catch — a private window or blocked site data
must not take the app down, and the theme still applies for the session.

---

## `components/Board.jsx`

One row per candidate: rank, name, flag chips, the evidence bar, score.

**`EvidenceBar`** is the component that earns its place. Two candidates on 71
points can be completely different people; the proportional split across the four
states shows that at a glance without opening anything.

Flag chips are generated from evidence, not decoration — hidden gem, surface
match, invisible text, unsupported claims, *n* proven by code, *n* unbacked, and
a parse-quality warning below 0.5. Each carries a `title` explaining itself.

---

## `components/Detail.jsx`

The per-candidate inspector, five sub-views:

| View | Shows |
|---|---|
| Evidence | The matrix, per skill, expandable to the sentence behind each cell |
| Ramp-up | Gaps priced in weeks, with the springboard and the ontological path |
| Interview | Questions targeting exactly those gaps and claims |
| Integrity | Unsupported claims, invisible text, resume-versus-code, redactions |
| Code | Repositories, what they prove, and README-only claims |

**The rule this file follows:** a number representing a judgement is never shown
without the reason beside it. The matrix has two numeric columns — **Said**
(`lex`, struck through when discounted) and **Counted** (`coverage`) — and
expanding a row explains the gap between them: the placement multiplier, the
support multiplier, the code multiplier, and the sentence they were derived from.

Also exports **`<Path>`**, the `A → B → C` ontology renderer, reused by `Views.jsx`.

---

## `components/Views.jsx`

**`FusionInspector`** — both channel rankings, the RRF score, the RRF rank and
its disagreement with the blended rank, one row per candidate.

**`TaxonomyExplorer`** — pick two skills, see each one's path, how they relate,
where they meet and what the walk costs. Below it, every non-identical match the
engine actually made in this pool, with the cosine and the resume sentence that
triggered it.

> The relation is *derived* at render (`pairing`) rather than cleared in an
> effect, so a stale relation from the previous pair cannot flash while the new
> one is in flight.

**`JobAudit`** — the bias report with measured impact per finding, then every
requirement the engine read out of the JD with the line it came from.

**`FeedbackView`** — fetched on demand (it is the expensive one), expandable per
candidate, with the draft rejection note and a copy button.

---

## Conventions

- **Panels are plain rectangles on paper with a hairline.** No glass, no glow, no
  shadow except on the two elements that genuinely float. A recruiter is reading
  dense evidence and decoration competes with it.
- **Tables scroll inside `.tablewrap`**, never the page body.
- **Every interactive row is a `<button>`** with `aria-expanded` or
  `aria-selected`, so the board is keyboard-navigable without extra handlers.
- **No inline colours.** Everything resolves through a token so both themes stay
  correct for free.
