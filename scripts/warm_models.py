"""
Pre-download and verify the embedding model.

Run this FIRST, in the background, before writing any other code. A cold
`SentenceTransformer(...)` call during a live demo means a multi-hundred-megabyte
download on venue wifi, which is the single most likely way this project fails.

    python scripts/warm_models.py
"""
from __future__ import annotations

import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import _console  # noqa: F401  (configures stdout encoding on import)

from backend import config


def main() -> int:
    print(f"device          : {config.DEVICE}")
    print(f"model           : {config.EMBED_MODEL}")
    print("downloading (first run may take a few minutes)...")

    t0 = time.perf_counter()
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(config.EMBED_MODEL, device=config.DEVICE)
    except Exception as exc:                      # noqa: BLE001 - we want every failure
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print("\nThe pipeline still runs. Set the offline fallback:")
        print("    export INTERLOOM_SEMANTIC=tfidf_svd")
        return 1

    load_s = time.perf_counter() - t0

    # Prove it actually embeds, and that the Express -> Node.js inference works.
    t1 = time.perf_counter()
    probe = [
        "Node.js server side development",
        "built REST APIs with Express and MongoDB",
        "finite element analysis in ANSYS for chassis components",
    ]
    emb = model.encode(probe, normalize_embeddings=True)
    encode_ms = (time.perf_counter() - t1) * 1000

    related = float(emb[0] @ emb[1])
    unrelated = float(emb[0] @ emb[2])

    print(f"\nloaded in       : {load_s:.1f}s")
    print(f"encode 3 texts  : {encode_ms:.0f}ms")
    print(f"dimensions      : {emb.shape[1]}")
    print("\nsanity check (the case the problem statement describes):")
    print(f"  'Node.js'  vs  'Express + MongoDB'   cos = {related:+.3f}")
    print(f"  'Node.js'  vs  'ANSYS chassis FEA'   cos = {unrelated:+.3f}")

    if related <= unrelated:
        print("\nWARNING: semantic channel is not discriminating. Investigate before relying on it.")
        return 1

    print(f"\n  margin = {related - unrelated:+.3f}  -> semantic channel is working")
    print("\nMODEL CACHED AND VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
