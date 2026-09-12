"""
Claim verification: does the code agree with the CV?

For every skill a resume names, there are four possible verdicts and the
difference between them is the whole point of this module:

    CORROBORATED   the resume says it and a dependency manifest, language stat or
                   config file proves it. Strongest evidence available. Rewarded.

    CONTRADICTED   the resume says it, the skill is one that code CAN prove, the
                   candidate has a real body of public code, and it is nowhere in
                   any of it. Penalised — this is the case the brief asks for.

    UNVERIFIABLE   the resume says it and no manifest could ever prove it. "System
                   design", "Agile" and "OOP" live here. Never penalised. Silence
                   from a tool that cannot see something is not evidence of absence.

    UNVERIFIED     no GitHub link, too little public code, or a claim the resume
                   itself backs with real work. Never penalised, and kept distinct
                   from UNVERIFIABLE so the UI can say *why* it stayed quiet.

The asymmetry is deliberate. Corroboration needs one positive fact. A
contradiction needs a positive fact to be *missing* from a place it should have
been, which is a far weaker inference — so it is gated three ways: a minimum body
of public code (`VERIFY_MIN_REPOS`), a skill the library map could actually
detect, and — the one that matters most — a claim the RESUME does not already
support on its own.

That last gate exists because of a failure this module had without it. A
candidate whose internship bullet reads "containerised the service with Docker
and deployed to AWS ECS" was marked as contradicting their own CV, because the
employer's code is not, and never will be, in a personal GitHub account. Absence
of private work from a public profile is not evidence of dishonesty. So a skill
that is demonstrated in the candidate's own EXPERIENCE or PROJECTS prose is never
contradicted, however thin their GitHub is; only an unsupported claim — a bare
tag in a skills list, with no project behind it and no code behind it either —
can be. That is the actual shape of resume padding, and it is the only shape this
module is willing to accuse anyone of.

Separately: README-versus-code. A README advertising Kubernetes over a repository
whose only manifest is Express and Mongoose is a claim about a project, not a
capability. Reported, and it feeds the contradiction count.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from backend import config
from backend.core.enrich import Enrichment
from backend.core.fusion import Primitives

CORROBORATED = "CORROBORATED"
CONTRADICTED = "CONTRADICTED"
UNVERIFIABLE = "UNVERIFIABLE"
UNVERIFIED = "UNVERIFIED"


@lru_cache(maxsize=1)
def detectable_skills() -> frozenset[str]:
    """Skills the library map can prove from a repository.

    A skill outside this set can never be CONTRADICTED, because no amount of code
    would have demonstrated it in the first place.
    """
    raw = json.loads(config.LIBRARY_MAP_PATH.read_text(encoding="utf-8"))
    out: set[str] = set()
    for table in ("packages", "imports", "files", "languages"):
        out.update(raw[table].values())
    return frozenset(out)


@dataclass(slots=True)
class ClaimVerdict:
    skill_id: str
    label: str
    tier: str
    verdict: str
    reason: str
    evidence: list[str] = field(default_factory=list)

    @property
    def positive(self) -> bool:
        return self.verdict == CORROBORATED

    @property
    def negative(self) -> bool:
        return self.verdict == CONTRADICTED


@dataclass(slots=True)
class VerificationReport:
    """One candidate's resume checked against their own public code."""
    doc_id: str
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    corroborated: int = 0
    contradicted: int = 0
    unverifiable: int = 0
    unverified: int = 0
    readme_only: list[str] = field(default_factory=list)
    trust: float = 0.5              # 0 = every checkable claim failed, 1 = all held
    checked: bool = False           # did we have enough code to judge at all
    headline: str = ""
    detail: list[str] = field(default_factory=list)

    def multiplier_for(self, skill_id: str) -> float:
        """Score multiplier for one claim, from its verdict."""
        for v in self.verdicts:
            if v.skill_id != skill_id:
                continue
            if v.verdict == CORROBORATED:
                return config.GITHUB_SCORE_MULTIPLIER
            if v.verdict == CONTRADICTED:
                return config.VERIFY_CONTRADICTION_PENALTY
            return 1.0
        return 1.0

    def verdict_for(self, skill_id: str) -> str:
        for v in self.verdicts:
            if v.skill_id == skill_id:
                return v.verdict
        return UNVERIFIED


def verify(p: Primitives, enrichment: Enrichment,
           integ=None) -> VerificationReport:
    """Check every claim on one resume against that candidate's public code.

    `integ` is the integrity report for the same candidate. It supplies the
    resume's own opinion of each claim, which is what keeps a genuine internship
    achievement from being read as a lie because the employer's repository is
    private. Omitting it makes every claim eligible for contradiction, which is
    only ever correct in tests.
    """
    report = VerificationReport(doc_id=p.doc_id)
    gh = enrichment.github
    detectable = detectable_skills()

    # Enough public code for absence to mean anything?
    substantial = gh.ok and len(gh.repos) >= config.VERIFY_MIN_REPOS
    report.checked = substantial

    # README claims the code does not support, across all repositories.
    readme_only: set[str] = set()
    for r in gh.repos:
        readme_only.update(r.readme_only)
    report.readme_only = sorted(readme_only)

    for cell in p.cells:
        # Only claims the resume actually makes are up for verification.
        if cell.lex <= 0.0:
            continue

        proven = enrichment.evidence_for(cell.skill_id)
        if proven:
            report.verdicts.append(ClaimVerdict(
                skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
                verdict=CORROBORATED,
                reason="named on the resume and proven by code",
                evidence=[e.as_sentence() for e in proven[:3]],
            ))
            continue

        if cell.skill_id not in detectable:
            report.verdicts.append(ClaimVerdict(
                skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
                verdict=UNVERIFIABLE,
                reason="no dependency or config file could demonstrate this skill",
            ))
            continue

        if not substantial:
            report.verdicts.append(ClaimVerdict(
                skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
                verdict=UNVERIFIED,
                reason=(f"only {len(gh.repos)} public "
                        f"{'repository' if len(gh.repos) == 1 else 'repositories'} to check against"
                        if gh.ok else "no public code linked from this resume"),
            ))
            continue

        # Does the resume itself stand behind this claim? A skill demonstrated in
        # a role or a project is supported work, and a personal GitHub account is
        # not where an employer's code lives. Only an unsupported claim is
        # eligible to be called contradicted.
        support = integ.support.get(cell.skill_id) if integ is not None else None
        self_supported = bool(support and not support.orphan) if integ is not None else True

        if self_supported:
            report.verdicts.append(ClaimVerdict(
                skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
                verdict=UNVERIFIED,
                reason=("demonstrated in the candidate's own experience or project text, "
                        "which is commonly private employer code — absence from a personal "
                        "GitHub account proves nothing either way"),
            ))
            continue

        in_readme = cell.skill_id in readme_only
        report.verdicts.append(ClaimVerdict(
            skill_id=cell.skill_id, label=cell.label, tier=cell.tier,
            verdict=CONTRADICTED,
            reason=("listed as a skill with no project behind it, and a README advertises "
                    "it while no manifest, language or config file in any repository uses it"
                    if in_readme else
                    f"listed as a skill with no project or role behind it on the resume, "
                    f"and absent from all {len(gh.repos)} public repositories"),
        ))

    report.corroborated = sum(v.verdict == CORROBORATED for v in report.verdicts)
    report.contradicted = sum(v.verdict == CONTRADICTED for v in report.verdicts)
    report.unverifiable = sum(v.verdict == UNVERIFIABLE for v in report.verdicts)
    report.unverified = sum(v.verdict == UNVERIFIED for v in report.verdicts)

    decided = report.corroborated + report.contradicted
    report.trust = round(report.corroborated / decided, 3) if decided else 0.5

    report.headline, report.detail = _narrate(report, gh)
    return report


def _narrate(report: VerificationReport, gh) -> tuple[str, list[str]]:
    detail: list[str] = []

    if not gh.ok:
        return "No public code to check against", [gh.message or
                                                   "No GitHub profile linked from this resume."]

    if not report.checked:
        return (
            f"Too little public code to verify claims",
            [f"{len(gh.repos)} public "
             f"{'repository' if len(gh.repos) == 1 else 'repositories'} found — below the "
             f"{config.VERIFY_MIN_REPOS}-repository minimum, so no claim is marked as "
             f"contradicted. Corroborations still count."],
        )

    if report.contradicted == 0 and report.corroborated:
        headline = f"All {report.corroborated} checkable claims are backed by code"
    elif report.contradicted:
        headline = (f"{report.contradicted} claimed "
                    f"{'skill is' if report.contradicted == 1 else 'skills are'} "
                    f"absent from every repository")
    else:
        headline = "Nothing on this resume could be checked against code"

    if report.corroborated:
        proven = [v.label for v in report.verdicts if v.positive][:8]
        detail.append(f"Proven by code: {', '.join(proven)}"
                      f"{' and more' if report.corroborated > 8 else ''}.")
    if report.contradicted:
        failed = [v.label for v in report.verdicts if v.negative][:8]
        detail.append(
            f"Claimed but never used in {len(gh.repos)} public repositories: "
            f"{', '.join(failed)}. Keyword evidence for these is cut to "
            f"{int(config.VERIFY_CONTRADICTION_PENALTY * 100)}%.")
    if report.readme_only:
        detail.append(
            f"README-only claims: {', '.join(report.readme_only[:6])} are advertised in "
            f"project documentation but appear in no manifest, language stat or config file.")
    if report.unverifiable:
        detail.append(
            f"{report.unverifiable} claims cannot be checked from code at all "
            f"(concepts like system design or ways of working) and are left untouched.")

    return headline, detail


def verify_many(primitives: list[Primitives],
                enrichments: dict[str, Enrichment],
                integrity_reports: dict | None = None) -> dict[str, VerificationReport]:
    from backend.core.enrich import Enrichment as _E
    reports = integrity_reports or {}
    return {
        p.doc_id: verify(p, enrichments.get(p.doc_id) or _E(doc_id=p.doc_id),
                         reports.get(p.doc_id))
        for p in primitives
    }


def pool_summary(reports: dict[str, VerificationReport]) -> dict:
    """Headline numbers for the pool, for the meta panel."""
    checked = [r for r in reports.values() if r.checked]
    return {
        "checked": len(checked),
        "with_contradictions": sum(1 for r in checked if r.contradicted),
        "fully_corroborated": sum(1 for r in checked if r.corroborated and not r.contradicted),
        "readme_only_claims": sum(len(r.readme_only) for r in reports.values()),
    }
