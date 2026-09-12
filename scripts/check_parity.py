"""
Prove that the browser's slider arithmetic matches the engine's.

frontend/src/lib/rescore.js re-ranks the pool client-side so the weight slider is
instant. That is only safe if it computes EXACTLY what fusion.score() computes.
Nothing raises if the two drift — the ranking would just be quietly wrong in the
UI — so this test is the thing standing between us and a silent demo bug.

    python scripts/check_parity.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
from dataclasses import asdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import _console  # noqa: F401  (configures stdout encoding on import)

from backend import config                                    # noqa: E402
from backend.core import assess, engine as engine_mod, fusion, parser, skills  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESCORE_JS = ROOT / "frontend" / "src" / "lib" / "rescore.js"
ALPHAS = [0.0, 0.25, 0.5, 0.75, 1.0]
TOLERANCE = 1e-9

NODE_DRIVER = """
import { rescore } from %(module)s;
import { readFileSync } from 'node:fs';

const { candidates, meta, alphas, gate } = JSON.parse(readFileSync(%(payload)s, 'utf8'));
const out = {};
for (const a of alphas) {
  out[a] = rescore(candidates, a, gate, meta).map(c => ({
    doc_id: c.doc_id, score: c.score, rank: c.rank,
    k: c.k_score, m: c.m_score, gate: c.gate,
  }));
}
process.stdout.write(JSON.stringify(out));
"""


def build_pool():
    syn = ROOT / "fixtures" / "synthetic"
    real_jd = sorted((ROOT / "data" / "jd").glob("*.pdf"))
    real_res = sorted((ROOT / "data" / "resumes").glob("*.pdf"))

    if real_jd and real_res:
        jd_path, resume_paths = real_jd[0], real_res
        source = "data/ (real corpus)"
    else:
        jd_path = syn / "jd_technova.pdf"
        resume_paths = sorted(p for p in syn.glob("*.pdf") if not p.name.startswith("jd_"))
        source = "fixtures/synthetic"

    if not jd_path.exists() or not resume_paths:
        sys.exit("No corpus found. Run: python scripts/make_synthetic_corpus.py")

    jd = parser.extract(jd_path, anonymise=False)
    skill_set = skills.extract_skills(jd.text)
    docs = parser.extract_many(resume_paths)
    subs = engine_mod.Engine().build(docs, jd.text, skill_set)
    primitives = fusion.prepare(subs, skill_set)

    # Apply the same adjustment pass /api/analyze applies. Without this the
    # pool would carry doc_multiplier == 1.0 everywhere and the parity check
    # would never exercise the term it exists to protect.
    fusion.adjust(primitives, assess.build(docs, skill_set, primitives).adjustments)
    return primitives, source


def main() -> int:
    if not RESCORE_JS.exists():
        sys.exit(f"Missing {RESCORE_JS}")

    primitives, source = build_pool()
    print(f"corpus     : {source}")
    print(f"pool       : {len(primitives)} candidates")
    print(f"tolerance  : {TOLERANCE}\n")

    # Shape the payload the way /api/analyze does, so we test the real contract.
    meta = {
        "k_weight_bm25": config.K_WEIGHT_BM25,
        "m_weight_docsim": config.M_WEIGHT_DOCSIM,
        "gate_floor": config.GATE_FLOOR,
        "gate_span": config.GATE_SPAN,
    }
    baseline = fusion.score(primitives, alpha=config.DEFAULT_ALPHA)
    payload = {
        "meta": meta,
        "alphas": ALPHAS,
        "gate": False,
        "candidates": [
            {
                "doc_id": c.doc_id, "name": c.name, "rank": c.rank,
                "primitives": {
                    k: v for k, v in asdict(c.primitives).items()
                    if k not in ("doc_id", "name", "filename", "cells", "warnings")
                },
            }
            for c in baseline
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        payload_path = tmp / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        driver = tmp / "driver.mjs"
        driver.write_text(NODE_DRIVER % {
            "module": json.dumps(RESCORE_JS.as_uri()),
            "payload": json.dumps(str(payload_path)),
        }, encoding="utf-8")

        try:
            proc = subprocess.run(
                ["node", str(driver)], capture_output=True, text=True, timeout=60, check=False
            )
        except FileNotFoundError:
            sys.exit("node not found on PATH — cannot verify slider parity.")

        if proc.returncode != 0:
            print(proc.stderr)
            sys.exit("node driver failed")

        # JavaScript stringifies numeric object keys, so 0.0 arrives as "0" and
        # 1.0 as "1". Re-key by float so lookups are exact.
        js_results = {float(k): v for k, v in json.loads(proc.stdout).items()}

    failures = 0
    for alpha in ALPHAS:
        py = fusion.score(primitives, alpha=alpha, gate=False)
        js = js_results[alpha]

        worst = 0.0
        order_ok = [c.doc_id for c in py] == [r["doc_id"] for r in js]

        by_id = {r["doc_id"]: r for r in js}
        for c in py:
            r = by_id.get(c.doc_id)
            if r is None:
                failures += 1
                continue
            for label, a, b in (
                ("score", c.score, r["score"]),
                ("k", c.k_score, r["k"]),
                ("m", c.m_score, r["m"]),
                ("gate", c.gate, r["gate"]),
            ):
                delta = abs(a - b)
                worst = max(worst, delta)
                if delta > TOLERANCE:
                    failures += 1
                    print(f"  MISMATCH alpha={alpha} {c.name} {label}: py={a} js={b} Δ={delta}")

        status = "PASS" if order_ok and worst <= TOLERANCE else "FAIL"
        print(f"  alpha={alpha:<5} order={'same' if order_ok else 'DIFFERENT':<9} "
              f"max Δ={worst:.2e}  {status}")
        if not order_ok:
            failures += 1

    print()
    if failures:
        print(f"PARITY FAILED — {failures} discrepancies.")
        print("fusion.score() and rescore.js have drifted. Fix before demoing.")
        return 1

    print("PARITY OK — the browser and the engine agree to 1e-9 at every alpha.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
