/**
 * The board: one row per candidate, ranked.
 *
 * Each row carries the four numbers a recruiter actually scans for — position,
 * score, how much of the JD is covered, and what kind of evidence that coverage
 * is made of. The evidence bar is the important one: two candidates on 71 points
 * can be completely different people, and the bar shows that at a glance without
 * anyone opening a drawer.
 */

import { useEffect, useState } from 'react'
import { motion, AnimatePresence, LayoutGroup } from 'framer-motion'
import { STATUSES, STATUS_LABEL, cls, pct, statusCounts } from '../lib/ui'

/* ── Loading ───────────────────────────────────────────────────────────────
   A first run embeds eighteen resumes and can fetch public repositories, which
   takes long enough that a bare spinner reads as a hang. These are the real
   stages in pipeline order, advanced on a timer rather than reported by the
   server — the wait is worth narrating, and the order is honest even though
   the timing is approximate. */

const STAGES = [
  'Parsing PDFs and stripping identity',
  'Reading requirements out of the job description',
  'Embedding resume chunks',
  'Scoring both channels, skill by skill',
  'Checking claims against public code',
  'Fusing, flagging and explaining',
]

function LoadingBoard() {
  const [stage, setStage] = useState(0)

  useEffect(() => {
    const t = setInterval(() => setStage((n) => Math.min(n + 1, STAGES.length - 1)), 2600)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="board">
      <div className="panel">
        <div className="panel__body">
          {STAGES.map((label, i) => (
            <div key={label} className="stage" style={{ opacity: i <= stage ? 1 : 0.35 }}>
              <span className="stage__tick">
                {i < stage ? '✓' : i === stage ? '▸' : '·'}
              </span>
              {label}{i === stage ? '…' : ''}
            </div>
          ))}
        </div>
      </div>
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="skeleton" style={{ opacity: 1 - i * 0.13 }} />
      ))}
    </div>
  )
}

function EvidenceBar({ candidate }) {
  const counts = statusCounts(candidate)
  const total = STATUSES.reduce((a, s) => a + counts[s], 0) || 1

  return (
    <div>
      <div className="evbar" role="img"
           aria-label={STATUSES.map((s) => `${counts[s]} ${STATUS_LABEL[s]}`).join(', ')}>
        {STATUSES.map((s) => counts[s] > 0 && (
          <div key={s} className={`evbar__seg evbar__seg--${cls(s)}`}
               style={{ width: `${(counts[s] / total) * 100}%` }} />
        ))}
      </div>
      <div className="evbar__legend">
        {STATUSES.map((s) => counts[s] > 0 && (
          <span key={s}>{counts[s]} {STATUS_LABEL[s].toLowerCase()}</span>
        ))}
      </div>
    </div>
  )
}

function Flags({ candidate }) {
  const p = candidate.primitives
  const out = []

  if (candidate.flag === 'HIDDEN_GEM') {
    out.push(<span key="gem" className="chip chip--inferred" title="Required skills demonstrated but never named — a keyword filter would miss this person.">Hidden gem</span>)
  }
  if (candidate.flag === 'SURFACE_MATCH') {
    out.push(<span key="surf" className="chip chip--weak" title="Names the skills, shows little work behind them.">Surface match</span>)
  }
  if (candidate.integrity?.hidden_flag) {
    out.push(<span key="hid" className="chip chip--missing" title="Invisible text found in the PDF.">Invisible text</span>)
  }
  if (candidate.integrity?.stuffing_flag) {
    out.push(<span key="stuff" className="chip chip--missing" title={candidate.integrity.headline}>Unsupported claims</span>)
  }
  if (p.external_proven > 0) {
    out.push(<span key="gh" className="chip chip--matched" title="Skills proven by public code.">{p.external_proven} proven by code</span>)
  }
  if (p.external_contradicted > 0) {
    out.push(<span key="bad" className="chip chip--missing" title="Claimed but absent from every public repository.">{p.external_contradicted} unbacked</span>)
  }
  if (p.quality < 0.5) {
    out.push(<span key="q" className="chip chip--weak" title="This PDF was hard to read; review manually.">Parse {p.quality.toFixed(2)}</span>)
  }
  return out
}

export default function Board({ candidates, selected, onSelect, loading, compare, onCompare }) {
  if (loading) return <LoadingBoard />

  return (
    <LayoutGroup>
      <div className="board" role="listbox" aria-label="Ranked candidates">
        <AnimatePresence initial={false}>
          {candidates.map((c) => (
            /* A div, not a button: the compare toggle is a control of its own
               and nesting one button inside another is invalid markup that
               screen readers handle badly. Keyboard behaviour is restored
               explicitly below. */
            <motion.div
              key={c.doc_id}
              layout
              transition={{ type: 'spring', stiffness: 520, damping: 42 }}
              className={`row ${c.rank <= 3 ? 'row--top' : ''} ${
                c.primitives.doc_multiplier < 1 ? 'row--penalised' : ''}`}
              role="option"
              tabIndex={0}
              aria-selected={selected?.doc_id === c.doc_id}
              onClick={() => onSelect(c)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(c) }
              }}
            >
              <div className="row__rank num">{String(c.rank).padStart(2, '0')}</div>

              <div style={{ minWidth: 0 }}>
                <div className="row__name">{c.name}</div>
                <div className="row__meta">
                  <span className="chip chip--mono">{pct(c.primitives.req_coverage)} required</span>
                  <Flags candidate={c} />
                  {onCompare && (
                    <button
                      className={`chip chip--mono ${compare?.includes(c.doc_id) ? 'chip--accent' : ''}`}
                      aria-pressed={compare?.includes(c.doc_id) ?? false}
                      title="Pick two candidates to see them side by side."
                      onClick={(e) => { e.stopPropagation(); onCompare(c.doc_id) }}
                    >
                      compare
                    </button>
                  )}
                </div>
              </div>

              <EvidenceBar candidate={c} />

              <div className="row__score">
                <div className="row__score-v">{c.score.toFixed(1)}</div>
                <div className="row__score-sub">K {c.k_score.toFixed(2)} · M {c.m_score.toFixed(2)}</div>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </LayoutGroup>
  )
}
