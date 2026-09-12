"""
The technology ontology: what a skill *is*, and how far it is from another one.

Two questions are answered here, and everything else in the product that talks
about skill relationships reads from this module rather than inventing its own
notion of "similar":

    path(skill)            FastAPI -> Python Backend -> Backend Engineering ->
                           Software Engineering. Shown on screen whenever the
                           engine credits a non-identical match, so a semantic
                           score is never asked to be taken on faith.

    bridge(known, target)  How far is the walk from something the candidate has
                           to something the JD wants, and what does that walk
                           cost in weeks.

Distance is structural, not learned. Two skills sharing an immediate parent are
siblings; two sharing only the root share nothing but being software. That gives
a defensible number without a model in the loop, and it is inspectable — which
is the whole point of showing the path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from backend import config

ROOT_CONCEPT = "software-engineering"


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(config.TAXONOMY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def concepts() -> dict[str, dict]:
    return _raw()["concepts"]


@lru_cache(maxsize=1)
def skills() -> dict[str, dict]:
    return _raw()["skills"]


@lru_cache(maxsize=1)
def _bridges() -> dict[tuple[str, str], dict]:
    return {(b["from"], b["to"]): b for b in _raw()["bridges"]}


def known(skill_id: str) -> bool:
    """True when the ontology has an explicit node for this skill.

    Mined skills (terms the gazetteer never heard of) legitimately have none, and
    every caller has to cope with that rather than guess a position for them.
    """
    return skill_id in skills()


def label(node_id: str) -> str:
    """Display name for a concept id, or the id itself for a skill."""
    c = concepts().get(node_id)
    return c["label"] if c else node_id


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def ancestors(skill_id: str) -> tuple[str, ...]:
    """Concept chain from the skill's immediate parent up to the root.

    Returned root-last, so ancestors('fastapi') is
    ('python-backend', 'backend', 'software-engineering').
    """
    node = skills().get(skill_id)
    if not node:
        return ()

    chain: list[str] = []
    seen: set[str] = set()
    cursor: str | None = node["parent"]
    while cursor and cursor not in seen:
        seen.add(cursor)
        chain.append(cursor)
        cursor = concepts().get(cursor, {}).get("parent")
    return tuple(chain)


@lru_cache(maxsize=512)
def path(skill_id: str) -> tuple[str, ...]:
    """The full ontological path, skill first: ('fastapi', 'python-backend', ...)."""
    if not known(skill_id):
        return ()
    return (skill_id, *ancestors(skill_id))


def path_labels(skill_id: str, display: str | None = None) -> list[str]:
    """Human-readable path, ready to render as `A -> B -> C`."""
    chain = path(skill_id)
    if not chain:
        return []
    return [display or skill_id, *[label(c) for c in chain[1:]]]


def depth(skill_id: str) -> int:
    """How specific this skill is. A deeper node is a narrower thing."""
    return len(ancestors(skill_id))


# ─────────────────────────────────────────────────────────────────────────────
# Distance
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Relation:
    """How two skills are related, and the evidence for saying so."""
    a: str
    b: str
    kind: str               # identical | sibling | family | domain | discipline | unrelated
    common: str | None      # lowest common ancestor concept id
    shared_depth: int       # how deep that ancestor sits (higher = more in common)
    steps: int              # nodes traversed from a up to the LCA and back down to b
    via: list[str] = field(default_factory=list)   # the walk, as concept ids

    @property
    def common_label(self) -> str:
        return label(self.common) if self.common else "nothing in common"


@lru_cache(maxsize=256)
def _concept_depth(concept_id: str) -> int:
    """Distance from the root. 0 is software-engineering itself."""
    depth, cursor, seen = 0, concept_id, set()
    while cursor and cursor not in seen:
        seen.add(cursor)
        parent = concepts().get(cursor, {}).get("parent")
        if parent is None:
            break
        depth += 1
        cursor = parent
    return depth


def _lca(a: str, b: str) -> tuple[str | None, int]:
    """Lowest common ancestor of two skills and its depth from the root."""
    chain_a = ancestors(a)
    chain_b = set(ancestors(b))
    for idx, node in enumerate(chain_a):
        if node in chain_b:
            # depth from root = total chain length below it
            return node, len(chain_a) - idx - 1
    return None, -1


def relate(a: str, b: str) -> Relation:
    """Classify the relationship between two skills, structurally."""
    if a == b:
        return Relation(a, b, "identical", ancestors(a)[0] if ancestors(a) else None,
                        depth(a), 0, [])

    if not known(a) or not known(b):
        return Relation(a, b, "unrelated", None, -1, 99, [])

    common, shared_depth = _lca(a, b)
    if common is None:
        return Relation(a, b, "unrelated", None, -1, 99, [])

    chain_a = ancestors(a)
    chain_b = ancestors(b)
    up = chain_a.index(common)
    down = chain_b.index(common)
    steps = up + down + 2          # +2 for the two skill nodes themselves

    # Direct secondary edge: react -> react native is a real adjacency the tree
    # alone cannot express, and it should not be read as a mere sibling.
    if b in skills()[a].get("also", []) or a in skills()[b].get("also", []):
        kind = "sibling"
    elif common == ROOT_CONCEPT:
        # Meeting only at "software engineering" is not a relationship. Checked
        # before the structural tests because a skill hanging directly off the
        # root (full stack, say) would otherwise read as a sibling of anything
        # else that does — which made "Agile, one week away from Full Stack".
        kind = "discipline"
    elif up == 0 and down == 0:
        kind = "sibling"           # same immediate parent
    elif up <= 1 and down <= 1 and _concept_depth(common) >= 2:
        # A family has to be a SPECIFIC family. Requiring a deep common ancestor
        # stops "both are DevOps" from pricing Git -> AWS like Flask -> FastAPI:
        # one is a change of tool, the other a change of subject.
        kind = "family"
    else:
        kind = "domain"            # same broad domain, different subtree

    via = [*chain_a[:up], common, *reversed(chain_b[:down])]
    return Relation(a, b, kind, common, shared_depth, steps, via)


def neighbours(skill_id: str, limit: int = 6) -> list[str]:
    """Closest other skills in the ontology, nearest first.

    Powers "you already know X, which is the springboard to Y" without anyone
    having to hand-maintain a similarity table.
    """
    if not known(skill_id):
        return []
    scored = []
    for other in skills():
        if other == skill_id:
            continue
        rel = relate(skill_id, other)
        if rel.kind in ("unrelated", "discipline"):
            continue
        scored.append((rel.steps, -rel.shared_depth, other))
    scored.sort()
    return [s for _, _, s in scored[:limit]]


# ─────────────────────────────────────────────────────────────────────────────
# Bridge cost
# ─────────────────────────────────────────────────────────────────────────────

# Weeks of focused effort to cross each kind of gap, for a skill of average
# difficulty (3). Difficulty scales these; see bridge().
BASE_WEEKS = {
    "identical":  0.0,
    "sibling":    1.0,   # React -> Vue
    "family":     1.5,   # Flask -> FastAPI
    "domain":     2.5,   # Python -> Node.js
    "discipline": 6.0,   # Frontend-only -> Backend
    "unrelated":  9.0,   # no adjacent experience at all: 2+ months
}

# A skill's own weight in the estimate. Difficulty 3 is the neutral point, so a
# trivial tool shortens the walk and a genuine mental model lengthens it.
DIFFICULTY_SCALE = {1: 0.5, 2: 0.75, 3: 1.0, 4: 1.4, 5: 1.9}


@dataclass(slots=True)
class Bridge:
    """One estimated walk from something the candidate has to something they need."""
    target: str                 # skill id being learned
    target_label: str
    springboard: str | None     # skill id they already have, or None
    springboard_label: str
    kind: str
    weeks: float
    note: str
    via_labels: list[str] = field(default_factory=list)
    pinned: bool = False        # came from an explicit bridges[] entry

    @property
    def human(self) -> str:
        """'~1 week', '2-3 weeks', '2+ months' — never false precision."""
        return humanise_weeks(self.weeks)


def humanise_weeks(weeks: float) -> str:
    """Render a week count the way a recruiter would say it out loud."""
    if weeks <= 0:
        return "already there"
    if weeks < 0.85:
        return "a few days"
    if weeks < 1.3:
        return "~1 week"
    if weeks < 4.4:
        lo = int(weeks)
        hi = lo + 1
        return f"{lo}-{hi} weeks"
    months = weeks / 4.345
    if months < 1.3:
        return "~1 month"
    if months < 2.0:
        return "6-8 weeks"
    return f"{int(months)}+ months"


def bridge(target: str, held: dict[str, float], target_label: str | None = None) -> Bridge:
    """Estimate the ramp-up onto `target` for someone who holds `held`.

    `held` maps skill_id -> strength in [0,1] (how well they demonstrably have it).
    The best springboard is the one with the cheapest walk, not simply the nearest:
    a weakly-held sibling can be a worse launch pad than a firmly-held cousin.
    """
    label_out = target_label or target
    node = skills().get(target)
    difficulty = node["difficulty"] if node else 3
    scale = DIFFICULTY_SCALE.get(difficulty, 1.0)

    strong = {k: v for k, v in held.items() if v > 0.0}

    # Nothing adjacent at all — the 2+ months case.
    if not strong or not known(target):
        weeks = BASE_WEEKS["unrelated"] * scale
        return Bridge(
            target=target, target_label=label_out, springboard=None,
            springboard_label="no adjacent experience", kind="unrelated",
            weeks=round(weeks, 1),
            note=("Nothing on this resume sits near this skill, so the estimate "
                  "assumes learning it from the ground up."),
        )

    pinned_best: Bridge | None = None
    computed_best: Bridge | None = None

    for source, strength in strong.items():
        if not known(source):
            continue
        rel = relate(source, target)

        pin = _bridges().get((source, target))
        if pin:
            weeks = pin["weeks"]
            note = pin["note"]
        else:
            weeks = BASE_WEEKS[rel.kind] * scale
            note = _describe(rel, source, target)

        # Holding the springboard firmly shortens the walk; holding it only by
        # inference lengthens it. Range is deliberately narrow — evidence
        # strength adjusts an estimate, it does not define it.
        weeks = round(max(weeks * (1.25 - 0.35 * strength), 0.25), 1)

        candidate = Bridge(
            target=target, target_label=label_out,
            springboard=source, springboard_label=source,
            # A pinned pair was measured, so its structural kind is not the story:
            # Python -> Node.js shares only the root yet is a three-week walk.
            # Report a band consistent with the number actually being shown.
            kind=_band(weeks) if pin else rel.kind,
            weeks=weeks, note=note,
            via_labels=[label(v) for v in rel.via],
            pinned=pin is not None,
        )

        slot = "pinned_best" if pin else "computed_best"
        current = pinned_best if pin else computed_best
        if current is None or candidate.weeks < current.weeks:
            if slot == "pinned_best":
                pinned_best = candidate
            else:
                computed_best = candidate

    # A pin is a measurement; a computed estimate is an inference from tree shape.
    # When we have measured this exact pair, that answer stands even if some other
    # held skill produces a cheaper-looking walk — otherwise Linux would quietly
    # undercut the Docker -> Kubernetes figure we deliberately pinned.
    best = pinned_best or computed_best

    if best is None:                     # every held skill was unknown to the ontology
        weeks = BASE_WEEKS["unrelated"] * scale
        return Bridge(
            target=target, target_label=label_out, springboard=None,
            springboard_label="no adjacent experience", kind="unrelated",
            weeks=round(weeks, 1),
            note="No skill on this resume is placed in the ontology near this one.",
        )
    return best


def _band(weeks: float) -> str:
    """Closeness band implied by a week count, for pinned pairs.

    Keeps the word on screen agreeing with the number next to it.
    """
    if weeks <= 1.2:
        return "sibling"
    if weeks <= 2.0:
        return "family"
    if weeks <= 3.5:
        return "domain"
    return "discipline"


def _describe(rel: Relation, source: str, target: str) -> str:
    """Plain-English reason for a computed (unpinned) estimate."""
    shared = rel.common_label
    if rel.kind == "sibling":
        return f"Both are {shared}; the concepts carry over and the syntax is the delta."
    if rel.kind == "family":
        return f"Same family ({shared}) — the model transfers, the API surface does not."
    if rel.kind == "domain":
        return f"Shared ground is {shared}; the ecosystem around it is new."
    if rel.kind == "discipline":
        return (f"{source} and {target} only meet at {shared} — this is a change of "
                f"discipline, not a change of tool.")
    return "No adjacent experience on the resume."


def combined_weeks(bridges: list[Bridge]) -> float:
    """Total ramp-up for several gaps at once.

    Not a sum. Someone learning three adjacent things learns the second and third
    faster than the first — shared context, and the gaps overlap. The largest gap
    is paid in full and each subsequent one at a decaying rate, which keeps the
    total honest without pretending they are free.
    """
    if not bridges:
        return 0.0
    ordered = sorted((b.weeks for b in bridges), reverse=True)
    return round(sum(w * (0.6 ** i) for i, w in enumerate(ordered)), 1)
