"""
The acceptance suite. Run this before demoing, and again after any tuning change.

Ten checks, in rough order of how badly a failure would hurt. Check 3 is the one
that matters most: if the keyword and semantic rankings are identical, one channel
is dead and 35% of the rubric is gone regardless of how good everything else looks.

    python scripts/verify.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import _console  # noqa: F401  (configures stdout encoding on import)

from backend import config                                    # noqa: E402
from backend.core import (                                    # noqa: E402
    bias_detector, chat, engine as engine_mod, explainer, fusion, parser, skills,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SYN = ROOT / "fixtures" / "synthetic"
EDGE = ROOT / "fixtures" / "edge"
PDF_GLOB = "*.pdf"

MIN_SPREAD = 35.0

# Time to rank a batch once the model is resident. This is the number a demo
# actually experiences: backend/app.py builds the Engine once per process, so
# the model load is paid at startup by run.bat's warm-up, not per analysis.
MAX_PIPELINE_SECONDS = 15.0

# Loading MiniLM is hardware, not pipeline. Reported so a slow machine is
# visible, and warned rather than failed so a cold CI box does not report a
# scoring regression that isn't one.
SLOW_MODEL_LOAD_SECONDS = 20.0

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool | str, detail: str = "") -> None:
    status = ok if isinstance(ok, str) else (PASS if ok else FAIL)
    results.append((name, status, detail))
    icon = {PASS: "✓", FAIL: "✗", WARN: "!"}[status]
    print(f"  {icon} {name:<44} {status:<5} {detail}")


def main() -> int:
    print("InterLoom acceptance suite\n")

    real_jd = sorted((ROOT / "data" / "jd").glob(PDF_GLOB))
    real_res = sorted((ROOT / "data" / "resumes").glob(PDF_GLOB))
    if real_jd and real_res:
        jd_path, resume_paths, source = real_jd[0], real_res, "data/ (REAL corpus)"
    else:
        jd_path = SYN / "jd_technova.pdf"
        resume_paths = sorted(p for p in SYN.glob(PDF_GLOB) if not p.name.startswith("jd_"))
        source = "fixtures/synthetic"

    if not jd_path.exists() or not resume_paths:
        sys.exit("No corpus found. Run: python scripts/make_synthetic_corpus.py")

    print(f"corpus: {source}  ({len(resume_paths)} resumes)\n")

    # ── 1. Cold smoke ────────────────────────────────────────────────────────
    # The JD is parsed WITHOUT anonymisation, exactly as backend/app.py does it:
    # redacting it would strip the gendered wording the bias detector exists to
    # find, and this suite must exercise the real path.
    t0 = time.perf_counter()
    jd = parser.extract(jd_path, anonymise=False)
    skill_set = skills.extract_skills(jd.text)
    docs = parser.extract_many(resume_paths)

    t_model = time.perf_counter()
    eng = engine_mod.Engine()
    primitives = fusion.prepare(eng.build(docs, jd.text, skill_set), skill_set)
    cands = fusion.score(primitives, alpha=config.DEFAULT_ALPHA)
    elapsed = time.perf_counter() - t0

    # Engine() loads the model lazily inside build(), so the two cannot be timed
    # apart without reaching into it. Warm it explicitly, then time a second
    # build: that is the per-batch cost the UI pays on every upload.
    t1 = time.perf_counter()
    fusion.prepare(eng.build(docs, jd.text, skill_set), skill_set)
    warm = time.perf_counter() - t1
    model_load = max(elapsed - warm - (t_model - t0), 0.0)

    record("1. warm pipeline under 15s", warm < MAX_PIPELINE_SECONDS,
           f"{warm:.1f}s warm, {elapsed:.1f}s cold, {len(cands)} ranked, "
           f"backend={eng.backend_name}")
    record("1b. model load", 
           PASS if model_load < SLOW_MODEL_LOAD_SECONDS else WARN,
           f"{model_load:.1f}s (hardware, paid once per process)")

    # ── 2. Parse coverage ────────────────────────────────────────────────────
    unreadable = [d for d in docs if not d.text.strip()]
    low = [d for d in docs if 0 < d.quality < 0.5]
    record("2. every resume parsed", not unreadable,
           f"{len(docs) - len(unreadable)}/{len(docs)} readable, {len(low)} low-quality")

    # ── 3. THE critical one: both channels load-bearing ──────────────────────
    kw = [c.doc_id for c in fusion.score(primitives, alpha=1.0)]
    sem = [c.doc_id for c in fusion.score(primitives, alpha=0.0)]
    moved = sum(1 for i, d in enumerate(kw) if sem.index(d) != i)
    biggest = max((abs(sem.index(d) - i) for i, d in enumerate(kw)), default=0)
    record("3. both channels change the ranking", kw != sem,
           f"{moved}/{len(kw)} candidates move, largest swing {biggest} places")

    # ── 4. Score spread ──────────────────────────────────────────────────────
    scores = [c.score for c in cands]
    spread = max(scores) - min(scores)
    record("4. score spread >= 35", spread >= MIN_SPREAD,
           f"{spread:.1f} points ({min(scores):.1f} to {max(scores):.1f})")

    # ── 5. Planted probes (synthetic corpus only) ────────────────────────────
    manifest_path = SYN / "manifest.json"
    if source.startswith("fixtures") and manifest_path.exists():
        manifest = {c["id"]: c for c in json.loads(manifest_path.read_text())["candidates"]}
        by_probe = {}
        for c in cands:
            probe = manifest.get(c.doc_id.split("_")[0], {}).get("probe")
            if probe:
                by_probe[probe] = c

        checks = []
        gem = by_probe.get("HIDDEN_GEM")
        checks.append(("hidden gem flagged", gem is not None and gem.flag == "HIDDEN_GEM"))
        surf = by_probe.get("SURFACE_MATCH")
        checks.append(("surface match flagged", surf is not None and surf.flag == "SURFACE_MATCH"))
        typo = by_probe.get("TYPO_CASE")
        checks.append(("typos resolved", typo is not None and typo.primitives.req_coverage > 0.8))
        empty = by_probe.get("NEAR_EMPTY")
        checks.append(("near-empty ranks last", empty is not None and empty.rank == len(cands)))

        failed = [n for n, ok in checks if not ok]
        record("5. planted probes recovered", not failed,
               "all 4 behaved" if not failed else f"failed: {', '.join(failed)}")
    else:
        record("5. planted probes recovered", WARN, "real corpus — probes not applicable")

    # ── 6. Explanation faithfulness ──────────────────────────────────────────
    texts = {d.doc_id: d.text for d in docs}
    bad_spans = 0
    unverifiable = 0
    for c in cands[:3]:
        for cell in c.primitives.cells:
            if cell.status != "MATCHED":
                continue
            if cell.start < 0:
                unverifiable += 1
                continue
            if texts[c.doc_id][cell.start:cell.end] != cell.evidence:
                bad_spans += 1
    record("6. top-3 matched claims traceable", bad_spans == 0,
           f"{bad_spans} bad spans, {unverifiable} without a citation")

    # ── 7. Slider parity ─────────────────────────────────────────────────────
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_parity.py")],
        capture_output=True, text=True, check=False,
    )
    record("7. rescore.js matches fusion.py", proc.returncode == 0,
           "exact at every alpha" if proc.returncode == 0 else "DRIFTED — see check_parity.py")

    # ── 8. Resilience fuzz ───────────────────────────────────────────────────
    crashed, handled = [], []
    for path in sorted(EDGE.glob(PDF_GLOB)):
        try:
            d = parser.extract(path)
            handled.append((path.name, d.quality, len(d.warnings)))
        except Exception as exc:                              # noqa: BLE001
            crashed.append(f"{path.name}: {type(exc).__name__}")
    record("8. malformed PDFs degrade gracefully", not crashed,
           f"{len(handled)} handled, 0 crashes" if not crashed else ", ".join(crashed))

    # ── 9. Chat intents ──────────────────────────────────────────────────────
    a, b = cands[0].name, cands[1].name
    probes = [
        (f"Why is {a} ranked above {b}?", "COMPARE"),
        (f"Why did {a} rank first?", "WHY"),
        (f"What is {b} missing?", "MISSING"),
        ("Who knows Docker?", "WHO_HAS"),
        ("Show me the top 5", "TOP_N"),
        ("what is the weather", "FALLBACK"),
    ]
    wrong = []
    for q, expected in probes:
        resp = chat.answer(q, cands, skill_set)
        if resp.intent != expected:
            wrong.append(f"{expected}->{resp.intent}")
        elif not resp.answer.strip():
            wrong.append(f"{expected}: empty")
    record("9. all six chat intents", not wrong,
           "6/6 routed and answered" if not wrong else ", ".join(wrong))

    # ── 10. No-LLM audit ─────────────────────────────────────────────────────
    banned = ("openai", "anthropic", "api_key", "api-key", "gpt-", "claude-",
              "cohere", "gemini", "huggingface_hub.InferenceClient")
    hits = []
    for py in (ROOT / "backend").rglob("*.py"):
        low = py.read_text(encoding="utf-8", errors="ignore").lower()
        for term in banned:
            if term in low:
                hits.append(f"{py.relative_to(ROOT)}:{term}")
    record("10. no LLM in the scoring path", not hits,
           "backend/ is clean" if not hits else ", ".join(hits))

    # ── Summary ──────────────────────────────────────────────────────────────
    failures = [r for r in results if r[1] == FAIL]
    warns = [r for r in results if r[1] == WARN]
    print(f"\n{len(results) - len(failures) - len(warns)}/{len(results)} passed", end="")
    if warns:
        print(f", {len(warns)} warning", end="")
    print()

    if failures:
        print("\nFAILED:")
        for name, _, detail in failures:
            print(f"  - {name}: {detail}")
        return 1

    print("\nAll acceptance checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
