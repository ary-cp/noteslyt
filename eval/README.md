# Real-model evaluation (reproducible, tier-50 default)

Same data + same model + same settings => same numbers.

## Corpus

Deterministic fake course generated from a seed — every parser in
`app/ingestion/registry.py`:

```powershell
uv run python eval/dataset/gen_corpus.py --out eval/dataset/files
```

Writes `physics.pdf` (6pp) · `notes.docx` · `slides.pptx` (8 slides) ·
`costs.xlsx` · `readings.csv` · `glossary.txt` · `summary.md` ·
`diagram.png` · `lecture.wav` (+ `lecture.mp4` when `ffmpeg` exists).
Unique `MARKER-*` strings per locator make assertions unambiguous.
`lecture.wav` is a valid tone placeholder: ingestion accepts it, but STT
yields no speech, so `requires_media` items skip gracefully instead of
failing the run. Drop a real recorded lecture over it to enable them.

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

## Results

`eval/results/real-<model>-t<tier>-<date>.json` + `latest.json` (+
`latest.md` table for the README). Header records date, git SHA,
`EVAL_SEED`, model/embedding/transcription ids, server `/health`
snapshot, and a `corpus_sha` so drift is detectable.

Mock runs stay in `eval/run_ragas.py` / `eval/simulate_students.py`
(zero-key CI). This harness is the real-model number for the README.
