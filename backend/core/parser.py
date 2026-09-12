"""
Resilient multi-engine PDF ingestion.

Contract: extract() NEVER raises. A resume that cannot be read becomes a
quality-0 stub that still flows through ranking and still appears in the UI,
flagged for manual review. A candidate silently vanishing from a shortlist is a
far worse failure than one shown with an honest warning.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from backend import config
from backend.core import blind, context, normalizer
from backend.core.context import ChunkContext
from backend.core.normalizer import Chunk

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf


# ─────────────────────────────────────────────────────────────────────────────
# Section vocabulary
# ─────────────────────────────────────────────────────────────────────────────

SECTION_SYNONYMS: dict[str, list[str]] = {
    "EXPERIENCE": [
        "experience", "work experience", "professional experience", "employment",
        "employment history", "work history", "professional background", "career history",
        "internship", "internships", "industry experience", "relevant experience",
    ],
    "EDUCATION": [
        "education", "academic background", "academics", "qualifications",
        "educational qualifications", "academic qualifications", "coursework",
    ],
    "SKILLS": [
        "skills", "technical skills", "core competencies", "competencies",
        "technologies", "tech stack", "areas of expertise", "proficiencies", "toolkit",
    ],
    "PROJECTS": [
        "projects", "personal projects", "academic projects", "key projects",
        "selected projects", "portfolio", "side projects",
    ],
    "SUMMARY": [
        "summary", "profile", "about", "about me", "objective", "career objective",
        "professional summary", "overview",
    ],
    "CERTIFICATIONS": [
        "certifications", "certificates", "licenses", "courses", "training",
    ],
    "ACHIEVEMENTS": [
        "achievements", "awards", "honors", "honours", "accomplishments",
        "publications", "activities", "extracurricular",
    ],
    "INTERESTS": ["interests", "hobbies", "personal interests"],
}

_HEADER_MAX_LEN = 60
_HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z /&'-]{2,58}:?$")


@dataclass(slots=True)
class HiddenSpan:
    """Text present in the PDF but not meant to be seen by a human reader."""
    text: str
    reason: str                 # invisible-colour | sub-legible-size
    detail: str
    page: int


@dataclass(slots=True)
class ParsedDoc:
    """One ingested document, ready for the engine.

    Two texts, and the difference matters. `text` is what the engine matched on
    and what every chunk span indexes — it has already had identity removed, so
    the ranking is blind by construction rather than by policy. `raw_text` keeps
    the pre-redaction form for provenance only; nothing scores against it.
    """
    doc_id: str
    name: str
    filename: str
    text: str                                       # redacted; chunk spans index this
    raw_text: str = ""                              # pre-redaction, never scored
    chunks: list[Chunk] = field(default_factory=list)
    chunk_contexts: list[ChunkContext] = field(default_factory=list)
    sections: dict[str, tuple[int, int]] = field(default_factory=dict)
    redaction: blind.RedactionReport | None = None
    hidden: list[HiddenSpan] = field(default_factory=list)
    engine: str = "none"
    pages: int = 0
    chars: int = 0
    quality: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def context_for(self, chunk_idx: int) -> ChunkContext | None:
        if 0 <= chunk_idx < len(self.chunk_contexts):
            return self.chunk_contexts[chunk_idx]
        return None

    @property
    def canonical_text(self) -> str:
        return normalizer.canonicalize(self.text)

    @property
    def tokens(self) -> list[str]:
        return normalizer.tokenize(self.canonical_text)


# ─────────────────────────────────────────────────────────────────────────────
# Extraction engines
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pymupdf(path: pathlib.Path) -> tuple[str, int]:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text("text") for page in doc), doc.page_count


def _extract_pdfplumber(path: pathlib.Path) -> tuple[str, int]:
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(pages), len(pdf.pages)


def _looks_usable(text: str) -> bool:
    return (
        len(text.strip()) >= config.MIN_CHARS_FOR_FAST_PATH
        and normalizer.alpha_ratio(text) >= config.MIN_ALPHA_RATIO
    )


# ─────────────────────────────────────────────────────────────────────────────
# Invisible text
# ─────────────────────────────────────────────────────────────────────────────

def _luma(packed: int) -> float:
    """Perceived brightness of a PyMuPDF packed sRGB colour, 0 (black) to 1 (white)."""
    r = (packed >> 16) & 0xFF
    g = (packed >> 8) & 0xFF
    b = packed & 0xFF
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def find_hidden(path: pathlib.Path) -> list[HiddenSpan]:
    """Text a human reader cannot see but a parser reads perfectly.

    The oldest ATS attack there is: paste the job description into the footer in
    white-on-white, or at one point, and let the keyword matcher find it. We read
    the span attributes rather than the flattened text, so the trick is visible to
    us exactly because it is invisible to everyone else.

    Best-effort by design — a PDF can hide text in ways this does not model (a
    white rectangle drawn over black text, a clipping path). Silence from this
    function is not a clean bill of health, and nothing downstream treats it as one.
    """
    found: list[HiddenSpan] = []
    try:
        with pymupdf.open(path) as doc:
            for page_no, page in enumerate(doc, start=1):
                data = page.get_text("dict")
                for block in data.get("blocks", []):
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = (span.get("text") or "").strip()
                            if len(text) < 12:      # too short to be a keyword dump
                                continue
                            size = float(span.get("size", 12.0))
                            luma = _luma(int(span.get("color", 0)))

                            if luma >= config.HIDDEN_TEXT_MIN_LUMA:
                                found.append(HiddenSpan(
                                    text=text, reason="invisible-colour",
                                    detail=f"rendered at {luma:.0%} brightness on a white page",
                                    page=page_no,
                                ))
                            elif size < config.HIDDEN_TEXT_MIN_SIZE:
                                found.append(HiddenSpan(
                                    text=text, reason="sub-legible-size",
                                    detail=f"rendered at {size:.1f}pt",
                                    page=page_no,
                                ))
    except Exception:                               # noqa: BLE001
        # A file we cannot introspect is not a file we get to accuse.
        return []
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Section segmentation
# ─────────────────────────────────────────────────────────────────────────────

def segment(display: str) -> dict[str, tuple[int, int]]:
    """Locate canonical sections as (start, end) spans over the display text.

    Headers are matched fuzzily, so EXPERIENCE / Work History / Employment all
    collapse to one canonical name. A resume with no detectable headers returns
    {} and the caller falls back to whole-document chunking — degraded, not broken.
    """
    hits: list[tuple[int, int, str]] = []   # (start, end_of_header_line, canonical)
    offset = 0

    for line in display.split("\n"):
        line_start = display.find(line, offset) if line else offset
        if line_start < 0:
            line_start = offset
        offset = line_start + len(line)

        probe = line.strip().rstrip(":")
        if not probe or len(probe) > _HEADER_MAX_LEN or not _HEADER_RE.match(line.strip()):
            continue

        # A header is short and is not a sentence. Uppercase is a strong signal but
        # not required, since plenty of resumes use Title Case headings.
        lowered = probe.lower()
        best_name, best_score = None, 0.0
        for canonical, synonyms in SECTION_SYNONYMS.items():
            for syn in synonyms:
                score = fuzz.ratio(lowered, syn)
                if score > best_score:
                    best_name, best_score = canonical, score

        if best_name and best_score >= config.SECTION_FUZZ_THRESHOLD:
            hits.append((line_start, offset, best_name))

    if not hits:
        return {}

    sections: dict[str, tuple[int, int]] = {}
    for idx, (start, header_end, name) in enumerate(hits):
        end = hits[idx + 1][0] if idx + 1 < len(hits) else len(display)
        # Later duplicates of a heading extend the first occurrence rather than
        # overwriting it, so "Projects" appearing twice does not lose the first block.
        if name in sections:
            sections[name] = (sections[name][0], max(sections[name][1], end))
        else:
            sections[name] = (header_end, end)
    return sections


# ─────────────────────────────────────────────────────────────────────────────
# Quality scoring
# ─────────────────────────────────────────────────────────────────────────────

def _quality(text: str, sections: dict, chunks: list[Chunk], engine: str) -> float:
    """0..1 confidence that we read this document properly. Surfaced in the UI."""
    if not text.strip():
        return 0.0

    chars = len(text.strip())
    volume = min(chars / 1200.0, 1.0)          # a full resume is ~1200-4000 chars
    structure = min(len(sections) / 4.0, 1.0)  # 4+ detected sections is healthy
    density = min(len(chunks) / 15.0, 1.0)
    legibility = normalizer.alpha_ratio(text)

    score = 0.40 * volume + 0.20 * structure + 0.20 * density + 0.20 * legibility
    if engine == "pdfplumber":
        score *= 0.95                          # needed the fallback; slightly less certain
    return round(min(max(score, 0.0), 1.0), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract(path: str | pathlib.Path, doc_id: str | None = None,
            anonymise: bool = True) -> ParsedDoc:
    """Ingest one PDF. Never raises.

    `anonymise=False` is for the job description, which must reach the bias
    detector intact — the gendered pronouns and school filters it hunts for are
    the very strings redaction removes.
    """
    path = pathlib.Path(path)
    doc_id = doc_id or path.stem
    warnings: list[str] = []
    raw, pages, engine = "", 0, "none"

    # --- Fast path ---
    try:
        raw, pages = _extract_pymupdf(path)
        engine = "pymupdf"
    except Exception as exc:                    # noqa: BLE001
        warnings.append(f"pymupdf failed: {type(exc).__name__}")

    # --- Layout-aware fallback ---
    if not _looks_usable(raw):
        if engine == "pymupdf":
            warnings.append(
                f"fast path yielded {len(raw.strip())} chars "
                f"(alpha ratio {normalizer.alpha_ratio(raw):.2f}); retrying with pdfplumber"
            )
        try:
            alt, alt_pages = _extract_pdfplumber(path)
            if len(alt.strip()) > len(raw.strip()):
                raw, pages, engine = alt, alt_pages, "pdfplumber"
        except Exception as exc:                # noqa: BLE001
            warnings.append(f"pdfplumber failed: {type(exc).__name__}")

    display = normalizer.repair_text(raw)

    if not display.strip():
        warnings.append("no extractable text — likely a scanned image. Manual review required.")
        return ParsedDoc(
            doc_id=doc_id, name=_infer_name(doc_id, ""), filename=path.name,
            text="", engine=engine, pages=pages, chars=0, quality=0.0, warnings=warnings,
        )

    name = _infer_name(doc_id, display)

    # --- Blind screening, before anything is matched -------------------------
    # Sections are found twice on purpose: once on the original so the
    # EDUCATION-only rules know where they are, then again on the redacted text
    # because the placeholders shift every offset after them. Chunk spans must
    # index the text the engine actually reads, so that second pass is the real one.
    hidden = find_hidden(path)
    if anonymise and config.REDACT_BEFORE_SCORING:
        redaction = blind.redact(display, name=name, sections=segment(display))
        engine_text = redaction.text
    else:
        redaction = blind.RedactionReport(text=display)
        engine_text = display

    sections = segment(engine_text)
    if not sections:
        warnings.append("no section headers detected; using whole-document chunking")

    chunks = normalizer.chunk_text(engine_text, sections)
    if not chunks:
        warnings.append("no chunks above the minimum length; document may be near-empty")

    contexts = context.annotate(chunks, engine_text) if config.CONTEXT_WEIGHTING_ENABLED else []

    quality = _quality(engine_text, sections, chunks, engine)
    if quality < 0.5:
        warnings.append(f"low parse quality ({quality:.2f})")

    if hidden:
        warnings.append(
            f"{len(hidden)} invisible text {'span' if len(hidden) == 1 else 'spans'} "
            f"found in the PDF — possible ATS manipulation"
        )

    return ParsedDoc(
        doc_id=doc_id,
        name=name,
        filename=path.name,
        text=engine_text,
        raw_text=display,
        chunks=chunks,
        chunk_contexts=contexts,
        sections=sections,
        redaction=redaction,
        hidden=hidden,
        engine=engine,
        pages=pages,
        chars=len(engine_text),
        quality=quality,
        warnings=warnings,
    )


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_NAME_LINE_RE = re.compile(r"^[A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+){1,3}$")


def _infer_name(doc_id: str, display: str) -> str:
    """Best-effort candidate name: the first line that looks like a person's name."""
    for line in display.split("\n")[:6]:
        probe = line.strip()
        if _NAME_LINE_RE.match(probe) and not _EMAIL_RE.search(probe):
            lowered = probe.lower()
            if not any(w in lowered for w in ("resume", "curriculum", "vitae", "profile")):
                return probe

    # Fall back to the filename: "c01_aarav_mehta" -> "Aarav Mehta"
    stem = re.sub(r"^c?\d+[_-]", "", doc_id)
    return re.sub(r"[_-]+", " ", stem).title() or doc_id


def extract_many(paths: list[str | pathlib.Path]) -> list[ParsedDoc]:
    """Ingest a batch. One bad file never takes down the rest."""
    return [extract(p, doc_id=pathlib.Path(p).stem) for p in sorted(paths, key=str)]
