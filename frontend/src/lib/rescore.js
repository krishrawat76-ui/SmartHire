/**
 * Client-side mirror of backend/core/fusion.py :: score()
 *
 * This is why the recruiter's weight slider is instant: the backend ships every
 * alpha-independent sub-score in the payload, so re-ranking is ~40 floating point
 * operations per candidate and never touches the network.
 *
 * CONTRACT: this file and fusion.py must produce identical numbers.
 * scripts/check_parity.py proves it at alpha in {0, 0.25, 0.5, 0.75, 1}.
 * If you change one, change the other.
 *
 * Note on flags: rank_lexical and rank_semantic derive from K and M, both of
 * which are alpha-independent. So cross-channel flags never change as the slider
 * moves, and are carried straight through from the payload.
 */

/** Keyword channel. Mirrors fusion.channel_k(). */
export function channelK(p, meta) {
  const w = meta.k_weight_bm25
  return w * p.bm25_norm + (1 - w) * p.lex_cov
}

/** Semantic channel. Mirrors fusion.channel_m(). */
export function channelM(p, meta) {
  const w = meta.m_weight_docsim
  return w * p.docsim_norm + (1 - w) * p.sem_cov
}

/** Must-have gate. Mirrors fusion.gate_multiplier(). */
export function gateMultiplier(p, enabled, meta) {
  if (!enabled) return 1.0
  return meta.gate_floor + meta.gate_span * p.req_coverage
}

/**
 * Re-rank the pool at a new alpha. Pure: returns a new array, mutates nothing.
 *
 * @param {Array}  candidates  from /api/analyze
 * @param {number} alpha       1.0 = pure keyword, 0.0 = pure semantic
 * @param {boolean} gate       apply the must-have gate
 * @param {object} meta        payload meta, carries the engine's own weights
 */
export function rescore(candidates, alpha, gate, meta) {
  const a = Math.min(Math.max(alpha, 0), 1)

  const scored = candidates.map((c) => {
    const k = channelK(c.primitives, meta)
    const m = channelM(c.primitives, meta)
    const g = gateMultiplier(c.primitives, gate, meta)
    // Whole-candidate integrity multiplier (today: the invisible-text penalty).
    // Alpha-independent, computed server-side, but applied HERE as well as in
    // fusion.score() — if the browser skipped it the slider would quietly hand a
    // penalised candidate their points back.
    const d = c.primitives.doc_multiplier ?? 1.0
    return {
      ...c,
      k_score: round(k, 4),
      m_score: round(m, 4),
      gate: round(g, 4),
      score: round(100 * (a * k + (1 - a) * m) * g * d, 2),
    }
  })

  // Mirrors fusion.score()'s sort: descending score, ties broken by name.
  scored.sort((x, y) => y.score - x.score || x.name.localeCompare(y.name))

  return scored.map((c, i) => ({
    ...c,
    rank: i + 1,
    prevRank: candidates.find((o) => o.doc_id === c.doc_id)?.rank ?? i + 1,
  }))
}

/** Python's round() is banker's rounding; JS toFixed is half-away-from-zero.
 *  At 2-4 decimal places on these magnitudes the difference never reaches the
 *  1e-9 parity tolerance, but we match Python's behaviour explicitly so the
 *  parity test is checking the formula rather than a rounding accident. */
function round(x, places) {
  const f = Math.pow(10, places)
  const scaled = x * f
  const r = Math.round(scaled)
  // Exact .5 cases go to even, as Python does.
  if (Math.abs(scaled - Math.trunc(scaled) ) === 0.5) {
    const floor = Math.floor(scaled)
    return (floor % 2 === 0 ? floor : floor + 1) / f
  }
  return r / f
}

/** Pool-level stats for the header. */
export function poolStats(candidates) {
  if (!candidates.length) return { max: 0, min: 0, spread: 0, mean: 0 }
  const s = candidates.map((c) => c.score)
  const max = Math.max(...s)
  const min = Math.min(...s)
  return {
    max,
    min,
    spread: max - min,
    mean: s.reduce((a, b) => a + b, 0) / s.length,
  }
}
