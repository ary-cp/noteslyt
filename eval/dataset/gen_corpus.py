"""Deterministic fake eval corpus — tier-50 default.

    uv run python eval/dataset/gen_corpus.py [--seed 42] [--out eval/dataset/files]

Writes every parser type in app/ingestion/registry.py:
  physics.pdf (6pp) | notes.docx | slides.pptx (8 slides) | costs.xlsx
  readings.csv | glossary.txt | summary.md | diagram.png | lecture.wav
  lecture.mp4 (only when ffmpeg is available, muxed from diagram.png + lecture.wav)

Same seed => same bytes. Unique MARKERS per locator (e.g. EIGENVEC-P5)
make testset.v1.json assertions unambiguous. Audio/video hold a real
16 kHz mono WAV so the pipeline accepts them; transcription content
depends on the STT model, so media items in the testset are flagged
`requires_media` and the runner skips them gracefully when the source
fails (tone placeholder) instead of failing the whole run. Drop a real
recorded lecture over lecture.wav/mp4 to enable those items.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path

SEED_DEFAULT = 42

PDF_SLIDES = [
    ("1. Vectors", "A vector is an ordered list of numbers. Vectors add component-wise. MARKER-VEC-P1. The span is all linear combinations."),
    ("2. Vector spaces", "A vector space is closed under addition and scalar multiplication. Examples R^n and polynomials degree <= n. A basis is a linearly independent spanning set. MARKER-BASIS-P2."),
    ("3. Matrix operations", "Matrices are rectangular arrays. Matrix multiplication is associative but NOT commutative. MARKER-COMM-P3. Transpose swaps rows and columns. Invertible iff determinant non-zero."),
    ("4. Determinants", "The determinant of a 2x2 matrix [[a,b],[c,d]] is ad - bc. MARKER-DET-P4. Geometrically it scales area/volume. Zero determinant collapses space."),
    ("5. Eigenvalues", "An eigenvector of A is non-zero v with Av = lambda v. MARKER-EIGENVEC-P5. Eigenvalues are roots of det(A - lambda I) = 0."),
    ("6. Diagonalisation", "A matrix with n independent eigenvectors factors as A = P D P^-1. MARKER-DIAG-P6. Symmetric matrices have real eigenvalues and orthogonal eigenvectors."),
]

DOCX_SECTIONS = [
    ("Thermodynamics", "Entropy is the measure of disorder in a system. MARKER-ENTROPY-DOCX."),
    ("First law", "Energy is conserved in every thermodynamic process. MARKER-FIRSTLAW-DOCX."),
    ("Heat engines", "A heat engine turns heat flow into work with efficiency below Carnot limit. MARKER-ENGINE-DOCX."),
]

PPTX_SLIDES = [
    ("Course overview", "Welcome to Physics 101. MARKER-PPT-OVERVIEW. Topics: vectors, energy, circuits."),
    ("Vectors recap", "Vector addition is component-wise. MARKER-PPT-VEC. Span and basis preview."),
    ("Energy", "Kinetic energy is 1/2 m v^2. MARKER-PPT-ENERGY. Conservation holds in closed systems."),
    ("Circuits", "Ohm's law V = I R. MARKER-PPT-OHM. Series resistances add."),
    ("Optics", "Snell's law n1 sin t1 = n2 sin t2. MARKER-PPT-SNELL."),
    ("Waves", "Wave speed v = f lambda. MARKER-PPT-WAVE."),
    ("Review", "Midterm covers vectors, energy, circuits. MARKER-PPT-REVIEW."),
    ("Next steps", "Homework due Friday. MARKER-PPT-HW."),
]

XLSX_ROWS = [("item", "cost"), ("steel", 120), ("coke", 80), ("labor", 200)]
CSV_ROWS = [("time_s", "voltage_V"), ("0", "1.5"), ("1", "1.4"), ("2", "1.2")]
TXT_BODY = """Glossary (MARKER-GLOSSARY-TXT)
vector: ordered list of numbers.
basis: independent spanning set.
eigenvector: Av = lambda v direction preserved.
entropy: disorder measure.
"""
MD_BODY = """# Study summary (MARKER-SUMMARY-MD)

- Determinant 2x2: ad - bc.
- Eigenvector satisfies Av = lambda v.
- Energy conserved (first law).
- Steel cost 120 in Operating Costs sheet.
"""


def _write_pdf(path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from make_sample_pdf import build_pdf  # noqa: E402

    import textwrap

    # Reuse the raw-PDF builder shape with our marker-rich slides.
    import make_sample_pdf as m

    m.SLIDES = [(t, b) for t, b in PDF_SLIDES]
    # textwrap import kept for parity; builder handles wrapping.
    _ = textwrap
    build_pdf(str(path))


def _write_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    for heading, body in DOCX_SECTIONS:
        doc.add_heading(heading, 1 if heading == "Thermodynamics" else 2)
        doc.add_paragraph(body)
    doc.save(path)


def _write_pptx(path: Path) -> None:
    from pptx import Presentation

    prs = Presentation()
    blank = prs.slide_layouts[6]
    for title, body in PPTX_SLIDES:
        slide = prs.slides.add_slide(blank)
        tx = slide.shapes.add_textbox(72 * 800, 72 * 600, 72 * 8000, 72 * 4500).text_frame
        tx.text = title
        tx.add_paragraph().text = body
    prs.save(path)


def _write_xlsx(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Operating Costs"
    for row in XLSX_ROWS:
        ws.append(list(row))
    ws2 = wb.create_sheet("Readings")
    ws2.append(["sensor", "value"])
    ws2.append(["temp_C", 21.5])
    wb.save(path)


def _write_csv(path: Path) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(CSV_ROWS)


def _write_text(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _write_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([80, 120, 300, 280], outline="black", width=3)
    d.ellipse([360, 120, 560, 280], outline="black", width=3)
    d.text((90, 60), "Force diagram F=ma MARKER-DIAGRAM-PNG", fill="black")
    d.text((90, 300), "Box mass 4kg on plane", fill="black")
    img.save(path)


def _write_wav(path: Path, seconds: int = 8) -> None:
    import math
    import struct

    rate = 16000
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(rate * seconds)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def _try_mp4(png: Path, wav: Path, mp4: Path) -> bool:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    try:
        subprocess.run(
            [ff, "-y", "-v", "error", "-loop", "1", "-i", str(png),
             "-i", str(wav), "-c:v", "libx264", "-t", "8", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(mp4)],
            check=True, timeout=120, capture_output=True,
        )
        return mp4.exists()
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument("--out", default="eval/dataset/files")
    args = ap.parse_args()
    _ = args.seed  # content is fixed; seed pins future shuffles + recorded in results.
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    _write_pdf(out / "physics.pdf")
    _write_docx(out / "notes.docx")
    _write_pptx(out / "slides.pptx")
    _write_xlsx(out / "costs.xlsx")
    _write_csv(out / "readings.csv")
    _write_text(out / "glossary.txt", TXT_BODY)
    _write_text(out / "summary.md", MD_BODY)
    _write_png(out / "diagram.png")
    _write_wav(out / "lecture.wav")
    if not _try_mp4(out / "diagram.png", out / "lecture.wav", out / "lecture.mp4"):
        print("note: ffmpeg unavailable — lecture.mp4 skipped (video items will skip at eval)")
    for f in sorted(out.iterdir()):
        print(f"wrote {f.name} ({f.stat().st_size} bytes)")
    print("CORPUS READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
