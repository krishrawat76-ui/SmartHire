"""
Print the complete derivation of one candidate's score, every number shown.

Built for pitch prep. The judges will ask the team to walk through how the
matching actually works; this prints exactly that walk-through for a real
candidate so you can rehearse against real figures rather than the formula alone.

    python scripts/explain_scoring.py                 # the top candidate
    python scripts/explain_scoring.py --name Priya    # a specific one
    python scripts/explain_scoring.py --flag HIDDEN_GEM
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import _console  # noqa: F401  (configures stdout encoding on import)

from backend import config                                    # noqa: E402
from backend.core import engine as engine_mod, fusion, parser, skills  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDF_GLOB = "*.pdf"
RULE = "─" * 78


def head(text: str) -> None:
    print(f"\n{text}\n{RULE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="substring of the candidate's name")
    ap.add_argument("--flag", choices=["HIDDEN_GEM", "SURFACE_MATCH", "CONSENSUS"])
    ap.add_argument("--alpha", type=float, default=config.DEFAULT_ALPHA)
    args = ap.parse_args()

    real_jd = sorted((ROOT / "data" / "jd").glob(PDF_GLOB))
    real_res = sorted((ROOT / "data" / "resumes").glob(PDF_GLOB))
    if real_jd and real_res:
        jd_path, resume_paths = real_jd[0], real_res
    else:
        syn = ROOT / "fixtures" / "synthetic"
        jd_path = syn / "jd_technova.pdf"
        resume_paths = sorted(p for p in syn.glob(PDF_GLOB) if not p.name.startswith("jd_"))

    jd = parser.extract(jd_path)
    skill_set = skills.extract_skills(jd.text)
    docs = parser.extract_many(resume_paths)
    primitives = fusion.prepare(engine_mod.Engine().build(docs, jd.text, skill_set), skill_set)
    cands = fusion.score(primitives, alpha=args.alpha)

    if args.name:
        pick = next((c for c in cands if args.name.lower() in c.name.lower()), None)
        if not pick:
            sys.exit(f"No candidate matching {args.name!r}")
    elif args.flag:
        pick = next((c for c in cands if c.flag == args.flag), None)
        if not pick:
            sys.exit(f"No candidate flagged {args.flag}")
    else:
        pick = cands[0]

    p = pick.primitives

    print(f"SCORE DERIVATION — {pick.name}")
    print(f"rank #{pick.rank} of {len(cands)} · flag {pick.flag} · alpha {args.alpha}")

    # ── Channel K ────────────────────────────────────────────────────────────
    head("CHANNEL K — keyword / lexical")
    print(f"  BM25-Okapi raw                     {p.bm25_raw:8.3f}")
    print(f"  normalised across the pool (P5–P95){p.bm25_norm:8.3f}")
    print(f"  weighted lexical coverage          {p.lex_cov:8.3f}")
    print()
    print(f"  K = {config.K_WEIGHT_BM25} × {p.bm25_norm:.3f} "
          f"+ {1 - config.K_WEIGHT_BM25:.2f} × {p.lex_cov:.3f}"
          f"  =  {pick.k_score:.4f}")

    # ── Channel M ────────────────────────────────────────────────────────────
    head("CHANNEL M — semantic")
    print(f"  top-{config.DOCSIM_TOP_K} pooled document similarity  {p.docsim_raw:8.3f}")
    print(f"  normalised across the pool         {p.docsim_norm:8.3f}")
    print(f"  weighted semantic coverage         {p.sem_cov:8.3f}")
    print()
    print(f"  M = {config.M_WEIGHT_DOCSIM} × {p.docsim_norm:.3f} "
          f"+ {1 - config.M_WEIGHT_DOCSIM:.2f} × {p.sem_cov:.3f}"
          f"  =  {pick.m_score:.4f}")

    # ── Fusion ───────────────────────────────────────────────────────────────
    head("FUSION")
    blend = args.alpha * pick.k_score + (1 - args.alpha) * pick.m_score
    print(f"  S = 100 × [ α·K + (1−α)·M ] × Gate")
    print(f"    = 100 × [ {args.alpha} × {pick.k_score:.4f} "
          f"+ {1 - args.alpha:.2f} × {pick.m_score:.4f} ] × {pick.gate:.2f}")
    print(f"    = 100 × {blend:.4f} × {pick.gate:.2f}")
    print(f"    = {pick.score:.2f}")

    # ── Evidence matrix ──────────────────────────────────────────────────────
    head("EVIDENCE MATRIX — every cell that produced those numbers")
    print(f"  {'skill':<20}{'tier':<11}{'lex':>5} {'kind':<7}{'cos':>7}"
          f"{'g(cos)':>8}{'cov':>7}  status")
    print(f"  {'-' * 74}")
    for cell in sorted(p.cells, key=lambda c: (c.tier != "REQUIRED", -c.coverage, c.label)):
        print(f"  {cell.label:<20}{cell.tier:<11}{cell.lex:>5.1f} {cell.lex_kind:<7}"
              f"{cell.sem_raw:>7.3f}{cell.sem_cal:>8.3f}{cell.coverage:>7.3f}  {cell.status}")

    print(f"\n  coverage = max(lex, g(cos))   — a soft-OR: literal presence and semantic")
    print(f"                                  inference are alternative evidence, not additive")
    print(f"  g(x)     = clip((x − {config.TAU_LO}) / ({config.TAU_HI} − {config.TAU_LO}), 0, 1)")

    # ── The semantic channel's actual contribution ───────────────────────────
    inferred = [c for c in p.cells if c.status == "INFERRED"]
    if inferred:
        head("WHAT THE SEMANTIC CHANNEL FOUND THAT KEYWORDS COULD NOT")
        for cell in sorted(inferred, key=lambda c: -c.sem_raw):
            print(f"  {cell.label}  (cosine {cell.sem_raw:.3f}, never written literally)")
            print(f"    “{' '.join(cell.evidence.split())[:100]}”")
            print()

    # ── Cross-channel ────────────────────────────────────────────────────────
    head("CROSS-CHANNEL AGREEMENT")
    print(f"  lexical rank    #{pick.rank_lexical}")
    print(f"  semantic rank   #{pick.rank_semantic}")
    print(f"  delta           {pick.rank_delta:+d}")
    print(f"  RRF             {pick.rrf:.6f}   = 1/(60+{pick.rank_lexical}) + 1/(60+{pick.rank_semantic})")
    print(f"  required covered only semantically  {p.inferred_req_ratio:.0%}")
    print(f"  lexical − semantic coverage         {p.lex_cov - p.sem_cov:+.3f}")
    print(f"\n  → {pick.flag}")

    print(f"\n{RULE}")
    print("No language model was consulted at any point in this derivation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
