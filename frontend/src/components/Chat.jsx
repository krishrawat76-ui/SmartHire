/**
 * Ask-the-shortlist dock.
 *
 * Every answer is templated from the score matrix the board is already showing,
 * so the dock can never claim something the evidence drawer would contradict —
 * and there is no language model anywhere in this path. The backend classifies
 * the question into one of a handful of intents and fills a sentence from the
 * same numbers. It also returns the doc_ids it cited, which is what lets an
 * answer open the candidate it is talking about.
 */

import { useEffect, useRef, useState } from 'react'

const INTENT_LABEL = {
  COMPARE: 'comparison',
  WHY: 'explanation',
  MISSING: 'gaps',
  WHO_HAS: 'skill lookup',
  TOP_N: 'ranking',
  UNKNOWN: 'not understood',
}

/** Questions worth asking, built from the pool that is actually loaded. */
function suggest(candidates) {
  if (candidates.length < 2) return []
  const first = candidates[0].name.split(' ')[0]
  const second = candidates[1].name.split(' ')[0]
  const gem = candidates.find((c) => c.flag === 'HIDDEN_GEM')
  const out = [
    `Why is ${first} above ${second}?`,
    `What is ${second} missing?`,
    'Who knows Docker?',
    'Show me the top 5',
  ]
  if (gem) out.splice(2, 0, `Why is ${gem.name.split(' ')[0]} ranked there?`)
  return out.slice(0, 4)
}

export default function ChatDock({ candidates, alpha, gate, onSelect }) {
  const [log, setLog] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [log])

  const send = async (text) => {
    const query = (text ?? q).trim()
    if (!query || busy) return
    setQ('')
    setLog((l) => [...l, { who: 'you', text: query }])
    setBusy(true)
    try {
      const res = await fetch(`/api/chat?alpha=${alpha}&gate=${gate}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      const data = await res.json().catch(() => ({}))
      setLog((l) => [...l, {
        who: 'engine',
        text: res.ok ? data.answer : (data.detail || `Request failed (${res.status})`),
        intent: res.ok ? data.intent : undefined,
        refs: res.ok ? (data.refs ?? []) : [],
      }])
    } catch {
      setLog((l) => [...l, { who: 'engine', text: 'Could not reach the engine.' }])
    } finally {
      setBusy(false)
    }
  }

  const suggestions = suggest(candidates)

  return (
    <div className="chat">
      <div className="chat__head">
        <div className="panel__title">Ask about this shortlist</div>
        {log.length > 0 && (
          <button className="btn btn--ghost" style={{ marginLeft: 'auto', padding: '2px 7px' }}
                  onClick={() => setLog([])}>Clear</button>
        )}
      </div>

      <div className="chat__log">
        {log.length === 0 ? (
          <>
            <p className="note" style={{ marginBottom: 8 }}>
              Answers are read straight off the score matrix — no language model
              is consulted, and every one cites the candidates it used.
            </p>
            <div className="chat__suggest">
              {suggestions.map((s) => (
                <button key={s} className="btn" onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          </>
        ) : (
          <>
            {log.map((m, i) => (
              <div key={i} className={`bubble bubble--${m.who}`}>
                {m.intent && (
                  <span className="bubble__intent">{INTENT_LABEL[m.intent] ?? m.intent.toLowerCase()}</span>
                )}
                <div className="bubble__text">{m.text}</div>
                {m.refs?.length > 0 && onSelect && (
                  <div className="bubble__refs">
                    {m.refs.map((id) => {
                      const c = candidates.find((x) => x.doc_id === id)
                      if (!c) return null
                      return (
                        <button key={id} className="chip chip--accent" onClick={() => onSelect(c)}>
                          {c.name}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="bubble bubble--engine"><div className="bubble__text note">…</div></div>}
            <div ref={endRef} />
          </>
        )}
      </div>

      <form className="chat__input" onSubmit={(e) => { e.preventDefault(); send() }}>
        <input value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="Why is X above Y?" aria-label="Ask about this shortlist" />
        <button className="btn btn--primary" type="submit" disabled={busy || !q.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </div>
  )
}
