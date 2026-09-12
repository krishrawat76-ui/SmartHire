"""
Blind screening: take identity out of the matching entirely.

The important design decision here is that redaction is NOT a view toggle bolted
on at the end. PII carries no skill signal, so removing it before the engine ever
sees the text costs nothing and buys a claim worth making:

    the ranking is blind by construction, not blind by policy.

`redact()` runs at parse time, always. The UI toggle controls only whether the
recruiter is shown the identity we already declined to score on — so switching it
cannot move a single candidate, and the screenshot of the scores is the same
screenshot either way. A toggle that re-ranks would prove the opposite of what
it is trying to prove.

What goes: names, emails, phones, postal addresses, photos (they never reach the
text layer anyway), university and school names, graduation years, nationality
and gender markers, and personal URLs that identify rather than demonstrate.

What deliberately stays: GitHub and portfolio links. They are evidence about
work, not about a person, and the enrichment channel needs them. They are
redacted from the DISPLAY when blind mode is on, but never from the text used for
matching, because the code behind them is exactly the signal we want.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# Patterns
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
PHONE_RE = re.compile(
    r"(?<![\w])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,5}[\s.-]?\d{3,5}(?![\w])"
)
# Personal social identity — LinkedIn is identity, GitHub is portfolio.
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+/?", re.IGNORECASE)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w.-]+(?:/[\w.-]+)?/?", re.IGNORECASE)

GRAD_YEAR_RE = re.compile(
    r"\b(?:class of|batch of|graduat\w*(?:\s+in)?|expected(?:\s+graduation)?)\s*:?\s*"
    r"(19[89]\d|20[0-4]\d)\b",
    re.IGNORECASE,
)
# A bare span like "2022 - 2026". Only ever applied inside EDUCATION: the same
# shape in EXPERIENCE is an employment date, and the recency model is built on
# those. Stripping them globally would trade one fairness gain for a worse one.
YEAR_SPAN_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\s*[-–—to]+\s*((?:19[89]\d|20[0-4]\d)|present)\b",
                          re.IGNORECASE)

# Institutions. Matched on the shape of an institution name rather than a list of
# them, so it generalises past whichever colleges happen to be in one corpus.
INSTITUTION_RE = re.compile(
    r"\b(?:[A-Z][\w&.'-]*\s+){0,4}"
    r"(?:University|Universität|Institute of Technology|Institute|College|Polytechnic|"
    r"School of Engineering|Academy|Vidyalaya|Vidyapeeth)"
    r"(?:\s+(?:of|at|for)\s+(?:[A-Z][\w&.'-]*\s*){1,3})?",
    re.IGNORECASE,
)
# Well-known abbreviations the shape rule cannot see.
INSTITUTION_ABBR_RE = re.compile(
    r"\b(?:IIT|NIT|BITS|IIIT|VIT|SRM|MIT|BMS|RVCE|PES|MSRIT|DTU|NSUT|VJTI|COEP)"
    r"(?:[\s-]+[A-Z][a-z]+)?\b"
)

# Demographic markers.
GENDER_RE = re.compile(
    r"\b(?:male|female|non[- ]binary|transgender|he/him|she/her|they/them|"
    r"mr\.?|mrs\.?|ms\.?|miss)\b",
    re.IGNORECASE,
)
PERSONAL_RE = re.compile(
    r"\b(?:date of birth|d\.?o\.?b\.?|nationality|marital status|religion|caste|"
    r"gender|sex|age)\s*:?\s*[^\n]{0,40}",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,4}[\w\s.,-]{4,40}\b(?:street|road|rd\.?|lane|ln\.?|avenue|ave\.?|nagar|"
    r"colony|sector|block|apartment|flat|house)\b[^\n]{0,30}",
    re.IGNORECASE,
)
PINCODE_RE = re.compile(r"\b\d{6}\b")

CATEGORIES: list[tuple[str, re.Pattern[str], str]] = [
    ("email",        EMAIL_RE,             "[EMAIL]"),
    ("linkedin",     LINKEDIN_RE,          "[LINKEDIN]"),
    ("personal",     PERSONAL_RE,          "[PERSONAL DETAIL]"),
    ("address",      ADDRESS_RE,           "[ADDRESS]"),
    ("institution",  INSTITUTION_RE,       "[INSTITUTION]"),
    ("institution",  INSTITUTION_ABBR_RE,  "[INSTITUTION]"),
    ("grad_year",    GRAD_YEAR_RE,         "[GRADUATION YEAR]"),
    ("gender",       GENDER_RE,            "[GENDER MARKER]"),
    ("phone",        PHONE_RE,             "[PHONE]"),
    ("pincode",      PINCODE_RE,           "[POSTCODE]"),
]

CATEGORY_LABELS = {
    "email": "Email address",
    "phone": "Phone number",
    "linkedin": "LinkedIn profile",
    "institution": "University / college",
    "grad_year": "Graduation year",
    "gender": "Gender marker",
    "personal": "Personal detail (DOB, nationality, marital status)",
    "address": "Postal address",
    "pincode": "Postcode",
    "name": "Candidate name",
}


@dataclass(slots=True)
class Redaction:
    category: str
    text: str
    start: int
    end: int


@dataclass(slots=True)
class RedactionReport:
    """What was removed before scoring, so the claim is auditable rather than asserted."""
    text: str                                       # the redacted text the engine sees
    redactions: list[Redaction] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.redactions)

    def summary(self) -> str:
        if not self.redactions:
            return "No personal information detected in this document."
        parts = [f"{n} {CATEGORY_LABELS.get(cat, cat).lower()}"
                 for cat, n in sorted(self.counts.items(), key=lambda kv: -kv[1])]
        return "Removed before scoring: " + ", ".join(parts) + "."


# ─────────────────────────────────────────────────────────────────────────────
# Redaction
# ─────────────────────────────────────────────────────────────────────────────

def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Regions no redactor may touch.

    GitHub URLs survive because they are evidence about code. Without this guard
    the phone-number pattern happily eats the digits out of a repo name and the
    enrichment channel loses its input.
    """
    return [(m.start(), m.end()) for m in GITHUB_RE.finditer(text)]


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < e and s < span[1] for s, e in spans)


def redact(
    text: str,
    name: str | None = None,
    sections: dict[str, tuple[int, int]] | None = None,
) -> RedactionReport:
    """Strip identity from `text`, preserving character count where it matters.

    Replacements are placeholders of arbitrary length, so spans into the ORIGINAL
    text are not valid against the result. Callers that need spans must chunk the
    redacted text, which is exactly what the parser does.

    `sections` enables the rules that are only correct in one part of a resume —
    today that is the bare graduation-year span inside EDUCATION.
    """
    if not text:
        return RedactionReport(text="")

    protected = _protected_spans(text)
    found: list[Redaction] = []

    if sections and "EDUCATION" in sections:
        edu_start, edu_end = sections["EDUCATION"]
        for m in YEAR_SPAN_RE.finditer(text[edu_start:edu_end]):
            found.append(Redaction(
                "grad_year", m.group(0), edu_start + m.start(), edu_start + m.end()))

    # The candidate's own name, wherever it appears. Done first and by exact
    # string so a two-word name is not left half-redacted by the shape rules.
    if name:
        for part in [name, *name.split()]:
            if len(part) < 3:
                continue
            for m in re.finditer(rf"(?<![\w]){re.escape(part)}(?![\w])", text, re.IGNORECASE):
                if not _overlaps((m.start(), m.end()), protected):
                    found.append(Redaction("name", m.group(0), m.start(), m.end()))

    for category, pattern, _ in CATEGORIES:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if _overlaps(span, protected):
                continue
            found.append(Redaction(category, m.group(0), *span))

    # Resolve overlaps: longest match wins, earliest first.
    found.sort(key=lambda r: (r.start, -(r.end - r.start)))
    kept: list[Redaction] = []
    cursor = -1
    for r in found:
        if r.start >= cursor:
            kept.append(r)
            cursor = r.end

    placeholder = {cat: repl for cat, _, repl in CATEGORIES}
    placeholder["name"] = "[CANDIDATE]"

    out: list[str] = []
    pos = 0
    counts: dict[str, int] = {}
    for r in kept:
        out.append(text[pos:r.start])
        out.append(placeholder.get(r.category, "[REDACTED]"))
        counts[r.category] = counts.get(r.category, 0) + 1
        pos = r.end
    out.append(text[pos:])

    return RedactionReport(text="".join(out), redactions=kept, counts=counts)


# ─────────────────────────────────────────────────────────────────────────────
# Display masking
# ─────────────────────────────────────────────────────────────────────────────

def alias_for(doc_id: str, index: int) -> str:
    """Stable pseudonym shown while blind mode is on."""
    return f"Candidate {index:02d}"


def mask_display(
    text: str,
    name: str | None = None,
    sections: dict[str, tuple[int, int]] | None = None,
) -> str:
    """Hide the identity that survived into DISPLAY text (GitHub links included).

    Display is the one place the portfolio link must also go: a recruiter who can
    read the GitHub handle can read the name off the profile, and blind mode would
    be theatre.
    """
    masked = GITHUB_RE.sub("[PORTFOLIO]", text)
    # Section spans are offsets into `text`; the GitHub substitution shifts
    # everything after the first link, so they are only safe when nothing moved.
    safe_sections = sections if masked == text else None
    return redact(masked, name=name, sections=safe_sections).text
