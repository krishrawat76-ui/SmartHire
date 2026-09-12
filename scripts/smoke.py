"""
Run the whole pipeline from the command line and print a ranked table.

The fastest way to see whether the engine is behaving, with no server and no UI.

    python scripts/smoke.py
    python scripts/smoke.py --alpha 1.0          # pure keyword
    python scripts/smoke.py --alpha 0.0 --gate   # pure semantic, must-have gate on
    python scripts/smoke.py --explain 3          # explanations for the top 3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import _console  # noqa: F401  (configures stdout encoding on import)

from backend import config                                    # noqa: E402
from backend.core import engine as engine_mod, explainer, fusion, parser, skills  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDF_GLOB = "*.pdf"


def resolve_corpus(jd_arg: str | None, resumes_arg: str | None):
    """Prefer an explicit path, then the real corpus in data/, then the fixtures."""
    if jd_arg and resumes_arg:
        return pathlib.Path(jd_arg), sorted(pathlib.Path(resumes_arg).glob(PDF_GLOB)), "explicit"

    real_jd = sorted((ROOT / "data" / "jd").glob(PDF_GLOB))
    real_res = sorted((ROOT / "data" / "resumes").glob(PDF_GLOB))
    if real_jd and real_res:
        return real_jd[0], real_res, "data/ (real corpus)"

    syn = ROOT / "fixtures" / "synthetic"
    jd = syn / "jd_technova.pdf"
    res = sorted(p for p in syn.glob(PDF_GLOB) if not p.name.startswith("jd_"))
    if not jd.exists() or not res:
        sys.exit("No corpus found. Run: python scripts/make_synthetic_corpus.py")
    return jd, res, "fixtures/synthetic"


def main() -> int:
    ap = argparse.ArgumentParser(description="Rank resumes against a job description.")
    ap.add_argument("--jd")
    ap.add_argument("--resumes")
    ap.add_argument("--alpha", type=float, default=config.DEFAULT_ALPHA,
                    help="1.0 = pure keyword, 0.0 = pure semantic")
    ap.add_argument("--gate", action="store_true", help="apply the must-have gate")
    ap.add_argument("--explain", type=int, default=0, metavar="N",
                    help="print explanations for the top N")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    jd_path, resume_paths, source = resolve_corpus(args.jd, args.resumes)

    t0 = time.perf_counter()
    jd = parser.extract(jd_path)
    skill_set = skills.extract_skills(jd.text)
    docs = parser.extract_many(resume_paths)
    eng = engine_mod.Engine()
    primitives = fusion.prepare(eng.build(docs, jd.text, skill_set), skill_set)
    cands = fusion.score(primitives, alpha=args.alpha, gate=args.gate)
    elapsed = time.perf_counter() - t0

    if args.json:
        print(json.dumps([{
            "rank": c.rank, "name": c.name, "score": c.score,
            "k": c.k_score, "m": c.m_score, "flag": c.flag,
            "req_coverage": round(c.primitives.req_coverage, 3),
        } for c in cands], indent=2))
        return 0

    req = sum(1 for s in skill_set if s.tier == "REQUIRED")
    print(f"corpus   : {source}  ({len(docs)} resumes)")
    print(f"engine   : {eng.backend_name} on {config.DEVICE}")
    print(f"skills   : {len(skill_set)} extracted ({req} required)")
    print(f"weights  : alpha={args.alpha}  gate={'on' if args.gate else 'off'}")
    print(f"elapsed  : {elapsed:.2f}s\n")

    print(f"{'#':<4}{'candidate':<20}{'score':>7}{'K':>7}{'M':>7}{'req':>6}"
          f"{'lex':>5}{'sem':>5}  flag")
    print("-" * 78)
    for c in cands:
        flag = "" if c.flag == "CONSENSUS" else c.flag.replace("_", " ").lower()
        print(f"{c.rank:<4}{c.name[:19]:<20}{c.score:>7.1f}{c.k_score:>7.3f}"
              f"{c.m_score:>7.3f}{c.primitives.req_coverage:>6.0%}"
              f"{c.rank_lexical:>5}{c.rank_semantic:>5}  {flag}")

    scores = [c.score for c in cands]
    print(f"\nspread   : {max(scores) - min(scores):.1f} points "
          f"(top {max(scores):.1f}, bottom {min(scores):.1f})")

    warned = [d for d in docs if d.warnings]
    if warned:
        print(f"warnings : {len(warned)} of {len(docs)} resumes parsed with notes")

    if args.explain:
        print()
        for e in explainer.explain_top(cands, args.explain):
            print("=" * 78)
            print(e.headline)
            for b in e.bullets:
                print(f"  - {b}")
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
