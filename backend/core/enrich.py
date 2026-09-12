"""
External evidence: what the candidate has actually built.

A resume is a claim. A dependency manifest is a receipt. This module goes and
finds the receipts, and it treats the two sources very differently:

    GitHub    Code that was written, committed and pushed. `import fastapi` in a
              repository is not an assertion about FastAPI, it is FastAPI being
              used. This is the strongest evidence the system can obtain, and it
              carries a score multiplier above 1.

    LinkedIn  Self-reported skills and certifications, entered by the candidate
              into a form. Exactly as verifiable as the resume itself, which is
              to say not at all. It may CORROBORATE a claim already on the
              resume; it may never create one. Low weight, deliberately.

Three hard rules, because this is the one part of the pipeline that touches a
network:

 1. Off by default. `/api/analyze` does not fetch anything unless asked to.
 2. Never blocks. Every call is timeout-bounded and every failure degrades to
    "no external evidence", which is a normal state and not an error.
 3. Cached to disk. The same handle is fetched once, so a re-run during a demo
    costs nothing and survives the venue wifi dying between rehearsal and stage.

On LinkedIn specifically: there is no public API for profile data and automated
scraping is blocked and prohibited by their terms. The provider here reads a
local export when one is supplied and otherwise reports itself unavailable. It
does not pretend to have data it could not legally or technically obtain.
"""
from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from backend import config

GITHUB = "github"
LINKEDIN = "linkedin"

# Status values, in the order a caller should care about them.
OK = "ok"
EMPTY = "empty"              # profile reachable, nothing usable in it
NOT_FOUND = "not_found"
UNAVAILABLE = "unavailable"  # blocked, rate-limited, offline, or no provider
DISABLED = "disabled"        # enrichment not requested


# ─────────────────────────────────────────────────────────────────────────────
# Handles
# ─────────────────────────────────────────────────────────────────────────────

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)",
    re.IGNORECASE,
)
_LINKEDIN_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/([A-Za-z0-9%-]{3,100})",
    re.IGNORECASE,
)

# Paths that look like handles but are not people.
_GITHUB_RESERVED = frozenset({
    "about", "features", "pricing", "enterprise", "explore", "topics", "collections",
    "trending", "events", "sponsors", "readme", "orgs", "settings", "marketplace",
    "apps", "login", "join", "search", "new", "notifications", "issues", "pulls",
})


def find_handles(text: str) -> dict[str, str]:
    """Pull profile handles out of resume text.

    Must be given the PRE-redaction text: blind screening replaces the LinkedIn
    URL with a placeholder, which is correct for scoring and fatal here.
    """
    out: dict[str, str] = {}

    for m in _GITHUB_URL_RE.finditer(text or ""):
        handle = m.group(1)
        if handle.lower() not in _GITHUB_RESERVED:
            out[GITHUB] = handle
            break

    m = _LINKEDIN_URL_RE.search(text or "")
    if m:
        out[LINKEDIN] = m.group(1)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Library map
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _library_map() -> dict:
    return json.loads(config.LIBRARY_MAP_PATH.read_text(encoding="utf-8"))


def _lookup(table: str, key: str) -> str | None:
    return _library_map()[table].get(key.strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
# Result containers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class CodeEvidence:
    """One concrete reason to believe a candidate has a skill."""
    skill_id: str
    source: str                 # github | linkedin
    kind: str                   # dependency | language | file | readme | certification | listed
    where: str                  # repo name, or "profile"
    detail: str                 # the line that proved it

    def as_sentence(self) -> str:
        return f"{self.detail} — {self.where}"


@dataclass(slots=True)
class RepoSummary:
    name: str
    description: str = ""
    url: str = ""
    stars: int = 0
    pushed_at: str = ""
    languages: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    readme_skills: list[str] = field(default_factory=list)
    code_skills: list[str] = field(default_factory=list)

    @property
    def readme_only(self) -> list[str]:
        """Skills the README advertises that the code does not back up."""
        return sorted(set(self.readme_skills) - set(self.code_skills))


@dataclass(slots=True)
class Profile:
    """Everything one external source said about one candidate."""
    source: str
    handle: str = ""
    url: str = ""
    status: str = DISABLED
    message: str = ""
    skills: dict[str, list[CodeEvidence]] = field(default_factory=dict)
    repos: list[RepoSummary] = field(default_factory=list)
    fetched_at: float = 0.0
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def skill_ids(self) -> set[str]:
        return set(self.skills)

    def weight(self) -> float:
        return (config.GITHUB_EVIDENCE_WEIGHT if self.source == GITHUB
                else config.LINKEDIN_EVIDENCE_WEIGHT)

    def multiplier(self) -> float:
        return (config.GITHUB_SCORE_MULTIPLIER if self.source == GITHUB
                else config.LINKEDIN_SCORE_MULTIPLIER)


@dataclass(slots=True)
class Enrichment:
    """Both sources for one candidate, plus the combined verdict."""
    doc_id: str
    github: Profile = field(default_factory=lambda: Profile(source=GITHUB))
    linkedin: Profile = field(default_factory=lambda: Profile(source=LINKEDIN))

    @property
    def any_evidence(self) -> bool:
        return self.github.ok or self.linkedin.ok

    def evidence_for(self, skill_id: str) -> list[CodeEvidence]:
        return self.github.skills.get(skill_id, []) + self.linkedin.skills.get(skill_id, [])

    def multiplier_for(self, skill_id: str) -> float:
        """Score multiplier for a skill this candidate can prove externally."""
        if skill_id in self.github.skills:
            return config.GITHUB_SCORE_MULTIPLIER
        if skill_id in self.linkedin.skills:
            return config.LINKEDIN_SCORE_MULTIPLIER
        return 1.0

    def proven_ids(self) -> set[str]:
        return self.github.skill_ids | self.linkedin.skill_ids


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(source: str, handle: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", handle)[:80]
    return config.ENRICHMENT_CACHE / source / f"{safe}.json"


def _cache_read(source: str, handle: str) -> Profile | None:
    path = _cache_path(source, handle)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None
    return _profile_from_dict(raw)


def _cache_write(profile: Profile) -> None:
    if not profile.handle:
        return
    path = _cache_path(profile.source, profile.handle)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_profile_to_dict(profile), indent=1), encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass                                           # a cache that cannot write is not an error


def _profile_to_dict(p: Profile) -> dict:
    d = asdict(p)
    d["skills"] = {k: [asdict(e) for e in v] for k, v in p.skills.items()}
    return d


def _profile_from_dict(raw: dict) -> Profile:
    skills = {
        k: [CodeEvidence(**e) for e in v]
        for k, v in (raw.get("skills") or {}).items()
    }
    return Profile(
        source=raw.get("source", GITHUB),
        handle=raw.get("handle", ""),
        url=raw.get("url", ""),
        status=raw.get("status", UNAVAILABLE),
        message=raw.get("message", ""),
        skills=skills,
        repos=[RepoSummary(**r) for r in raw.get("repos", [])],
        fetched_at=raw.get("fetched_at", 0.0),
        from_cache=True,
    )


def _fixture(source: str, handle: str) -> Profile | None:
    """A canned profile shipped with the repo.

    Lets the enrichment channel be demonstrated with no network and no rate
    limit, against the synthetic corpus whose handles do not exist on real
    GitHub. Fixtures are clearly marked as such in `message`.
    """
    path = config.ROOT / "fixtures" / "profiles" / source / f"{handle.lower()}.json"
    if not path.exists():
        return None
    try:
        profile = _profile_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:                                  # noqa: BLE001
        return None
    profile.from_cache = True
    profile.message = profile.message or "synthetic fixture profile (no network call)"
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# GitHub
# ─────────────────────────────────────────────────────────────────────────────

def _session():
    import requests
    s = requests.Session()
    s.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": "InterLoom-Shortlisting-Engine",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if config.GITHUB_TOKEN:
        s.headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return s


def _get(session, url: str, **kw):
    try:
        return session.get(url, timeout=config.ENRICHMENT_TIMEOUT, **kw)
    except Exception:                                  # noqa: BLE001
        return None


_DEP_LINE_RE = re.compile(r"^\s*([A-Za-z0-9._@/-]+)\s*(?:[=<>!~\[]|$)")
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z0-9_.]+)", re.MULTILINE)


def _deps_from_manifest(name: str, text: str) -> set[str]:
    """Dependency names from a manifest, without a parser per ecosystem."""
    names: set[str] = set()
    lower = name.lower()

    if lower == "package.json":
        try:
            data = json.loads(text)
        except Exception:                              # noqa: BLE001
            return names
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            names.update((data.get(key) or {}).keys())

    elif lower in ("requirements.txt", "pipfile"):
        for line in text.splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            m = _DEP_LINE_RE.match(line)
            if m:
                names.add(m.group(1))

    elif lower == "pyproject.toml":
        for line in text.splitlines():
            line = line.split("#")[0].strip().strip('",')
            m = _DEP_LINE_RE.match(line)
            if m and not line.startswith("["):
                names.add(m.group(1))

    elif lower in ("pom.xml", "build.gradle", "composer.json", "gemfile", "cargo.toml", "go.mod"):
        # Every ecosystem here writes the artifact name as a bare token somewhere
        # on the line; matching the library map against those tokens is enough and
        # avoids carrying four more parsers for a feature that only needs names.
        names.update(re.findall(r"[A-Za-z][A-Za-z0-9._/-]{2,60}", text))

    return {n.strip() for n in names if n.strip()}


def _skills_from_text_deps(deps: set[str]) -> dict[str, str]:
    """dependency name -> skill id, for the ones the library map knows."""
    out: dict[str, str] = {}
    for dep in deps:
        skill = _lookup("packages", dep)
        if skill:
            out[dep] = skill
    return out


def _fetch_repo(session, handle: str, repo: dict) -> tuple[RepoSummary, list[CodeEvidence]]:
    """One repository: languages, file tree, manifests, README."""
    name = repo.get("name", "")
    summary = RepoSummary(
        name=name,
        description=(repo.get("description") or "")[:200],
        url=repo.get("html_url", ""),
        stars=repo.get("stargazers_count", 0),
        pushed_at=(repo.get("pushed_at") or "")[:10],
    )
    evidence: list[CodeEvidence] = []
    seen: set[str] = set()

    def add(skill_id: str, kind: str, detail: str, into: list[str] | None = None):
        if into is not None and skill_id not in into:
            into.append(skill_id)
        key = (skill_id, kind)
        if key in seen:
            return
        seen.add(key)
        evidence.append(CodeEvidence(
            skill_id=skill_id, source=GITHUB, kind=kind, where=name, detail=detail))

    # --- languages ---
    langs = _get(session, f"{config.GITHUB_API}/repos/{handle}/{name}/languages")
    if langs is not None and langs.ok:
        data = langs.json() or {}
        total = sum(data.values()) or 1
        for lang, count in sorted(data.items(), key=lambda kv: -kv[1]):
            summary.languages.append(lang)
            skill = _lookup("languages", lang)
            # Below 5% of the repo it is a stray config file, not a language used.
            if skill and count / total >= 0.05:
                add(skill, "language",
                    f"{lang} is {count / total:.0%} of the code", summary.code_skills)

    # --- file tree, in one call ---
    branch = repo.get("default_branch") or "main"
    tree = _get(session,
                f"{config.GITHUB_API}/repos/{handle}/{name}/git/trees/{branch}",
                params={"recursive": "1"})
    paths: list[str] = []
    if tree is not None and tree.ok:
        paths = [n.get("path", "") for n in (tree.json() or {}).get("tree", [])]

    file_table = _library_map()["files"]
    for path in paths:
        lowered = path.lower()
        base = lowered.rsplit("/", 1)[-1]
        for needle, skill in file_table.items():
            if base == needle or needle in lowered:
                add(skill, "file", f"repository contains {path}", summary.code_skills)
                break

    # --- manifests ---
    manifest_names = _library_map()["manifests"]
    wanted = [p for p in paths
              if p.rsplit("/", 1)[-1] in manifest_names and p.count("/") <= 2][:4]

    for path in wanted:
        summary.manifests.append(path)
        blob = _get(session, f"{config.GITHUB_API}/repos/{handle}/{name}/contents/{path}")
        if blob is None or not blob.ok:
            continue
        payload = blob.json() or {}
        content = payload.get("content") or ""
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:                              # noqa: BLE001
            continue
        base_name = path.rsplit("/", 1)[-1]
        for dep, skill in _skills_from_text_deps(_deps_from_manifest(base_name, text)).items():
            add(skill, "dependency", f"depends on `{dep}` in {base_name}", summary.code_skills)
        # Python imports, when the manifest is thin or absent.
        if base_name.lower() in ("requirements.txt", "pyproject.toml"):
            for mod in set(_IMPORT_RE.findall(text)):
                skill = _lookup("imports", mod.split(".")[0])
                if skill:
                    add(skill, "dependency", f"imports `{mod}`", summary.code_skills)

    # --- README ---
    readme = _get(session, f"{config.GITHUB_API}/repos/{handle}/{name}/readme")
    if readme is not None and readme.ok:
        try:
            text = base64.b64decode((readme.json() or {}).get("content") or "").decode(
                "utf-8", errors="replace")
        except Exception:                              # noqa: BLE001
            text = ""
        summary.readme_skills = _readme_skills(text)

    return summary, evidence


def _readme_skills(text: str) -> list[str]:
    """Skills a README claims, by name.

    Read separately from the code so the two can be compared: a README that
    advertises Kubernetes over a repository with no manifest, no Dockerfile and
    no YAML is a claim, not a capability.
    """
    lowered = (text or "").lower()
    found: list[str] = []
    for table in ("packages", "languages"):
        for key, skill in _library_map()[table].items():
            if skill in found:
                continue
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", lowered):
                found.append(skill)
    return found


def fetch_github(handle: str) -> Profile:
    """Public repository evidence for one GitHub handle."""
    profile = Profile(source=GITHUB, handle=handle,
                      url=f"https://github.com/{handle}")

    fixture = _fixture(GITHUB, handle)
    if fixture is not None:
        return fixture

    cached = _cache_read(GITHUB, handle)
    if cached is not None:
        return cached

    try:
        import requests                                # noqa: F401
    except ImportError:
        profile.status = UNAVAILABLE
        profile.message = "the `requests` package is not installed"
        return profile

    session = _session()
    resp = _get(session, f"{config.GITHUB_API}/users/{handle}/repos",
                params={"sort": "pushed", "per_page": config.ENRICHMENT_MAX_REPOS,
                        "type": "owner"})

    if resp is None:
        profile.status = UNAVAILABLE
        profile.message = "GitHub could not be reached (offline or timed out)"
        return profile
    if resp.status_code == 404:
        profile.status = NOT_FOUND
        profile.message = f"no public GitHub user `{handle}`"
        return profile
    if resp.status_code in (403, 429):
        profile.status = UNAVAILABLE
        profile.message = ("GitHub rate limit reached — set a GITHUB_TOKEN environment "
                           "variable to lift it from 60 to 5000 requests an hour")
        return profile
    if not resp.ok:
        profile.status = UNAVAILABLE
        profile.message = f"GitHub returned HTTP {resp.status_code}"
        return profile

    repos = [r for r in (resp.json() or []) if not r.get("fork")]
    if not repos:
        profile.status = EMPTY
        profile.message = "profile exists but has no public non-forked repositories"
        _cache_write(profile)
        return profile

    collected: list[CodeEvidence] = []
    with ThreadPoolExecutor(max_workers=config.ENRICHMENT_MAX_WORKERS) as pool:
        for summary, evidence in pool.map(lambda r: _fetch_repo(session, handle, r), repos):
            profile.repos.append(summary)
            collected.extend(evidence)

    for ev in collected:
        profile.skills.setdefault(ev.skill_id, []).append(ev)

    profile.status = OK if profile.skills else EMPTY
    profile.message = (
        f"{len(profile.repos)} public repositories, "
        f"{len(profile.skills)} skills proven by code"
        if profile.skills else
        "repositories found, but nothing in them maps to a known skill"
    )
    profile.fetched_at = time.time()
    _cache_write(profile)
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn
# ─────────────────────────────────────────────────────────────────────────────

def fetch_linkedin(handle: str) -> Profile:
    """Self-reported skills and certifications, when they can be obtained lawfully.

    LinkedIn publishes no profile API for this use and blocks automated access to
    profile pages; scraping them also breaches their user agreement. So this
    provider reads a supplied export — `fixtures/profiles/linkedin/<handle>.json`,
    or a cached copy — and otherwise reports itself unavailable.

    That is not a stub standing in for a scraper. It is the honest ceiling of what
    this source can contribute, and the low weight it carries reflects that even a
    complete profile here is self-reported.
    """
    profile = Profile(source=LINKEDIN, handle=handle,
                      url=f"https://linkedin.com/in/{handle}")

    supplied = _fixture(LINKEDIN, handle) or _cache_read(LINKEDIN, handle)
    if supplied is not None:
        return supplied

    profile.status = UNAVAILABLE
    profile.message = (
        "LinkedIn has no public profile API and blocks automated access. "
        "Drop an exported profile at fixtures/profiles/linkedin/"
        f"{handle.lower()}.json to feed this channel."
    )
    return profile


def profile_from_export(handle: str, skills: list[str],
                        certifications: list[str] | None = None) -> Profile:
    """Build a LinkedIn profile from a supplied list. Used by the export loader."""
    profile = Profile(
        source=LINKEDIN, handle=handle, url=f"https://linkedin.com/in/{handle}",
        status=OK, message="from a supplied profile export",
    )
    for name in skills:
        skill = _lookup("packages", name) or _lookup("languages", name) or name.strip().lower()
        profile.skills.setdefault(skill, []).append(CodeEvidence(
            skill_id=skill, source=LINKEDIN, kind="listed", where="profile",
            detail=f"listed as a skill: {name}"))
    for name in certifications or []:
        skill = _lookup("packages", name) or _lookup("languages", name) or name.strip().lower()
        profile.skills.setdefault(skill, []).append(CodeEvidence(
            skill_id=skill, source=LINKEDIN, kind="certification", where="profile",
            detail=f"certification: {name}"))
    if not profile.skills:
        profile.status = EMPTY
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def enrich_one(doc_id: str, text: str, enabled: bool) -> Enrichment:
    """Fetch both sources for one candidate. Never raises, never blocks for long."""
    out = Enrichment(doc_id=doc_id)
    if not enabled:
        out.github.message = "external evidence not requested"
        out.linkedin.message = "external evidence not requested"
        return out

    handles = find_handles(text)

    if GITHUB in handles:
        try:
            out.github = fetch_github(handles[GITHUB])
        except Exception as exc:                       # noqa: BLE001
            out.github = Profile(source=GITHUB, handle=handles[GITHUB],
                                 status=UNAVAILABLE, message=f"{type(exc).__name__}")
    else:
        out.github.status = EMPTY
        out.github.message = "no GitHub link on this resume"

    if LINKEDIN in handles:
        try:
            out.linkedin = fetch_linkedin(handles[LINKEDIN])
        except Exception as exc:                       # noqa: BLE001
            out.linkedin = Profile(source=LINKEDIN, handle=handles[LINKEDIN],
                                   status=UNAVAILABLE, message=f"{type(exc).__name__}")
    else:
        out.linkedin.status = EMPTY
        out.linkedin.message = "no LinkedIn link on this resume"

    return out


def enrich_many(docs: list, enabled: bool) -> dict[str, Enrichment]:
    """Enrich a whole pool in parallel. `docs` are ParsedDocs."""
    if not enabled:
        return {d.doc_id: enrich_one(d.doc_id, "", False) for d in docs}

    config.ENRICHMENT_CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=config.ENRICHMENT_MAX_WORKERS) as pool:
        results = pool.map(
            # raw_text, not text: the LinkedIn URL is redacted out of the scored copy.
            lambda d: enrich_one(d.doc_id, d.raw_text or d.text, True), docs)
        return {e.doc_id: e for e in results}
