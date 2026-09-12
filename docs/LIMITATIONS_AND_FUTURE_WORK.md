# Current Technical Gaps & Roadmap

*Written honestly, for the team's own planning and for answering a judge's
"what would you do with another week" question without getting caught
overclaiming something that isn't actually built.*

---

## What is missing / current boundaries

### Gazetteer coverage
Skill and requirement extraction depends on `backend/data/skill_gazetteer.json`,
a curated list of **~95 technical terms** (with aliases and cluster
assignments). The original architecture plan scoped this at roughly 350
entries; the shipped version is smaller because build time went toward
correctness and calibration (verified against a corpus with known-good
answers) rather than raw gazetteer breadth. The n-gram miner in `skills.py`
catches some capitalised technical-looking terms the gazetteer misses, but it
is deliberately conservative (it rejects anything that's a fragment of an
already-known skill, to avoid the phantom-requirement bug documented in
`docs/PROBLEM_SOLVING_AND_DECISIONS.md`), so it is not a substitute for
genuine coverage. **Practical effect:** a hyper-niche framework, a very new
tool, or an internal/proprietary technology name that isn't in the gazetteer
and doesn't get mined will not be recognised as a distinct required or
preferred skill — it simply won't appear as a row in the evidence matrix.

### Multi-lingual processing
The entire pipeline — the stopword list, the alias map, the gazetteer's
descriptive phrases, the section-header synonym table, and the sentence
embedding model itself — is built and tuned for **English-language resumes
and job descriptions only**. A resume written in another language would
still be extracted and chunked (the parser doesn't care what language the
text is in), but skill matching, section detection, and the semantic
similarity scores would all degrade, since none of the reference vocabulary
or the embedding model's training data assumptions hold outside English.

### Image-only scans
`parser.py`'s cascade (PyMuPDF fast path, pdfplumber layout fallback) extracts
**text layers** from a PDF; it does not perform optical character
recognition. A pure bitmap scan with no embedded text layer — a photographed
or flatbed-scanned resume saved straight to PDF — will fail both extraction
attempts and correctly degrade to a `quality=0.0` stub, flagged in the UI as
"no extractable text — likely a scanned image, manual review required," rather
than crashing or silently ranking that candidate as a zero-fit. That's the
honest, safe failure mode — but it is a failure mode, not a solution. There
is currently no OCR step (Tesseract or otherwise) anywhere in the pipeline.

### Complex spatial layouts / multi-column resumes
This one is worth stating precisely because an earlier version of the design
plan described a mitigation that was **not actually built**: the plan called
for "multi-column interleave detection via x-coordinate clustering" as a
resilience feature. The shipped parser does not do this. When `pdfplumber`'s
fallback path is used on a genuinely multi-column resume layout, it relies on
that library's own default text-extraction ordering, which can interleave
content from adjacent columns out of its intended reading order. In practice
this mostly affects chunk *coherence* rather than causing outright failure —
the resume still parses, still chunks, and evidence spans still resolve
correctly (a chunk is still a valid, real substring of the extracted text) —
but an interleaved chunk may read as a slightly garbled sentence rather than
the clean line a human would see on the page, which can occasionally weaken
the semantic-channel match for content that landed in a chunk boundary that
crossed a column break.

---

## Future roadmap

Roughly in the order we'd tackle them, cheapest/highest-value first:

1. **Local OCR fallback (Tesseract).** When the PyMuPDF/pdfplumber cascade
   both return near-empty text, rasterise the page and run it through a local
   Tesseract pass before giving up. Fully offline, no new external service,
   directly closes the "image-only scan" gap above. Natural next step because
   the failure mode it targets is already detected and reported — this just
   gives the pipeline one more thing to try before conceding.

2. **Gazetteer expansion via offline named-entity extraction.** Run a local
   NER pass (e.g. a small offline spaCy model, or a statistics-based
   candidate-term extractor) over a larger sample of real job descriptions to
   surface technical terms worth curating into the gazetteer, rather than
   hand-writing entries one at a time. The extraction itself would stay
   fully local and deterministic — this is about *growing the reference data*
   the deterministic matcher already uses, not introducing a model into the
   scoring path itself.

3. **True multi-column layout reconstruction.** Implement the x-coordinate
   bounding-box clustering that was originally planned: group text blocks by
   horizontal position before flattening to a reading order, so a two-column
   resume reconstructs in the order a human would actually read it rather
   than in whatever order the underlying PDF's content stream happens to list
   blocks.

4. **Cross-encoder re-ranking as an optional third signal.** The original
   architecture scoped `cross-encoder/ms-marco-MiniLM-L-6-v2` as a stretch
   goal — re-scoring only the top 8–10 candidates from the bi-encoder ranking
   with a slower, more accurate cross-encoder pass (candidate text and JD
   text encoded *together*, not separately, which generally improves ranking
   precision at the cost of speed). Scoped out of the initial build
   specifically because it only pays off once the primary two-channel signal
   is calibrated and trustworthy, which took priority. Would sit behind a
   config flag exactly as originally planned, applied only to a short list so
   the latency cost stays bounded.

5. **A larger, more diverse calibration corpus.** The τ-band calibration
   (`TAU_LO`/`TAU_HI` in `backend/config.py`) and the two channel-weighting
   constants (`K_WEIGHT_BM25`, `M_WEIGHT_DOCSIM`) were measured against one
   18-candidate synthetic corpus with known-good expected rankings. They
   transfer reasonably (the same distributional shape — matched-vs-absent
   cosine separation — should hold on other English-language technical
   resumes), but the honest position is that they are tuned against one
   dataset. Re-running `scripts/verify.py`'s spread and probe-recovery checks
   against the real judge-provided corpus, and adjusting the two config
   constants if needed, is the correct next step the moment that corpus is
   available — not a hypothetical, but a concrete to-do already called out in
   `README.md`.
