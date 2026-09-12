/**
 * The whole-pool views: fusion inspector, taxonomy explorer, JD audit, feedback.
 *
 * These answer questions about the RANKING rather than about a person, which is
 * why they live beside the board rather than inside the candidate drawer.
 */

import { useEffect, useState } from 'react'
import { Path } from './Detail'
import { GapScatter } from './Charts'
import { delta, num, pct } from '../lib/ui'

/* ══ Side by side ═══════════════════════════════════════════════════════ */

/**
 * Two candidates, one skill list.
 *
 * The board answers "who is ahead"; this answers "on what". Only the rows where
 * exactly one of the two holds the skill are drawn at full strength — the rows
 * they agree on are the ones that explain nothing about the gap between them.
 */
export function DiffPanel({ a, b, onClose }) {
  const cellsOf = (c) => Object.fromEntries(c.primitives.cells.map((x) => [x.skill_id, x]))
  const ca = cellsOf(a)
  const cb = cellsOf(b)
  const held = (status) => status === 'MATCHED' || status === 'INFERRED'

  const ids = [...new Set([...Object.keys(ca), ...Object.keys(cb)])].sort(
    (x, y) => (cb[y]?.weight ?? 0) - (cb[x]?.weight ?? 0) || x.localeCompare(y),
  )

  const lead = a.score >= b.score ? a : b
  const trail = a.score >= b.score ? b : a
  const decisive = ids.filter((id) => held(ca[id]?.status) !== held(cb[id]?.status))

  return (
    <div className="panel" style={{ marginBottom: 'var(--s4)' }}>
      <div className="panel__head">
        <div className="panel__title">Side by side</div>
        <div className="panel__sub">{decisive.length} skills separate them</div>
        <button className="btn btn--ghost btn--icon" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="panel__body">
        <p style={{ fontSize: 12.5, color: 'var(--ink)', lineHeight: 1.55 }}>
          <strong>{lead.name}</strong> leads by {(lead.score - trail.score).toFixed(1)} points.
          {' '}Keyword {lead.k_score.toFixed(2)} against {trail.k_score.toFixed(2)},
          {' '}semantic {lead.m_score.toFixed(2)} against {trail.m_score.toFixed(2)}.
        </p>
      </div>

      <div className="panel__body" style={{ paddingTop: 0 }}>
        <div className="diff">
          <div className="diff__who">{a.name}</div>
          <div />
          <div className="diff__who diff__who--right">{b.name}</div>

          {ids.map((id) => {
            const x = ca[id]
            const y = cb[id]
            const only = held(x?.status) !== held(y?.status)
            return (
              <div key={id} style={{ display: 'contents' }}>
                <div style={{ textAlign: 'right' }}>
                  <span className={`chip chip--${(x?.status ?? 'MISSING').toLowerCase()}`}
                        style={{ opacity: only && held(x?.status) ? 1 : 0.4 }}
                        title={x?.evidence || 'No evidence'}>
                    {x?.status === 'MATCHED' ? '●' : x?.status === 'INFERRED' ? '◐' : '○'}
                  </span>
                </div>
                <div className={`diff__skill ${only ? 'diff__skill--decisive' : ''}`}>
                  {x?.label ?? y?.label}
                </div>
                <div>
                  <span className={`chip chip--${(y?.status ?? 'MISSING').toLowerCase()}`}
                        style={{ opacity: only && held(y?.status) ? 1 : 0.4 }}
                        title={y?.evidence || 'No evidence'}>
                    {y?.status === 'MATCHED' ? '●' : y?.status === 'INFERRED' ? '◐' : '○'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
        <p className="note" style={{ marginTop: 10 }}>
          ● stated · ◐ demonstrated without being named · ○ no evidence. Rows both
          candidates hold are dimmed — they cannot explain the gap.
        </p>
      </div>
    </div>
  )
}

/* ══ Cross-channel signals ═════════════════════════════════════════════ */

/**
 * The two channels disagreeing, plotted and then written out.
 *
 * A keyword filter and an embedding model rank the same pool differently, and
 * the gap between the two positions is the only place a hidden gem can be
 * found. The scatter puts score against how much of the required set is still
 * missing; the cards below name the specific candidates the channels argue
 * about and say, in numbers, what the argument is.
 */
export function SignalsView({ candidates, selected, onSelect }) {
  const gems = candidates.filter((c) => c.flag === 'HIDDEN_GEM')
  const surface = candidates.filter((c) => c.flag === 'SURFACE_MATCH')
  const consensus = candidates.length - gems.length - surface.length

  return (
    <>
      <div className="panel">
        <div className="panel__head">
          <div className="panel__title">Skill-gap map</div>
          <div className="panel__sub">{candidates.length} candidates</div>
        </div>
        <div className="panel__body">
          <p className="note" style={{ marginBottom: 10 }}>
            Match score across, unmet required skills down. Dot size is how much
            text the resume gave the engine to work with, so a small dot far
            right is a strong score built on thin evidence. Click a dot to open
            that candidate.
          </p>
          <GapScatter candidates={candidates} selectedId={selected?.doc_id}
                      onSelect={onSelect} />
          <div className="legend">
            {[['Hidden gem', 'var(--inferred)'], ['Surface match', 'var(--weak)'],
              ['Both channels agree', 'var(--ink-4)']].map(([l, c]) => (
              <span key={l} className="legend__item">
                <i style={{ background: c }} />{l}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel__head">
          <div className="panel__title">Cross-channel signals</div>
          <div className="panel__sub">
            {gems.length} gems · {surface.length} surface · {consensus} agreed
          </div>
        </div>

        <div className="panel__body">
          <p className="note">
            Each candidate is ranked twice, independently — once by BM25 and
            per-skill lexical coverage, once by sentence embeddings and per-skill
            semantic inference. Where the two positions diverge, one of the
            channels is wrong about that person, and it is worth knowing which.
          </p>
        </div>

        {gems.length === 0 && surface.length === 0 && (
          <div className="panel__body" style={{ paddingTop: 0 }}>
            <p className="note">
              The channels agree on every candidate in this pool. No one is
              being rescued or overrated by the wording of their resume.
            </p>
          </div>
        )}

        {gems.map((c) => (
          <button key={c.doc_id} className="signal signal--gem" onClick={() => onSelect(c)}>
            <div className="signal__head">
              <span className="chip chip--inferred">Hidden gem</span>
              <span className="signal__name">{c.name}</span>
              <span className="signal__rank num">#{c.rank}</span>
            </div>
            <p className="signal__why">
              Semantic rank #{c.rank_semantic} against lexical rank #{c.rank_lexical}.
              {' '}{pct(c.primitives.inferred_req_ratio)} of the required skills are
              demonstrated by the work described without ever being named, so a
              keyword filter would have dropped this résumé.
            </p>
          </button>
        ))}

        {surface.map((c) => (
          <button key={c.doc_id} className="signal signal--surface" onClick={() => onSelect(c)}>
            <div className="signal__head">
              <span className="chip chip--weak">Surface match</span>
              <span className="signal__name">{c.name}</span>
              <span className="signal__rank num">#{c.rank}</span>
            </div>
            <p className="signal__why">
              Lexical rank #{c.rank_lexical} against semantic rank #{c.rank_semantic} — the
              keyword channel likes this résumé {c.rank_semantic - c.rank_lexical} places more
              than the embedding channel does. The skills are named; the work behind
              them is thinner than the naming suggests.
              {/* Coverage is quoted only when it agrees with the rank gap. The flag
                  is set on ranks, and on a tight pool the two can point opposite
                  ways — saying so plainly beats printing a negative lead. */}
              {c.primitives.lex_cov > c.primitives.sem_cov
                ? ` Lexical coverage runs ${((c.primitives.lex_cov - c.primitives.sem_cov) * 100).toFixed(0)} points ahead of semantic.`
                : ' Per-skill coverage is close on both channels, so the gap is one of position rather than degree.'}
            </p>
          </button>
        ))}
      </div>
    </>
  )
}

/* ══ Fusion inspector ═══════════════════════════════════════════════════ */

/**
 * Both channel rankings side by side with the RRF merge.
 *
 * The point of showing all three is that they disagree. Reciprocal Rank Fusion
 * uses only positions, so it is immune to the two channels having different
 * score distributions — and blind to how far apart candidates actually are.
 * The blended score knows the distances. Neither is right on its own, and a
 * recruiter can see which candidates the two methods argue about.
 */
export function FusionInspector({ candidates, meta }) {
  const rows = [...candidates].sort((a, b) => a.rank - b.rank)

  return (
    <div className="panel">
      <div className="panel__head">
        <div className="panel__title">Fusion inspector</div>
        <div className="panel__sub">RRF k = {meta.rrf_k}</div>
      </div>

      <div className="panel__body">
        <p className="note">
          Each retrieval method ranks the pool on its own. Reciprocal Rank Fusion
          merges them by position alone — <span className="mono">
          1/(k+r<sub>keyword</sub>) + 1/(k+r<sub>semantic</sub>)</span> — so neither
          channel's score scale can dominate the other. It is shown next to the
          blended score rather than instead of it: RRF knows the order, the
          blended score knows the distances, and a candidate the two disagree
          about is exactly the one worth a second look.
        </p>
      </div>

      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Candidate</th>
              <th className="num">Score</th>
              <th className="num">r<sub>keyword</sub></th>
              <th className="num">r<sub>semantic</sub></th>
              <th className="num">Δ</th>
              <th className="num">RRF</th>
              <th className="num">RRF rank</th>
              <th className="num">vs blended</th>
              <th>Reading</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const d = delta(c.rank_delta)
              const disagree = delta(c.rank - c.rank_rrf)
              return (
                <tr key={c.doc_id}>
                  <td className="num">{c.rank}</td>
                  <td className="table__name">{c.name}</td>
                  <td className="num">{c.score.toFixed(1)}</td>
                  <td className="num">{c.rank_lexical}</td>
                  <td className="num">{c.rank_semantic}</td>
                  <td className={`num ${d.cls}`}>{d.text}</td>
                  <td className="num">{c.rrf.toFixed(5)}</td>
                  <td className="num">{c.rank_rrf}</td>
                  <td className={`num ${disagree.cls}`}>{disagree.text}</td>
                  <td>
                    {c.flag === 'HIDDEN_GEM' && <span className="chip chip--inferred">semantic finds them first</span>}
                    {c.flag === 'SURFACE_MATCH' && <span className="chip chip--weak">keyword overrates them</span>}
                    {c.flag === 'CONSENSUS' && <span className="note">both channels agree</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ══ Taxonomy explorer ══════════════════════════════════════════════════ */

/**
 * Why a non-identical match counted.
 *
 * When the engine credits "Express" towards "Node.js", that decision came from
 * an explicit tree, not a similarity number nobody can inspect. This view walks
 * that tree — and prices the distance between any two skills, which is the same
 * computation the ramp-up estimates use.
 */
export function TaxonomyExplorer({ job, candidates }) {
  const skills = job?.skills ?? []
  const [a, setA] = useState(skills[0]?.id ?? '')
  const [b, setB] = useState(skills[1]?.id ?? '')
  const [relation, setRelation] = useState(null)
  const [detail, setDetail] = useState(null)

  useEffect(() => {
    if (!a) return
    let live = true
    fetch(`/api/taxonomy?skill_id=${encodeURIComponent(a)}`)
      .then((r) => r.json()).then((d) => live && setDetail(d))
      .catch(() => {})
    return () => { live = false }
  }, [a])

  useEffect(() => {
    if (!a || !b || a === b) return
    let live = true
    fetch(`/api/taxonomy/relate?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`)
      .then((r) => r.json()).then((d) => live && setRelation(d))
      .catch(() => {})
    return () => { live = false }
  }, [a, b])

  // Derived rather than cleared in an effect: a stale relation from the previous
  // pair must not render while the new one is in flight.
  const pairing = relation && relation.a === a && relation.b === b ? relation : null

  // Every non-identical match the engine actually made in this pool.
  const inferred = []
  for (const c of candidates) {
    for (const cell of c.primitives.cells) {
      if (cell.status === 'INFERRED' && inferred.length < 14) {
        inferred.push({ candidate: c.name, cell })
      }
    }
  }

  return (
    <>
      <div className="panel">
        <div className="panel__head"><div className="panel__title">Skill ontology</div></div>
        <div className="panel__body">
          <p className="note" style={{ marginBottom: 12 }}>
            Every skill hangs off an explicit concept chain ending at Software
            Engineering, so any two skills have a lowest common ancestor and
            therefore a defensible distance. This is the structure behind every
            semantic credit the engine gives and every ramp-up estimate it makes.
          </p>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <label style={{ fontSize: 12 }}>
              <div className="eyebrow" style={{ marginBottom: 3 }}>Skill</div>
              <select className="btn" value={a} onChange={(e) => setA(e.target.value)}>
                {skills.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>
              <div className="eyebrow" style={{ marginBottom: 3 }}>Compare with</div>
              <select className="btn" value={b} onChange={(e) => setB(e.target.value)}>
                {skills.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </label>
          </div>

          {detail?.path?.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Ontological path</div>
              <Path nodes={detail.path.map((n) => n.label)} />
              <div className="note" style={{ marginTop: 8 }}>
                Difficulty {detail.difficulty}/5 ·{' '}
                {detail.neighbours?.length
                  ? <>Nearest neighbours: {detail.neighbours.join(', ')}</>
                  : 'No close neighbours in the ontology.'}
              </div>
            </>
          )}

          {pairing && (
            <div className="banner banner--info" style={{ marginTop: 14 }}>
              <div>
                <strong>{pairing.a} → {pairing.b}: {pairing.kind}</strong>
                <div style={{ marginTop: 4, color: 'var(--ink-2)' }}>
                  They meet at <strong>{pairing.common_label}</strong>, {pairing.steps} steps
                  apart. Someone holding {pairing.a} would need about <strong>{pairing.human}</strong> to
                  pick up {pairing.b}.
                </div>
                <div style={{ marginTop: 4, color: 'var(--ink-2)' }}>{pairing.note}</div>
                {pairing.via?.length > 0 && (
                  <div style={{ marginTop: 6 }}><Path nodes={pairing.via} /></div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel__head">
          <div className="panel__title">Non-identical matches in this pool</div>
          <div className="panel__sub">{inferred.length} shown</div>
        </div>
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr><th>Candidate</th><th>Credited with</th><th className="num">Cosine</th><th>Because they wrote</th></tr>
            </thead>
            <tbody>
              {inferred.map(({ candidate, cell }, i) => (
                <tr key={i}>
                  <td className="table__name">{candidate}</td>
                  <td>
                    <span className="chip chip--inferred">{cell.label}</span>
                    <div style={{ marginTop: 4 }}><Path nodes={cell.path} /></div>
                  </td>
                  <td className="num">{num(cell.sem_raw)}</td>
                  <td className="note">{cell.evidence}</td>
                </tr>
              ))}
              {inferred.length === 0 && (
                <tr><td colSpan={4} className="note">
                  No skills were credited semantically in this pool — every match was literal.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

/* ══ JD audit ═══════════════════════════════════════════════════════════ */

export function JobAudit({ job }) {
  const bias = job?.bias

  return (
    <>
      <div className="panel">
        <div className="panel__head">
          <div className="panel__title">Job description audit</div>
          {bias && <div className="panel__sub">inclusivity {bias.score}/100</div>}
        </div>
        <div className="panel__body">
          {bias ? (
            <>
              <div className="meter" style={{ marginBottom: 10 }}>
                <div className={`meter__fill ${bias.score >= 75 ? 'meter__fill--matched'
                  : bias.score >= 50 ? 'meter__fill--weak' : 'meter__fill--missing'}`}
                     style={{ width: `${bias.score}%` }} />
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--ink)' }}>{bias.summary}</p>
              <p className="note" style={{ marginTop: 8 }}>
                Two layers: a scan for coded and exclusionary wording, then an
                impact simulation that re-ranks the pool with each hard
                requirement removed — so a finding reports what a clause actually
                costs rather than asserting that it might.
              </p>
            </>
          ) : <p className="note">No audit available.</p>}
        </div>

        {bias?.findings?.length > 0 && (
          <div className="tablewrap">
            <table className="table">
              <thead><tr><th>Phrase</th><th>Why</th><th>Suggest</th><th>Measured impact</th></tr></thead>
              <tbody>
                {bias.findings.map((f, i) => (
                  <tr key={i}>
                    <td>
                      <span className={`chip chip--${f.severity === 'low' ? '' : 'missing'}`}>
                        {f.matched_text}
                      </span>
                      <div className="note" style={{ fontSize: 10.5, marginTop: 3 }}>{f.label}</div>
                    </td>
                    <td className="note">{f.why}</td>
                    <td className="note">{f.suggestion}</td>
                    <td className="note">{f.impact || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="panel__head">
          <div className="panel__title">What the engine read from this JD</div>
          <div className="panel__sub">{job?.skills?.length ?? 0} requirements</div>
        </div>
        <div className="tablewrap">
          <table className="table">
            <thead><tr><th>Skill</th><th>Tier</th><th>Found in the JD</th><th>Ontology</th></tr></thead>
            <tbody>
              {(job?.skills ?? []).map((s) => (
                <tr key={s.id}>
                  <td className="table__name">{s.label}</td>
                  <td>
                    <span className={`chip ${s.tier === 'REQUIRED' ? 'chip--accent' : ''}`}>
                      {s.tier.toLowerCase()}
                    </span>
                  </td>
                  <td className="note">{s.evidence}</td>
                  <td><Path nodes={s.path} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

/* ══ Candidate feedback ═════════════════════════════════════════════════ */

/**
 * Rejection letters worth receiving.
 *
 * Each gap is priced by re-ranking the whole pool with that gap closed, so the
 * advice is competitive rather than notional. Fetched on demand: it is the one
 * expensive derived view, and nobody needs it until they ask.
 */
export function FeedbackView({ alpha, gate, shortlist, ready }) {
  const [roadmaps, setRoadmaps] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null)

  const load = () => {
    setLoading(true); setError(null)
    fetch(`/api/feedback?alpha=${alpha}&gate=${gate}&shortlist=${shortlist}&limit=12`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)
        return r.json()
      })
      .then((d) => setRoadmaps(d.roadmaps))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  return (
    <div className="panel">
      <div className="panel__head">
        <div className="panel__title">Feedback for candidates below the cut</div>
        <button className="btn" onClick={load} disabled={loading || !ready}>
          {loading ? 'Working…' : roadmaps ? 'Recalculate' : 'Generate roadmaps'}
        </button>
      </div>

      <div className="panel__body">
        <p className="note">
          The evaluation engine, run backwards. For each rejected candidate every
          gap is closed in simulation and the whole pool re-ranked, so “this
          would have moved you four places” is a measured statement about this
          pool rather than a guess. Shortlist cut is the top {shortlist}.
        </p>
        {error && <div className="banner banner--error" style={{ marginTop: 10 }}>{error}</div>}
      </div>

      {roadmaps?.map((r) => (
        <div key={r.doc_id} style={{ borderTop: '1px solid var(--line-soft)' }}>
          <button className="mrow" style={{ gridTemplateColumns: '1fr auto' }}
                  aria-expanded={open === r.doc_id}
                  onClick={() => setOpen(open === r.doc_id ? null : r.doc_id)}>
            <div style={{ minWidth: 0 }}>
              <div className="mrow__name">#{r.rank} {r.name}</div>
              <div className="note" style={{ fontSize: 11, marginTop: 2 }}>{r.headline}</div>
            </div>
            <div className="mrow__val">{r.total_human}</div>
          </button>

          {open === r.doc_id && (
            <div className="mdetail">
              {r.steps.map((s) => (
                <div key={s.skill_id} className="gap">
                  <div className="gap__head">
                    <span className="gap__name">{s.label}</span>
                    <span className="chip chip--mono">
                      {s.places_gained > 0 ? `+${s.places_gained} places` : `+${s.score_gain} pts`}
                    </span>
                    <span className="gap__time">{s.human_time}</span>
                  </div>
                  <p className="gap__note"><strong style={{ color: 'var(--ink-2)' }}>Build:</strong> {s.project}</p>
                  <p className="gap__note"><strong style={{ color: 'var(--ink-2)' }}>Start with:</strong> {s.first_step}</p>
                </div>
              ))}

              <div style={{ marginTop: 12 }}>
                <div className="eyebrow" style={{ marginBottom: 5 }}>Draft rejection note</div>
                <pre className="email">{r.email}</pre>
                <button className="btn" style={{ marginTop: 8 }}
                        onClick={() => navigator.clipboard?.writeText(r.email)}>
                  Copy to clipboard
                </button>
              </div>
            </div>
          )}
        </div>
      ))}

      {roadmaps?.length === 0 && (
        <div className="panel__body"><p className="note">Nobody fell below the cut.</p></div>
      )}
    </div>
  )
}
