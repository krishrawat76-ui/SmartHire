/**
 * Application shell.
 *
 * Two pieces of state deserve a note, because they behave differently on purpose:
 *
 *   alpha   never touches the network. The backend ships every alpha-independent
 *           sub-score, so dragging the weight slider re-ranks in the browser via
 *           lib/rescore.js. scripts/check_parity.py proves that arithmetic
 *           matches fusion.py to 1e-9.
 *
 *   blind   DOES round-trip, and cannot change a single score. Identity is
 *           stripped at parse time and never reaches the engine, so the server
 *           only re-renders which name goes on a row. That is the whole claim,
 *           and the reason it is safe to flip mid-demo: the numbers do not move.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { rescore, poolStats } from './lib/rescore'
import { useTheme } from './lib/ui'
import Board from './components/Board'
import Detail from './components/Detail'
import ChatDock from './components/Chat'
import { DiffPanel, SignalsView, FusionInspector, TaxonomyExplorer, JobAudit, FeedbackView }
  from './components/Views'

const TABS = [
  ['board', 'Shortlist'],
  ['signals', 'Signals'],
  ['fusion', 'Fusion inspector'],
  ['taxonomy', 'Skill ontology'],
  ['audit', 'JD audit'],
  ['feedback', 'Candidate feedback'],
]

function Toggle({ checked, onChange, label, title }) {
  return (
    <button className="toggle" role="switch" aria-checked={checked}
            onClick={() => onChange(!checked)} title={title}>
      <span className="toggle__track"><span className="toggle__thumb" /></span>
      <span className="toggle__label">{label}</span>
    </button>
  )
}

export default function App() {
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const [alpha, setAlpha] = useState(0.5)
  const [gate, setGate] = useState(false)
  const [blind, setBlind] = useState(false)
  const [enrichment, setEnrichment] = useState(true)

  const [tab, setTab] = useState('board')
  const [selected, setSelected] = useState(null)
  // At most two, most-recently-picked wins — a diff of three is a table, not a diff.
  const [compare, setCompare] = useState([])

  const [theme, toggleTheme] = useTheme()
  const fileRef = useRef(null)

  /* Ranking recomputed in the browser from sub-scores already in the payload. */
  const candidates = useMemo(() => {
    if (!payload) return []
    return rescore(payload.candidates, alpha, gate, payload.meta)
  }, [payload, alpha, gate])

  const stats = useMemo(() => poolStats(candidates), [candidates])

  // Keep an open inspector pointed at fresh numbers as the slider moves.
  const liveSelected = selected
    ? candidates.find((c) => c.doc_id === selected.doc_id) ?? null
    : null

  const run = useCallback(async (fetcher) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetcher()
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Request failed (${res.status})`)
      }
      const data = await res.json()
      setPayload(data)
      setAlpha(data.meta.alpha)
      setGate(data.meta.gate)
      setBlind(data.meta.blind)
      setSelected(null)
      setCompare([])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadSample = () => run(() => fetch(
    `/api/analyze/sample?enrichment=${enrichment}&blind_mode=${blind}`, { method: 'POST' }))

  const upload = (files) => {
    const list = [...files]
    const jd = list.find((f) => /jd|job|description/i.test(f.name)) ?? list[0]
    const resumes = list.filter((f) => f !== jd)
    if (!resumes.length) {
      setError('Select a job description plus at least one resume.')
      return
    }
    const form = new FormData()
    form.append('jd', jd)
    for (const r of resumes) form.append('resumes', r)
    form.append('enrichment', String(enrichment))
    form.append('blind_mode', String(blind))
    run(() => fetch('/api/analyze', { method: 'POST', body: form }))
  }

  /* Blind mode is a server-side re-render: pseudonyms come from pool position,
     and the resume text has to be masked for display. Scores are untouched. */
  const setBlindMode = useCallback((next) => {
    setBlind(next)
    if (!payload) return
    fetch(`/api/rank?alpha=${alpha}&gate=${gate}&blind_mode=${next}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setPayload(d))
      .catch(() => {})
  }, [payload, alpha, gate])

  const toggleCompare = useCallback((docId) => {
    setCompare((prev) => (prev.includes(docId)
      ? prev.filter((d) => d !== docId)
      : [...prev, docId].slice(-2)))
  }, [])

  const comparePair = compare
    .map((id) => candidates.find((c) => c.doc_id === id))
    .filter(Boolean)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setSelected(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  /* ── Empty state ─────────────────────────────────────────────────────── */
  if (!payload && !loading) {
    return (
      <div className="app">
        <div className="empty">
          <div className="empty__inner">
            <h1>Rank a batch of resumes against one job description.</h1>
            <p>
              Two independent channels — BM25 with per-skill lexical coverage, and
              sentence embeddings with per-skill semantic inference — fused into one
              ranking. Every score traces back to a line of text, weighted by where
              on the resume that line appears and whether any public code backs it up.
              No language model scores anything.
            </p>

            {error && (
              <div className="banner banner--error" style={{ marginTop: 16, textAlign: 'left' }}>
                {error}
              </div>
            )}

            <div className="empty__actions">
              <button className="btn btn--primary" onClick={loadSample}>
                Analyse the sample corpus
              </button>
              <button className="btn" onClick={() => fileRef.current?.click()}>
                Upload JD + resumes
              </button>
              <input ref={fileRef} type="file" accept="application/pdf" multiple hidden
                     onChange={(e) => e.target.files?.length && upload(e.target.files)} />
            </div>

            <div className="empty__actions" style={{ marginTop: 14 }}>
              <Toggle checked={enrichment} onChange={setEnrichment}
                      label="Use external evidence (GitHub)"
                      title="Fetch public repositories to verify claims. Cached to disk; falls back silently when offline." />
              <Toggle checked={blind} onChange={setBlind}
                      label="Blind screening"
                      title="Hide identity. Scoring is already blind — this controls what you can see." />
            </div>

            <p className="note" style={{ marginTop: 18 }}>
              Name the job description file with “jd” or “job” so it is picked out of the batch.
            </p>
          </div>
        </div>
      </div>
    )
  }

  const meta = payload?.meta
  const job = payload?.job
  const pool = meta?.pool_integrity

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="brand__mark">IL</span> InterLoom</div>

        {job && (
          <div className="topbar__role">
            <strong>{job.title}</strong>
            <span>{meta.pool_size} candidates</span>
          </div>
        )}

        <div className="topbar__spacer" />

        <div className="topbar__stats">
          <div className="stat">
            <div className="stat__v num">{stats.spread.toFixed(1)}</div>
            <div className="stat__l">spread</div>
          </div>
          <div className="stat">
            <div className="stat__v num">{stats.max.toFixed(1)}</div>
            <div className="stat__l">top score</div>
          </div>

          <Toggle checked={blind} onChange={setBlindMode} label="Blind"
                  title="Identity never reached the engine. This only controls what you can see — the scores do not change." />

          <button className="btn btn--ghost btn--icon" onClick={toggleTheme}
                  aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
                  title="Toggle theme">
            {theme === 'dark' ? '☀' : '☾'}
          </button>

          <button className="btn" onClick={() => fileRef.current?.click()}>New batch</button>
          <input ref={fileRef} type="file" accept="application/pdf" multiple hidden
                 onChange={(e) => e.target.files?.length && upload(e.target.files)} />
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" className="tab" aria-selected={tab === id}
                  onClick={() => setTab(id)}>
            {label}
            {id === 'board' && <span className="tab__count">{candidates.length}</span>}
            {id === 'signals' && (
              <span className="tab__count">
                {candidates.filter((c) => c.flag !== 'CONSENSUS').length}
              </span>
            )}
            {id === 'audit' && job?.bias && <span className="tab__count">{job.bias.findings.length}</span>}
          </button>
        ))}
      </nav>

      <div className={`workspace ${liveSelected ? 'workspace--detail' : ''}`}>
        <aside className="col rail">
          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Matching weight</div>
            <div className="slider">
              <input type="range" min="0" max="1" step="0.01" value={alpha}
                     onChange={(e) => setAlpha(parseFloat(e.target.value))}
                     aria-label="Keyword to semantic weighting" />
              <div className="slider__ends">
                <span>semantic</span>
                <span className="mono">α {alpha.toFixed(2)}</span>
                <span>keyword</span>
              </div>
            </div>
            <p className="note" style={{ marginTop: 6 }}>
              Re-ranks in the browser — no network call.
            </p>
          </div>

          <div>
            <Toggle checked={gate} onChange={setGate} label="Must-have gate"
                    title="Scale scores by how much of the required set a candidate covers. Floored, so it re-ranks rather than eliminates." />
            <p className="note" style={{ marginTop: 6 }}>
              Scales by required coverage, floored at {meta?.gate_floor} so a strong
              near-miss stays visible.
            </p>
          </div>

          {pool && (
            <div>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Pool integrity</div>
              <div className="kv"><span className="kv__k">Unsupported claims</span>
                <span className="kv__v">{pool.stuffing_flagged}</span></div>
              <div className="kv"><span className="kv__k">Invisible text</span>
                <span className="kv__v">{pool.hidden_text_flagged}</span></div>
              <div className="kv"><span className="kv__k">Profiles found</span>
                <span className="kv__v">{pool.profiles_found}</span></div>
              <div className="kv"><span className="kv__k">Code-verified</span>
                <span className="kv__v">{pool.fully_corroborated}/{pool.checked}</span></div>
              <div className="kv"><span className="kv__k">README-only claims</span>
                <span className="kv__v">{pool.readme_only_claims}</span></div>
            </div>
          )}

          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Run</div>
            <div className="kv"><span className="kv__k">Engine</span>
              <span className="kv__v">{meta?.semantic_backend}</span></div>
            <div className="kv"><span className="kv__k">Elapsed</span>
              <span className="kv__v">{meta?.elapsed_ms} ms</span></div>
            <div className="kv"><span className="kv__k">PII removed</span>
              <span className="kv__v">{meta?.redactions}</span></div>
            <div className="kv"><span className="kv__k">Parse warnings</span>
              <span className="kv__v">{meta?.parse_warnings}</span></div>
            <div className="kv"><span className="kv__k">External evidence</span>
              <span className="kv__v">{meta?.enrichment ? 'on' : 'off'}</span></div>
          </div>
        </aside>

        <main className="col main">
          {error && <div className="banner banner--error" style={{ marginBottom: 12 }}>{error}</div>}

          {tab === 'board' && (
            <>
              {comparePair.length === 2 && (
                <DiffPanel a={comparePair[0]} b={comparePair[1]}
                           onClose={() => setCompare([])} />
              )}
              <Board candidates={candidates} selected={liveSelected}
                     onSelect={setSelected} loading={loading}
                     compare={compare} onCompare={toggleCompare} />
            </>
          )}
          {tab === 'signals' && (
            <SignalsView candidates={candidates} selected={liveSelected} onSelect={setSelected} />
          )}
          {tab === 'fusion' && <FusionInspector candidates={candidates} meta={meta} />}
          {tab === 'taxonomy' && <TaxonomyExplorer job={job} candidates={candidates} />}
          {tab === 'audit' && <JobAudit job={job} />}
          {tab === 'feedback' && (
            <FeedbackView alpha={alpha} gate={gate} shortlist={3} ready={!!payload} />
          )}
        </main>

        {/* The inspector is one candidate; the dock underneath is the whole pool.
            Both are answers about the same ranking, so they share a column —
            and the dock stays reachable whether or not a row is open. */}
        <aside className="col inspector">
          <div className="inspector__body">
            <Detail candidate={liveSelected} alpha={alpha} onClose={() => setSelected(null)} />
          </div>
          {candidates.length > 0 && (
            <ChatDock candidates={candidates} alpha={alpha} gate={gate}
                      onSelect={setSelected} />
          )}
        </aside>
      </div>
    </div>
  )
}
