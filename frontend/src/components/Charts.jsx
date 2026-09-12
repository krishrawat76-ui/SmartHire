/**
 * Hand-drawn SVG charts.
 *
 * No charting library. These are a handful of shapes, and writing them by hand
 * is what lets every colour come from the same tokens as the rest of the app —
 * a library would ship its own palette and fight index.css for the legend.
 */

/* ── Skill-gap map ─────────────────────────────────────────────────────────
   Score on x, unmet required-skill density on y. The interesting candidates
   are not the ones in a corner but the ones off the diagonal: a high score
   carrying a real gap, or a low score with nothing actually missing. */

const FLAG_FILL = {
  HIDDEN_GEM: 'var(--inferred)',
  SURFACE_MATCH: 'var(--weak)',
  CONSENSUS: 'var(--ink-4)',
}

export function GapScatter({ candidates, selectedId, onSelect, width = 340, height = 230 }) {
  const pad = { t: 14, r: 16, b: 32, l: 40 }
  const w = width - pad.l - pad.r
  const h = height - pad.t - pad.b

  const maxScore = Math.max(100, ...candidates.map((c) => c.score))
  const x = (s) => pad.l + (s / maxScore) * w
  const y = (g) => pad.t + Math.min(Math.max(g, 0), 1) * h

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label="Match score against required-skill gap density">
      {/* Quadrant guides: a healthy match on one axis, half the required set
          missing on the other. */}
      <line className="chart__grid" x1={x(maxScore * 0.6)} y1={pad.t}
            x2={x(maxScore * 0.6)} y2={pad.t + h} strokeDasharray="3 4" />
      <line className="chart__grid" x1={pad.l} y1={y(0.5)} x2={pad.l + w} y2={y(0.5)}
            strokeDasharray="3 4" />

      <line className="chart__axis" x1={pad.l} y1={pad.t + h} x2={pad.l + w} y2={pad.t + h} />
      <line className="chart__axis" x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + h} />

      <text className="chart__label" x={pad.l + w} y={height - 9} textAnchor="end">
        match score →
      </text>
      <text className="chart__label" x={pad.l - 7} y={pad.t + 4} textAnchor="end">100%</text>
      <text className="chart__label" x={pad.l - 7} y={pad.t + h} textAnchor="end">0%</text>
      <text className="chart__label" x={11} y={pad.t + h / 2} textAnchor="middle"
            transform={`rotate(-90 11 ${pad.t + h / 2})`}>
        required missing
      </text>

      {candidates.map((c) => {
        const sel = c.doc_id === selectedId
        // Radius carries a third dimension: how much text the resume gave us.
        const evidence = Math.min((c.primitives.n_chunks ?? 0) / 16, 1)
        return (
          <circle
            key={c.doc_id}
            className="chart__pt"
            cx={x(c.score)}
            cy={y(c.primitives.gap_density)}
            r={sel ? 7 : 3.5 + evidence * 3}
            fill={FLAG_FILL[c.flag] ?? 'var(--ink-4)'}
            fillOpacity={sel ? 1 : 0.6}
            stroke={sel ? 'var(--ink)' : 'none'}
            strokeWidth="1.5"
            onClick={() => onSelect?.(c)}
          >
            <title>
              {`${c.name} — ${c.score.toFixed(1)} points, ` +
               `${Math.round(c.primitives.gap_density * 100)}% of the required set missing`}
            </title>
          </circle>
        )
      })}
    </svg>
  )
}

/* ── Channel contribution bar ──────────────────────────────────────────────
   How much of THIS candidate's points came from each channel at the current
   alpha. Reads differently from the slider: the slider sets the weight, this
   shows what that weight bought. */

export function ContribBar({ candidate, alpha }) {
  const k = alpha * candidate.k_score
  const m = (1 - alpha) * candidate.m_score
  const total = k + m || 1
  const kPct = (k / total) * 100

  return (
    <div className="contrib" role="img"
         aria-label={`Keyword contributes ${kPct.toFixed(0)} percent, semantic ${(100 - kPct).toFixed(0)} percent`}>
      <i className="contrib__k" style={{ flexGrow: Math.max(k, 0.001) }}>
        {kPct > 24 ? `KEYWORD ${kPct.toFixed(0)}%` : ''}
      </i>
      <i className="contrib__m" style={{ flexGrow: Math.max(m, 0.001) }}>
        {100 - kPct > 24 ? `SEMANTIC ${(100 - kPct).toFixed(0)}%` : ''}
      </i>
    </div>
  )
}

/* ── Coverage radar, one axis per gazetteer cluster ────────────────────────
   The board's evidence bar says how much is covered; this says where. Two
   candidates on the same score can have opposite shapes, and the shape is the
   thing a hiring manager actually argues about. */

export function ScoreRadar({ candidate, size = 250 }) {
  const byCluster = new Map()
  for (const cell of candidate.primitives.cells) {
    const c = byCluster.get(cell.cluster) ?? { w: 0, cov: 0 }
    c.w += cell.weight
    c.cov += cell.weight * cell.coverage
    byCluster.set(cell.cluster, c)
  }
  const data = [...byCluster.entries()]
    .map(([label, v]) => ({ label, value: v.w ? v.cov / v.w : 0 }))
    .sort((x, y) => x.label.localeCompare(y.label))

  // Fewer than three axes is a line, not a shape — nothing to read.
  if (data.length < 3) return null

  const cx = size / 2
  const cy = size / 2
  // Leave room for the axis labels: they sit outside the outer ring.
  const r = size / 2 - 56
  const step = (Math.PI * 2) / data.length

  const pt = (i, frac) => {
    const a = i * step - Math.PI / 2
    return [cx + Math.cos(a) * r * frac, cy + Math.sin(a) * r * frac]
  }

  return (
    <svg className="chart chart--radar" viewBox={`0 0 ${size} ${size}`} role="img"
         aria-label="Skill coverage by area">
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} className="chart__grid" fill="none"
                 points={data.map((_, i) => pt(i, f).join(',')).join(' ')} />
      ))}
      {data.map((_, i) => {
        const [x, y] = pt(i, 1)
        return <line key={i} className="chart__grid" x1={cx} y1={cy} x2={x} y2={y} />
      })}

      <polygon points={data.map((d, i) => pt(i, Math.max(d.value, 0.02)).join(',')).join(' ')}
               fill="var(--inferred)" fillOpacity="0.17"
               stroke="var(--inferred)" strokeWidth="1.5" strokeLinejoin="round" />
      {data.map((d, i) => {
        const [x, y] = pt(i, Math.max(d.value, 0.02))
        return <circle key={d.label} cx={x} cy={y} r="2.5" fill="var(--inferred)" />
      })}

      {data.map((d, i) => {
        const [x, y] = pt(i, 1.16)
        return (
          <text key={d.label} className="chart__label" x={x} y={y}
                textAnchor="middle" dominantBaseline="middle">
            {d.label}
          </text>
        )
      })}
    </svg>
  )
}
