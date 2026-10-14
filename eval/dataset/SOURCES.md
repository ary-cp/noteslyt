# Eval dataset sources — Ohm's law & electric circuits

## Generated study files (`files/`, committed — 3 of each type)

Rich content inside: every PDF carries tables and `circuits.pdf` embeds
the circuit diagram; every DOCX carries tables and two embed the
diagram; every PPTX carries tables/pictures and `lesson.pptx` adds a
V-I line chart; `measurements.xlsx` adds a LineChart and
`components.xlsx` uses SUM formulas.

pdf `circuits.pdf` (6pp) · `practical.pdf` (4pp wiring guide) ·
`formulae.pdf` (3pp) — docx `lab_notes.docx` · `worksheet.docx` ·
`revision.docx` — pptx `lesson.pptx` (9 slides) · `practical.pptx` (5) ·
`revision.pptx` (4) — xlsx `measurements.xlsx` · `problems.xlsx` ·
`components.xlsx` — csv `readings.csv` · `rheostat_run.csv` ·
`series_parallel.csv` — txt `glossary.txt` · `safety.txt` ·
`symbols.txt` — md `summary.md` · `revision.md` · `experiment_log.md`
— png `circuit_diagram.png` · `triangle.png` · `hose.png`.

## Videos (fetched at eval time, not redistributed)

| # | Title | Channel | Duration | ID | Corpus name |
|---|-------|---------|----------|----|-------------|
| 1 | What Is OHM'S Law? [Explained in Under 5 Minutes] | Instrumentation & Control | 4:42 | `uZ-m91IEkjQ` | `ohms_law_video.mp4` (video) |
| 2 | HOW TO CONNECT OHM'S LAW CIRCUIT IN 4 MINS \| BOARD PRACTICAL | Make Me Scientific | 3:53 | `9WB82CvGIa8` | `circuit_demo_audio.m4a` (audio-only) |

```powershell
uv run python eval/dataset/fetch_media.py --out eval/dataset/files
```

Reference transcripts (`raw/*.en.vtt`, auto-captions, committed) were
used to align the generated files and questions with what the videos
actually say. Key facts: hose analogy (voltage=pressure, valve=resistor,
flow=current), V=IR triangle (cover-and-read), notation V/volts ·
R,Ω/ohms · I/amps, worked example 12V + 250Ω → 0.048A = 48mA,
practical wiring battery→ammeter→resistor→rheostat→battery with the
voltmeter in parallel across the resistor.

Media files (`*.mp4`, `*.m4a`) are gitignored; everything else in
`files/` is committed. YouTube content remains property of its owners
and is downloaded locally for evaluation only.
