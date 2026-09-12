/**
 * Small shared helpers: theme, formatting, and the status vocabulary.
 *
 * The four evidence states are defined once, here, and every component reads
 * their label and colour class from this file. That is what stops the radar,
 * the rows, the matrix and the diff from slowly disagreeing about what amber
 * means.
 */

import { useCallback, useEffect, useState } from 'react'

/* ── Status vocabulary ─────────────────────────────────────────────────── */

export const STATUSES = ['MATCHED', 'INFERRED', 'WEAK', 'MISSING']

export const STATUS_LABEL = {
  MATCHED: 'Stated',
  INFERRED: 'Demonstrated',
  WEAK: 'Adjacent',
  MISSING: 'Absent',
}

export const STATUS_HELP = {
  MATCHED: 'The resume names this skill outright.',
  INFERRED: 'Never named, but the work described demonstrates it.',
  WEAK: 'Only adjacent experience — related, not the thing itself.',
  MISSING: 'No evidence of this skill in either channel.',
}

export const cls = (status) => status.toLowerCase()

export const VERDICT_TONE = {
  CORROBORATED: 'matched',
  CONTRADICTED: 'missing',
  UNVERIFIABLE: '',
  UNVERIFIED: '',
}

/* ── Formatting ────────────────────────────────────────────────────────── */

export const pct = (x) => `${Math.round((x ?? 0) * 100)}%`
export const num = (x, places = 2) => (x ?? 0).toFixed(places)

/** "+3" / "-2" / "—", with the class that colours it. */
export function delta(value) {
  if (!value) return { text: '—', cls: 'delta-flat' }
  return value > 0
    ? { text: `+${value}`, cls: 'delta-up' }
    : { text: `${value}`, cls: 'delta-down' }
}

/** Counts of each evidence status across a candidate's cells. */
export function statusCounts(candidate) {
  const out = { MATCHED: 0, INFERRED: 0, WEAK: 0, MISSING: 0 }
  for (const cell of candidate?.primitives?.cells ?? []) {
    if (out[cell.status] !== undefined) out[cell.status] += 1
  }
  return out
}

/* ── Theme ─────────────────────────────────────────────────────────────── */

const THEME_KEY = 'interloom:theme'

/**
 * Light/dark, persisted, following the OS until the user chooses.
 *
 * The attribute goes on <html> rather than a React root, because the token
 * blocks in index.css are keyed off :root — and because painting the document
 * element is what stops a flash of the wrong theme behind the app.
 */
export function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      const stored = localStorage.getItem(THEME_KEY)
      if (stored === 'light' || stored === 'dark') return stored
    } catch {
      /* private mode, or site data blocked — fall through to the OS */
    }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      /* nothing to do: the theme still applies for this session */
    }
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  }, [])

  return [theme, toggle]
}

/* ── Evidence spans ────────────────────────────────────────────────────── */

/**
 * Split resume text around one evidence span so the matched sentence can be
 * highlighted. Returns null when the span is absent or out of range, which
 * happens for cells whose evidence came from nowhere quotable.
 */
export function splitAround(text, start, end) {
  if (!text || start < 0 || end <= start || end > text.length) return null
  return [text.slice(0, start), text.slice(start, end), text.slice(end)]
}
