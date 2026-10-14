"""Deterministic Ohm's-law eval corpus — topic-anchored II real videos.

    uv run python eval/dataset/gen_corpus.py [--seed 42] [--out eval/dataset/files]
    uv run python eval/dataset/fetch_media.py --out eval/dataset/files  # YouTube media (gitignored)

Writes every parser type in app/ingestion/registry.py on ONE topic
(Ohm's law & electric circuits), aligned with the two pinned videos
(see SOURCES.md) and their committed transcripts (raw/*.en.vtt):

  circuits.pdf (6pp) | lab_notes.docx | lesson.pptx (8 slides)
  measurements.xlsx | readings.csv | glossary.txt | summary.md
  circuit_diagram.png | ohms_law_video.mp4 (fetch) | circuit_demo_audio.m4a (fetch)

Same seed => same bytes. Unique MARKERS per locator make
testset.v1.json assertions unambiguous. Media files come from
fetch_media.py and are gitignored (YouTube license + size).
"""

from __future__ import annotations

import argparse
from pathlib import Path

SEED_DEFAULT = 42

PDF_SLIDES = [
    ("1. The basic circuit",
     "The simplest circuit is a battery, some connecting wire and a resistor in a loop. "
     "The battery creates a potential difference (voltage) that pushes electrons through the wire. "
     "Current flows out of the positive terminal, through the resistor, and back to the battery. MARKER-CIRCUIT-P1."),
    ("2. The water-hose analogy",
     "Think of a garden water hose. Battery voltage is the water-supply pressure: higher pressure pushes harder. "
     "The outlet valve is the resistor: closing it reduces the flow. The water flow itself is the electric current. "
     "Increase resistance and flow drops; open the valve (less resistance) and flow rises. MARKER-HOSE-P2."),
    ("3. Ohm's law and the triangle",
     "Ohm's law links the three quantities: V = I x R. Draw it as a triangle with V on top and I, R below. "
     "Cover the value you want: cover V and you read I multiplied by R; cover I and you read V divided by R; "
     "cover R and you read V divided by I. MARKER-TRIANGLE-P3."),
    ("4. Units and symbols",
     "In engineering voltage is denoted V and measured in volts. Resistance is denoted R (or the omega sign) "
     "and measured in ohms. Current is denoted I and measured in amps. So V=IR reads volts = amps x ohms. MARKER-UNITS-P4."),
    ("5. Worked example: 12V across 250 ohms",
     "Take a 12 volt battery with a 250 ohm resistor across it. Cover I in the triangle: I = V / R. "
     "12 volts divided by 250 ohms gives 0.048 amps, which is 48 milliamps — all without using a meter. "
     "Connecting wire has a small resistance too, but here the total is treated as 250 ohms. MARKER-EXAMPLE-P5."),
    ("6. Series, parallel and the board practical",
     "An ammeter joins the loop in series so the full current passes through it. A voltmeter connects in parallel "
     "across the resistor only: red (positive) where current enters, black (negative) where it leaves. "
     "A rheostat in series varies the current for the experiment. Red terminals are positive, black are negative, "
     "and every connection must be tight. MARKER-PRACTICAL-P6."),
]

DOCX_SECTIONS = [
    ("Ohm's law", "Ohm's law states V = I x R: voltage equals current times resistance. "
                  "Knowing any two values lets you calculate the third. MARKER-OHM-DOCX."),
    ("Water analogy", "Voltage is water pressure, the valve is the resistor, flow is current. "
                      "Higher pressure or a more open valve raises the flow. MARKER-HOSE-DOCX."),
    ("Triangle method", "Cover V to get I x R; cover I to get V / R; cover R to get V / I. MARKER-TRIANGLE-DOCX."),
    ("Board practical wiring", "Connect battery positive to ammeter positive (red), ammeter negative to one end "
     "of the unknown resistor, the other end through the rheostat back to battery negative. "
     "Voltmeter goes in parallel across the resistor: red where current enters, black where it leaves. MARKER-WIRING-DOCX."),
]

PPTX_SLIDES = [
    ("Ohm's law in ~5 minutes", "Two videos, one topic. MARKER-PPT-TITLE. Theory + board practical wiring."),
    ("The basic circuit", "Battery, wire, resistor in a loop. MARKER-PPT-CIRCUIT. Current leaves + terminal, returns to battery."),
    ("Hose analogy", "Pressure = voltage, valve = resistor, flow = current. MARKER-PPT-HOSE."),
    ("V = I x R triangle", "Cover-and-read: V=I*R, I=V/R, R=V/I. MARKER-PPT-TRIANGLE."),
    ("Symbols and units", "V volts, R / ohm-sign ohms, I amps. MARKER-PPT-UNITS."),
    ("Worked example", "12V across 250 ohm gives 0.048A = 48mA. MARKER-PPT-EXAMPLE."),
    ("Series vs parallel", "Ammeter in series; voltmeter in parallel across the resistor. MARKER-PPT-SERIES."),
    ("Practical checklist", "Red positive, black negative, tight connections, rheostat in series. MARKER-PPT-WIRING."),
]

XLSX_VI = [("voltage_V", "current_mA", "resistance_ohm"),
           (3.0, 12.0, 250), (6.0, 24.0, 250), (9.0, 36.0, 250), (12.0, 48.0, 250)]
XLSX_RES = [("resistor", "value_ohm"), ("R1 unknown", 250), ("R2", 100), ("R3", 470)]
CSV_ROWS = [("voltage_V", "current_mA"), ("3.0", "12.0"), ("6.0", "24.0"), ("9.0", "36.0"), ("12.0", "48.0")]
TXT_BODY = """Glossary (MARKER-GLOSSARY-TXT)
voltage: potential difference in volts (V).
current: electron flow in amps (I).
resistance: opposition in ohms (R, omega sign).
ammeter: meter joined in series to read current.
voltmeter: meter joined in parallel to read voltage.
rheostat: variable resistor that sets the current.
series: one loop, same current everywhere.
parallel: shared endpoints, same voltage across branches.
"""
MD_BODY = """# Ohm's law summary (MARKER-SUMMARY-MD)

- V = I x R; triangle: cover V (I*R), I (V/R), R (V/I).
- Units: volts (V), ohms (R), amps (I).
- Example: 12V / 250 ohm = 0.048A = 48mA.
- Wiring: battery+ to ammeter+ (red), ammeter- to resistor, resistor via rheostat to battery-; voltmeter in parallel across the resistor.
"""
PNG_LABEL = "Circuit: battery-ammeter-resistor-rheostat loop, voltmeter in parallel MARKER-CIRCUIT-PNG"


def _write_pdf(path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import make_sample_pdf as m

    def _clean(s: str) -> str:
        return s.replace("\u2014", "-").replace("\u2013", "-").encode("latin-1", "replace").decode("latin-1")

    m.SLIDES = [(_clean(t), _clean(b)) for t, b in PDF_SLIDES]
    m.build_pdf(str(path))


def _write_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    for i, (heading, body) in enumerate(DOCX_SECTIONS):
        doc.add_heading(heading, 1 if i == 0 else 2)
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
    ws.title = "V-I Readings"
    for row in XLSX_VI:
        ws.append(list(row))
    ws2 = wb.create_sheet("Resistors")
    for row in XLSX_RES:
        ws2.append(list(row))
    wb.save(path)


def _write_csv(path: Path) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(CSV_ROWS)


def _write_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (700, 420), "white")
    d = ImageDraw.Draw(img)
    # loop: battery left, ammeter top, resistor right, rheostat bottom
    d.rectangle([120, 120, 580, 300], outline="black", width=3)
    d.text((60, 190), "BAT 12V", fill="black")
    d.text((300, 95), "A ammeter (series)", fill="black")
    d.text((590, 190), "R 250", fill="black")
    d.text((280, 310), "rheostat", fill="black")
    d.rectangle([250, 160, 450, 260], outline="black", width=2)  # voltmeter branch
    d.text((255, 140), "V voltmeter (parallel)", fill="black")
    d.text((80, 40), PNG_LABEL, fill="black")
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument("--out", default="eval/dataset/files")
    args = ap.parse_args()
    _ = args.seed
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Remove the previous synthetic corpus so the folder holds one topic.
    for stale in ("physics.pdf", "notes.docx", "slides.pptx", "costs.xlsx",
                  "diagram.png", "lecture.wav", "lecture.mp4"):
        p = out / stale
        if p.exists():
            p.unlink()

    _write_pdf(out / "circuits.pdf")
    _write_docx(out / "lab_notes.docx")
    _write_pptx(out / "lesson.pptx")
    _write_xlsx(out / "measurements.xlsx")
    _write_csv(out / "readings.csv")
    (out / "glossary.txt").write_text(TXT_BODY, encoding="utf-8")
    (out / "summary.md").write_text(MD_BODY, encoding="utf-8")
    _write_png(out / "circuit_diagram.png")
    print("note: media comes from fetch_media.py (gitignored): "
          "ohms_law_video.mp4 + circuit_demo_audio.m4a")
    for f in sorted(out.iterdir()):
        print(f"wrote {f.name} ({f.stat().st_size} bytes)")
    print("CORPUS READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
