# Real-model evaluation (reproducible, tier-50 default)

Same data + same model + same settings => same numbers.

## Corpus — real videos + generated study files (one topic)

Topic: **Ohm's law & electric circuits** (see `dataset/SOURCES.md`).

```powershell
uv run python eval/dataset/gen_corpus.py --out eval/dataset/files
uv run python eval/dataset/fetch_media.py --out eval/dataset/files
```

`gen_corpus.py` writes `circuits.pdf` (6pp) · `lab_notes.docx` ·
`lesson.pptx` (8 slides) · `measurements.xlsx` · `readings.csv` ·
`glossary.txt` · `summary.md` · `circuit_diagram.png` — all aligned
with what the videos actually say (V=IR triangle, hose analogy,
12V/250Ω → 48mA example, board-practical wiring). `fetch_media.py`
restores the two pinned YouTube sources (theory `ohms_law_video.mp4`
4:42 + practical `circuit_demo_audio.m4a` 3:53, audio-only use).
Reference transcripts (`dataset/raw/*.en.vtt`) are committed; media is
gitignored (YouTube license + ~15MB) and fetched reproducibly.

## Config

```powershell
Copy-Item .env-test.example .env-test   # fill real keys
```

`eval/run_real.py` reads `.env-test` only to verify the running server
matches it (`/health` mismatch prints `[warn]`) and to stamp results.
The server itself keeps using root `.env` — keep both files identical
for a clean run.

## Run (tier 30 / 50 / 100, default 50)

```powershell
uv run uvicorn app.main:app --port 8000   # root .env = same models as .env-test
uv run python eval/run_real.py --env-file .env-test --tier 50
```

Uploads the corpus to two fresh projects, waits for ingestion, asks each
`eval/testset.v1.json` item in a fresh chat, and scores faithfulness
(LLM judge → keyword fallback, method per row), answer relevancy
(cosine), context precision/recall (locator-level), and refusal rate.
Gates: faithfulness ≥ 0.8, refusal == 100%.

Free-tier rate limits (e.g. 15 RPM) are respected by default: `--rpm 3`
paces ~3 questions/min (~12 LLM calls/min — each turn costs an answer +
title + classifier call plus the judge), uploads go in settled batches
of `--upload-batch 3`, and every chat call retries with backoff on
429/5xx. Tier 30 takes ~10 min at default pacing; raise `--rpm` on paid
tiers.

## Results

`eval/results/real-<model>-t<tier>-<date>.json` + `latest.json` (+
`latest.md` table for the README). Header records date, git SHA,
`EVAL_SEED`, model/embedding/transcription ids, server `/health`
snapshot, and a `corpus_sha` so drift is detectable.

Mock runs stay in `eval/run_ragas.py` / `eval/simulate_students.py`
(zero-key CI). This harness is the real-model number for the README.
