/**
 * The inspector: everything known about one candidate, in five views.
 *
 *   Evidence   the matrix, per skill, with the sentence that produced each cell
 *              and the discount that was applied to it
 *   Ramp-up    what the gaps would cost in weeks, and from which springboard
 *   Interview  questions aimed at exactly those gaps and claims
 *   Integrity  unsupported claims, invisible text, resume-versus-code
 *   Code       the repositories, and which skills they prove
 *
 * The rule this file follows: a number that represents a judgement is never
 * shown without the reason beside it. That is why every discounted cell expands
 * to its own explanation rather than just rendering a smaller bar.
 */

import { useState } from 'react'
import { ContribBar, ScoreRadar } from './Charts'
import { STATUS_HELP, STATUS_LABEL, cls, num, splitAround } from '../lib/ui'

/* ── Ontological path ──────────────────────────────────────────────────── */

export function Path({ nodes }) {
  if (!nodes?.length) return null
  return (
    <div className="path">
      {nodes.map((n, i) => (
        <span key={`${n}-${i}`} style={{ display: 'contents' }}>
          {i > 0 && <span className="path__arrow">→</span>}
          <span className={`path__node ${i === 0 ? 'path__node--leaf' : ''}`}>{n}</span>
        </span>
      ))}
    </div>
  )
}

/* ── Evidence matrix ───────────────────────────────────────────────────── */

function Cell({ cell, resumeText }) {
  const [open, setOpen] = useState(false)
  const discounted = cell.lex > 0 && cell.lex_eff < cell.lex - 1e-9
  const boosted = cell.lex_eff > cell.lex + 1e-9
  const parts = splitAround(resumeText, cell.start, cell.end)

  return (
    <>
      <button className="mrow" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <div className="mrow__label">
          <span className={`dot dot--${cls(cell.status)}`} title={STATUS_HELP[cell.status]} />
          <span className="mrow__name">{cell.label}</span>
          {cell.tier === 'REQUIRED' && <span className="mrow__req">REQ</span>}
          {discounted && <span className="chip chip--weak">discounted</span>}
          {boosted && <span className="chip chip--matched">code-backed</span>}
        </div>
        <div className={`mrow__val ${discounted ? 'mrow__val--strike' : ''}`}>
          {cell.lex > 0 ? num(cell.lex) : '—'}
        </div>
        <div className="mrow__val">{num(cell.coverage)}</div>
      </button>

      {open && (
        <div className="mdetail">
          <div className="kv"><span className="kv__k">Status</span>
            <span className="kv__v">{STATUS_LABEL[cell.status]}</span></div>
          <div className="kv"><span className="kv__k">Keyword evidence</span>
            <span className="kv__v">{num(cell.lex)} ({cell.lex_kind})</span></div>
          <div className="kv"><span className="kv__k">Semantic (raw → calibrated)</span>
            <span className="kv__v">{num(cell.sem_raw)} → {num(cell.sem_cal)}</span></div>
          {cell.lex > 0 && (
            <div className="kv"><span className="kv__k">Counted as</span>
              <span className="kv__v">{num(cell.lex_eff)}</span></div>
          )}

          {(discounted || boosted) && (
            <div style={{ marginTop: 8 }}>
              {cell.context_reason && <p className="note">{cell.context_reason}</p>}
              {cell.notes?.map((n, i) => <p key={i} className="note">{n}</p>)}
              <div className="note" style={{ marginTop: 4 }}>
                Placement ×{num(cell.context_weight)}
                {cell.integrity_mult !== 1 && ` · support ×${num(cell.integrity_mult)}`}
                {cell.external_mult !== 1 && ` · code ×${num(cell.external_mult)}`}
              </div>
            </div>
          )}

          {cell.external_evidence?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Proven by</div>
              {cell.external_evidence.map((e, i) => <p key={i} className="note">{e}</p>)}
            </div>
          )}

          {cell.evidence && (
            <div style={{ marginTop: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}>
                From the resume{cell.section !== 'UNKNOWN' ? ` · ${cell.section.toLowerCase()}` : ''}
              </div>
              <blockquote className="quote">
                {parts ? <>{parts[0].slice(-90)}<mark>{parts[1]}</mark>{parts[2].slice(0, 90)}</>
                       : cell.evidence}
              </blockquote>
            </div>
          )}

          {cell.path?.length > 1 && (
            <div style={{ marginTop: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Where this sits</div>
              <Path nodes={cell.path} />
            </div>
          )}
        </div>
      )}
    </>
  )
}

function EvidenceView({ candidate, alpha }) {
  const cells = [...candidate.primitives.cells].sort(
    (a, b) => (a.tier !== 'REQUIRED') - (b.tier !== 'REQUIRED')
      || b.coverage - a.coverage
      || a.label.localeCompare(b.label),
  )

  return (
    <>
      {candidate.explanation && (
        <div className="panel__body">
          <p style={{ color: 'var(--ink)', fontSize: 12.5, lineHeight: 1.55 }}>
            {candidate.explanation.headline}
          </p>
          {candidate.explanation.bullets?.map((b, i) => (
            <p key={i} className="note" style={{ marginTop: 6 }}>{b}</p>
          ))}
        </div>
      )}

      <div className="panel__body" style={{ paddingTop: 0 }}>
        <div className="eyebrow" style={{ marginBottom: 5 }}>
          Where the points came from at α {alpha.toFixed(2)}
        </div>
        <ContribBar candidate={candidate} alpha={alpha} />
        <p className="note" style={{ marginTop: 5 }}>
          The slider sets the weight; this is what that weight bought. Drag it and
          the split moves without a network call.
        </p>

        <div className="eyebrow" style={{ margin: '14px 0 2px' }}>Coverage by area</div>
        <ScoreRadar candidate={candidate} />
      </div>

      <div className="mrow" style={{ borderTop: '1px solid var(--line)', cursor: 'default' }}>
        <div className="eyebrow">Skill</div>
        <div className="eyebrow" style={{ textAlign: 'right' }}>Said</div>
        <div className="eyebrow" style={{ textAlign: 'right' }}>Counted</div>
      </div>

      <div className="matrix">
        {cells.map((c) => (
          <Cell key={c.skill_id} cell={c} resumeText={candidate.resume_text} />
        ))}
      </div>
    </>
  )
}

/* ── Ramp-up ───────────────────────────────────────────────────────────── */

function RampView({ candidate }) {
  const ramp = candidate.ramp_up
  if (!ramp) {
    return <div className="panel__body"><p className="note">
      Ramp-up is estimated for the shortlist only — open one of the top three.
    </p></div>
  }

  return (
    <div className="panel__body">
      <div className="banner banner--info" style={{ marginBottom: 12 }}>
        <div>
          <strong>{ramp.total_human}</strong> to bridge every gap.
          <div style={{ marginTop: 4, color: 'var(--ink-2)' }}>{ramp.headline}</div>
        </div>
      </div>

      {ramp.gaps.map((g) => (
        <div key={g.skill_id} className="gap">
          <div className="gap__head">
            <span className="gap__name">{g.label}</span>
            <span className={`chip chip--${cls(g.status)}`}>{STATUS_LABEL[g.status]}</span>
            {g.tier === 'REQUIRED' && <span className="chip">required</span>}
            <span className="gap__time">{g.human}</span>
          </div>
          <p className="gap__note">
            {g.springboard && g.springboard !== 'no adjacent experience'
              ? <>Builds on <strong style={{ color: 'var(--ink-2)' }}>{g.springboard}</strong> ({g.springboard_held}).</>
              : <>No adjacent experience on this resume.</>}
            {' '}{g.note}
            {g.pinned && <span className="chip chip--mono" style={{ marginLeft: 6 }}>measured</span>}
          </p>
          <div style={{ marginTop: 6 }}><Path nodes={g.path} /></div>
        </div>
      ))}
    </div>
  )
}

/* ── Interview ─────────────────────────────────────────────────────────── */

function InterviewView({ candidate }) {
  const guide = candidate.interview
  if (!guide || !guide.questions.length) {
    return <div className="panel__body"><p className="note">
      {guide?.headline || 'Interview questions are generated for the shortlist only.'}
    </p></div>
  }

  return (
    <div className="panel__body">
      <p className="note" style={{ marginBottom: 12 }}>{guide.headline}</p>
      {guide.questions.map((q, i) => (
        <div key={i} className="qcard">
          <div className="qcard__kind">
            {q.kind === 'GAP' && 'Probes a gap'}
            {q.kind === 'CLAIM' && 'Tests an unsupported claim'}
            {q.kind === 'INFERRED' && 'Confirms what they never named'}
            {q.kind === 'PROJECT' && 'Grounded in their code'}
          </div>
          <div className="qcard__q">{q.question}</div>
          <div className="qcard__why">{q.why}</div>
          {q.evidence && (
            <blockquote className="quote" style={{ marginTop: 8 }}>{q.evidence}</blockquote>
          )}
        </div>
      ))}
    </div>
  )
}

/* ── Integrity ─────────────────────────────────────────────────────────── */

function IntegrityView({ candidate }) {
  const integ = candidate.integrity
  const ver = candidate.verification
  const red = candidate.redaction

  return (
    <div className="panel__body">
      {integ && (
        <>
          <div className={`banner banner--${integ.hidden_flag ? 'error'
            : integ.stuffing_flag ? 'warn'
            : integ.orphans.length ? 'info' : 'ok'}`}>
            <div>
              <strong>{integ.headline}</strong>
              {integ.detail.map((d, i) => (
                <div key={i} style={{ marginTop: 4, color: 'var(--ink-2)' }}>{d}</div>
              ))}
            </div>
          </div>

          {integ.hidden_samples.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Invisible text found</div>
              {integ.hidden_samples.map((s, i) => (
                <blockquote key={i} className="quote" style={{ marginTop: 4 }}>{s}</blockquote>
              ))}
            </div>
          )}
        </>
      )}

      {ver && (
        <div style={{ marginTop: 14 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Resume against code</div>
          <p style={{ color: 'var(--ink)', fontSize: 12.5 }}>{ver.headline}</p>
          {ver.detail.map((d, i) => <p key={i} className="note" style={{ marginTop: 5 }}>{d}</p>)}

          {ver.verdicts.length > 0 && (
            <div className="tablewrap" style={{ marginTop: 10 }}>
              <table className="table">
                <thead><tr><th>Claim</th><th>Verdict</th></tr></thead>
                <tbody>
                  {ver.verdicts.map((v) => (
                    <tr key={v.skill_id}>
                      <td className="table__name">{v.label}</td>
                      <td>
                        <span className={`chip ${v.verdict === 'CORROBORATED' ? 'chip--matched'
                          : v.verdict === 'CONTRADICTED' ? 'chip--missing' : ''}`}
                              title={v.reason}>
                          {v.verdict.toLowerCase()}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {red && (
        <div style={{ marginTop: 14 }}>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Blind screening</div>
          <p className="note">{red.summary}</p>
          <p className="note">
            Identity is removed before the engine reads the document, so it is
            removed whether or not blind mode is switched on. The toggle only
            controls what you can see.
          </p>
        </div>
      )}
    </div>
  )
}

/* ── Code ──────────────────────────────────────────────────────────────── */

function ProfileBlock({ profile, title, note }) {
  const ok = profile?.status === 'ok'
  return (
    <div style={{ marginBottom: 16 }}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>
        {title}
        <span style={{ marginLeft: 6, textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>
          weight ×{profile?.weight?.toFixed(2) ?? '0.00'}
        </span>
      </div>
      {profile?.handle && (
        <p className="note">
          {profile.url ? <a href={profile.url} target="_blank" rel="noreferrer">{profile.handle}</a>
                       : profile.handle}
        </p>
      )}
      <p className="note">{profile?.message || 'Not requested.'}</p>
      {note && <p className="note" style={{ marginTop: 4 }}>{note}</p>}

      {ok && profile.repos?.length > 0 && (
        <div className="tablewrap" style={{ marginTop: 8 }}>
          <table className="table">
            <thead><tr><th>Repository</th><th>Proves</th><th>README only</th></tr></thead>
            <tbody>
              {profile.repos.map((r) => (
                <tr key={r.name}>
                  <td className="table__name">
                    {r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.name}</a> : r.name}
                    <div className="note" style={{ fontSize: 11 }}>{r.description}</div>
                  </td>
                  <td>{r.code_skills.slice(0, 5).join(', ') || '—'}</td>
                  <td>
                    {r.readme_only.length
                      ? <span className="chip chip--weak">{r.readme_only.join(', ')}</span>
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {ok && profile.skills?.length > 0 && (
        <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {profile.skills.map((s) => <span key={s} className="chip chip--matched">{s}</span>)}
        </div>
      )}
    </div>
  )
}

function CodeView({ candidate }) {
  const ext = candidate.external
  if (!ext) {
    return <div className="panel__body"><p className="note">
      External evidence was not requested for this run. Turn on “Use external
      evidence” and re-analyse to fetch public repositories.
    </p></div>
  }

  return (
    <div className="panel__body">
      <ProfileBlock
        profile={ext.github} title="GitHub — code"
        note="Dependency manifests, language stats and config files. The strongest evidence available: this code was executed, not typed into a CV."
      />
      <ProfileBlock
        profile={ext.linkedin} title="LinkedIn — self-reported"
        note="Self-reported and unverifiable, so it may support a claim the resume already makes but never create one."
      />
    </div>
  )
}

/* ── Shell ─────────────────────────────────────────────────────────────── */

const VIEWS = [
  ['evidence', 'Evidence'],
  ['ramp', 'Ramp-up'],
  ['interview', 'Interview'],
  ['integrity', 'Integrity'],
  ['code', 'Code'],
]

export default function Detail({ candidate, alpha, onClose }) {
  const [view, setView] = useState('evidence')

  if (!candidate) {
    return (
      <div className="panel__body">
        <p className="note">Select a candidate to see the evidence behind their score.</p>
      </div>
    )
  }

  return (
    <div>
      <div className="panel__head" style={{ position: 'sticky', top: 0, zIndex: 2 }}>
        <div style={{ minWidth: 0 }}>
          <div className="panel__title">{candidate.name}</div>
          <div className="note" style={{ fontSize: 11 }}>
            #{candidate.rank} · {candidate.score.toFixed(1)} points
            {candidate.primitives.doc_multiplier < 1 &&
              ` · penalised ×${candidate.primitives.doc_multiplier.toFixed(2)}`}
          </div>
        </div>
        <button className="btn btn--ghost btn--icon" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="subtabs" role="tablist">
        {VIEWS.map(([id, label]) => (
          <button key={id} role="tab" className="subtab"
                  aria-selected={view === id} onClick={() => setView(id)}>
            {label}
          </button>
        ))}
      </div>

      {view === 'evidence' && <EvidenceView candidate={candidate} alpha={alpha} />}
      {view === 'ramp' && <RampView candidate={candidate} />}
      {view === 'interview' && <InterviewView candidate={candidate} />}
      {view === 'integrity' && <IntegrityView candidate={candidate} />}
      {view === 'code' && <CodeView candidate={candidate} />}
    </div>
  )
}
