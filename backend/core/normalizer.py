"""
Text repair, alias canonicalisation and chunking.

Design note on evidence spans
-----------------------------
The UI highlights the exact resume sentence behind every matched skill, so spans
must stay valid. We avoid fragile offset remapping by keeping two parallel forms:

    display   - repaired but still human-readable. Spans point into THIS.
    canonical - lowercased and alias-substituted. Matching happens against THIS.

Both derive from the same chunk, so a match on the canonical form always has a
correct span into the display form. No offset arithmetic, nothing to drift.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from backend import config

# ─────────────────────────────────────────────────────────────────────────────
# Text repair
# ─────────────────────────────────────────────────────────────────────────────

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st", "İ": "I",
}

# Bullet glyphs and the assorted dashes PDFs love to emit.
_BULLETS = "•▪◦‣∙·●○■□–—‒―*"
_BULLET_RE = re.compile(rf"^[\s{re.escape(_BULLETS)}]+")
_HYPHEN_WRAP_RE = re.compile(r"(\w)-\s*\n\s*(\w)")
_MULTI_SPACE_RE = re.compile(r"[ \t ]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def repair_text(raw: str) -> str:
    """Produce clean, human-readable display text. Spans point into the result."""
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)
    for lig, repl in _LIGATURES.items():
        text = text.replace(lig, repl)
    text = _CONTROL_RE.sub(" ", text)

    # Rejoin words broken across a line wrap: "develop-\nment" -> "development".
    # Done before newline collapsing, while the break is still visible.
    text = _HYPHEN_WRAP_RE.sub(r"\1\2", text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = []
    for line in text.split("\n"):
        line = _BULLET_RE.sub("", line)
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        lines.append(line)

    text = "\n".join(lines)
    return _MULTI_NEWLINE_RE.sub("\n\n", text).strip()


def alpha_ratio(text: str) -> float:
    """Fraction of non-space characters that are alphanumeric.

    Garbled extraction (wrong font encoding, embedded binary) produces a low ratio,
    which is how we decide the fast path failed even when it returned plenty of bytes.
    """
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return 0.0
    return sum(c.isalnum() for c in stripped) / len(stripped)


# ─────────────────────────────────────────────────────────────────────────────
# Alias canonicalisation
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _alias_pattern() -> tuple[re.Pattern[str], dict[str, str]]:
    raw = json.loads(config.ALIAS_PATH.read_text(encoding="utf-8"))
    aliases = {k.lower(): v for k, v in raw.items() if not k.startswith("_")}

    # Longest first, so "node js" wins over "node" and "rest apis" over "rest api".
    keys = sorted(aliases, key=len, reverse=True)
    # Custom boundaries rather than \b: many keys end in "." or "#", where \b
    # behaves in ways that silently drop matches.
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in keys) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern, aliases


def canonicalize(text: str) -> str:
    """Lowercase and fold every known surface form onto one canonical spelling."""
    if not text:
        return ""
    pattern, aliases = _alias_pattern()
    lowered = text.lower()
    return pattern.sub(lambda m: aliases[m.group(1).lower()], lowered)


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
_STOPWORDS = frozenset("""
a an the and or but if then than of in on at to for with from by as is are was were be been
being do does did doing have has had having i me my we our you your he she it they them their
this that these those will would shall should can could may might must not no nor so such very
s t don just now also using used use work worked working experience
""".split())


def tokenize(text: str) -> list[str]:
    """Token stream for BM25. Expects already-canonicalised text."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t.strip(".-") for t in tokens if t not in _STOPWORDS and len(t.strip(".-")) > 1]


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Chunk:
    """One embeddable unit of a resume — normally a bullet or a sentence."""
    text: str                 # display form; substring of the document's display text
    canonical: str            # matching form
    start: int                # char offset into display text
    end: int
    section: str = "UNKNOWN"
    tokens: list[str] = field(default_factory=list)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def chunk_text(display: str, sections: dict[str, tuple[int, int]] | None = None) -> list[Chunk]:
    """Split display text into chunks carrying valid spans.

    Lines are the primary unit because resumes are line-oriented; long lines are
    further split on sentence boundaries so a run-on paragraph (the messy-format
    case) still yields usable granularity.
    """
    chunks: list[Chunk] = []
    offset = 0

    for line in display.split("\n"):
        line_start = display.find(line, offset) if line else offset
        if line_start < 0:
            line_start = offset
        offset = line_start + len(line)

        stripped = line.strip()
        if len(stripped) < config.MIN_CHUNK_CHARS:
            continue

        pieces = _SENTENCE_SPLIT_RE.split(stripped) if len(stripped) > 180 else [stripped]

        piece_cursor = line_start
        for piece in pieces:
            piece = piece.strip()
            if len(piece) < config.MIN_CHUNK_CHARS:
                continue
            found = display.find(piece, piece_cursor)
            if found < 0:
                found = piece_cursor
            start, end = found, found + len(piece)
            piece_cursor = end

            canon = canonicalize(piece)
            chunks.append(Chunk(
                text=piece,
                canonical=canon,
                start=start,
                end=end,
                section=_section_for(start, sections) if sections else "UNKNOWN",
                tokens=tokenize(canon),
            ))

    return chunks


def _section_for(pos: int, sections: dict[str, tuple[int, int]]) -> str:
    for name, (start, end) in sections.items():
        if start <= pos < end:
            return name
    return "UNKNOWN"
