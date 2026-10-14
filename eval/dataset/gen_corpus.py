"""Ohm's-law eval corpus — every format x3 (media excepted), rich content inside.

    uv run python eval/dataset/gen_corpus.py [--seed 42] [--out eval/dataset/files]
    uv run python eval/dataset/fetch_media.py --out eval/dataset/files  # YouTube media (gitignored)

One topic (Ohm's law & electric circuits, see SOURCES.md), aligned with
the two pinned videos and their transcripts (raw/*.en.vtt):

  pdf      circuits.pdf (6pp, diagram image + V-I table) ·
           practical.pdf (4pp wiring guide + connection table) ·
           formulae.pdf (3pp + symbol table)
  docx     lab_notes.docx (sections + V-I table + diagram) ·
           worksheet.docx (problem table + diagram) · revision.docx (Q&A + symbol table)
  pptx     lesson.pptx (8 slides + diagram + V-I table + line chart) ·
           practical.pptx (5 slides + connection table) · revision.pptx (4 slides + table)
  xlsx     measurements.xlsx (V-I table + LineChart + Resistors) ·
           problems.xlsx (problems + answers) · components.xlsx (inventory + SUM total)
  csv      readings.csv · rheostat_run.csv · series_parallel.csv
  txt      glossary.txt · safety.txt · symbols.txt
  md       summary.md · revision.md · experiment_log.md
  png      circuit_diagram.png · triangle.png · hose.png
  media    ohms_law_video.mp4 + circuit_demo_audio.m4a (fetch_media, gitignored)

Same seed => same bytes. Unique MARKERS per locator keep
testset.v1.json assertions unambiguous.
"""

from __future__ import annotations

import argparse
import csv
import tempfile
import warnings
from pathlib import Path

import pymupdf

warnings.filterwarnings("ignore", message="Duplicate name.*")

SEED_DEFAULT = 42
PAGE_W, PAGE_H = 595, 842

# ---------------------------------------------------------------- text bank
CIRCUIT_PAGES = [
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
     "12 volts divided by 250 ohms gives 0.048 amps, which is 48 milliamps - all without using a meter. "
     "Connecting wire has a small resistance too, but here the total is treated as 250 ohms. MARKER-EXAMPLE-P5."),
    ("6. Series, parallel and the board practical",
     "An ammeter joins the loop in series so the full current passes through it. A voltmeter connects in parallel "
     "across the resistor only: red (positive) where current enters, black (negative) where it leaves. "
     "A rheostat in series varies the current for the experiment. Red terminals are positive, black are negative, "
     "and every connection must be tight. MARKER-PRACTICAL-P6."),
]

VI_ROWS = [("3.0", "12.0", "250"), ("6.0", "24.0", "250"),
           ("9.0", "36.0", "250"), ("12.0", "48.0", "250")]

# ---------------------------------------------------------------- pdf helpers
def _pdf_text_page(doc: pymupdf.Document, title: str, body: str) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_textbox(pymupdf.Rect(60, 60, PAGE_W - 60, 140), title,
                        fontsize=22, fontname="helv")
    page.insert_textbox(pymupdf.Rect(60, 150, PAGE_W - 60, 500), body,
                        fontsize=12, fontname="helv", align=0)


def _pdf_table(page: pymupdf.Page, top: float, headers: list[str],
               rows: list[tuple[str, ...]], col_w: float = 130.0) -> None:
    x0, n = 60.0, len(headers)
    y = top
    for j, htxt in enumerate(headers):
        page.draw_rect(pymupdf.Rect(x0 + j * col_w, y, x0 + (j + 1) * col_w, y + 26))
        page.insert_textbox(pymupdf.Rect(x0 + j * col_w + 4, y + 4,
                                        x0 + (j + 1) * col_w - 4, y + 24),
                            htxt, fontsize=11, fontname="hebo")
    y += 26
    for row in rows:
        for j, cell in enumerate(row):
            page.draw_rect(pymupdf.Rect(x0 + j * col_w, y, x0 + (j + 1) * col_w, y + 24))
            page.insert_textbox(pymupdf.Rect(x0 + j * col_w + 4, y + 3,
                                            x0 + (j + 1) * col_w - 4, y + 22),
                                str(cell), fontsize=11, fontname="helv")
        y += 24


def _pdf_image(page: pymupdf.Page, img: Path, rect: pymupdf.Rect) -> None:
    # JPEG temp keeps the PDF small (fitz stores PNG pixels raw).
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        Image.open(img).convert("RGB").save(tmp.name, quality=82)
        page.draw_rect(rect)
        page.insert_image(rect + pymupdf.Rect(2, 2, -2, -2), filename=tmp.name)
    Path(tmp.name).unlink(missing_ok=True)


def build_pdfs(out: Path, imgs: dict[str, Path]) -> None:
    doc = pymupdf.open()
    for i, (title, body) in enumerate(CIRCUIT_PAGES):
        _pdf_text_page(doc, title, body)
        page = doc[i]
        if i == 0:
            _pdf_image(page, imgs["circuit"], pymupdf.Rect(90, 510, 505, 720))
            page.insert_textbox(pymupdf.Rect(90, 725, 505, 760),
                                "Fig 1.1: battery-ammeter-resistor-rheostat loop, voltmeter in parallel.",
                                fontsize=10, fontname="helv")
        if i == 4:
            page.insert_textbox(pymupdf.Rect(60, 470, PAGE_W - 60, 500),
                                "Measured V-I pairs for R = 250 ohm:",
                                fontsize=12, fontname="hebo")
            _pdf_table(page, 505, ["voltage_V", "current_mA", "resistance_ohm"], VI_ROWS)
    doc.save(out / "circuits.pdf")

    doc = pymupdf.open()
    steps = [
        ("Step 1: battery to ammeter",
         "Connect the battery positive terminal to the ammeter positive (red) terminal. "
         "Red is positive, black is negative. Keep every connection tight. MARKER-WIRE-P1."),
        ("Step 2: ammeter to resistor to rheostat",
         "Join the ammeter negative terminal to one end of the unknown resistor, then through "
         "the rheostat back to the battery negative terminal. The rheostat varies the current. MARKER-WIRE-P2."),
        ("Step 3: voltmeter in parallel",
         "Connect the voltmeter across the resistor only: red (positive) where current enters, "
         "black (negative) where it leaves. A voltmeter is always in parallel. MARKER-WIRE-P3."),
        ("Step 4: read and record",
         "Trace the loop: battery+ to ammeter+ to resistor to rheostat to battery-. "
         "Vary the rheostat, record each voltmeter/ammeter pair, and check V/I stays constant. MARKER-WIRE-P4."),
    ]
    for title, body in steps:
        _pdf_text_page(doc, title, body)
    page = doc[2]
    page.insert_textbox(pymupdf.Rect(60, 420, PAGE_W - 60, 450),
                        "Connection table:", fontsize=12, fontname="hebo")
    _pdf_table(page, 455, ["from", "to", "wire"],
               [("battery +", "ammeter + (red)", "tight"),
                ("ammeter -", "resistor end A", "tight"),
                ("resistor end B", "rheostat", "tight"),
                ("rheostat", "battery -", "tight"),
                ("voltmeter red", "resistor end A", "parallel"),
                ("voltmeter black", "resistor end B", "parallel")],
               col_w=150.0)
    doc.save(out / "practical.pdf")

    doc = pymupdf.open()
    _pdf_text_page(doc, "Formula sheet: the triangle",
                   "Cover V for I x R. Cover I for V / R. Cover R for V / I. "
                   "Two known values always give the third. MARKER-FORM-P1.")
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_textbox(pymupdf.Rect(60, 60, PAGE_W - 60, 110),
                        "Symbols and units", fontsize=22, fontname="helv")
    _pdf_table(page, 130, ["symbol", "quantity", "unit"],
               [("V", "voltage", "volts"),
                ("I", "current", "amps"),
                ("R", "resistance", "ohms (omega)")])
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_textbox(pymupdf.Rect(60, 60, PAGE_W - 60, 110),
                        "One-minute problems", fontsize=22, fontname="helv")
    _pdf_table(page, 130, ["given", "find", "answer"],
               [("12V, 250 ohm", "I", "0.048 A = 48 mA"),
                ("6V, 250 ohm", "I", "0.024 A = 24 mA"),
                ("48mA, 250 ohm", "V", "12 V")])
    doc.save(out / "formulae.pdf")


# ---------------------------------------------------------------- images
def build_images(out: Path) -> dict[str, Path]:
    from PIL import Image, ImageDraw

    paths: dict[str, Path] = {}
    img = Image.new("RGB", (700, 420), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 580, 300], outline="black", width=3)
    d.text((60, 190), "BAT 12V", fill="black")
    d.text((300, 95), "A ammeter (series)", fill="black")
    d.text((590, 190), "R 250", fill="black")
    d.text((280, 310), "rheostat", fill="black")
    d.rectangle([250, 160, 450, 260], outline="black", width=2)
    d.text((255, 140), "V voltmeter (parallel)", fill="black")
    d.text((80, 40), "Circuit: battery-ammeter-resistor-rheostat loop, voltmeter in parallel MARKER-CIRCUIT-PNG", fill="black")
    paths["circuit"] = out / "circuit_diagram.png"
    img.save(paths["circuit"])

    img = Image.new("RGB", (500, 420), "white")
    d = ImageDraw.Draw(img)
    d.polygon([(250, 60), (90, 320), (410, 320)], outline="black", width=3)
    d.line([(170, 190), (330, 190)], fill="black", width=3)
    d.text((240, 110), "V", fill="black")
    d.text((180, 240), "I", fill="black")
    d.text((305, 240), "R", fill="black")
    d.text((60, 350), "Cover one letter to read its formula MARKER-TRIANGLE-PNG", fill="black")
    paths["triangle"] = out / "triangle.png"
    img.save(paths["triangle"])

    img = Image.new("RGB", (700, 360), "white")
    d = ImageDraw.Draw(img)
    for i, (label, sub) in enumerate([("PRESSURE", "voltage V"), ("VALVE", "resistor R"), ("FLOW", "current I")]):
        x = 40 + i * 220
        d.rectangle([x, 120, x + 180, 220], outline="black", width=3)
        d.text((x + 30, 140), label, fill="black")
        d.text((x + 30, 170), sub, fill="black")
        if i < 2:
            d.line([(x + 180, 170), (x + 220, 170)], fill="black", width=3)
    d.text((60, 40), "Garden hose analogy MARKER-HOSE-PNG", fill="black")
    paths["hose"] = out / "hose.png"
    img.save(paths["hose"])
    return paths


# ---------------------------------------------------------------- docx
def build_docx(out: Path, imgs: dict[str, Path]) -> None:
    from docx import Document
    from docx.shared import Inches

    sections = [
        ("Ohm's law", "Ohm's law states V = I x R: voltage equals current times resistance. "
                      "Knowing any two values lets you calculate the third. MARKER-OHM-DOCX."),
        ("Water analogy", "Voltage is water pressure, the valve is the resistor, flow is current. "
                          "Higher pressure or a more open valve raises the flow. MARKER-HOSE-DOCX."),
        ("Triangle method", "Cover V to get I x R; cover I to get V / R; cover R to get V / I. MARKER-TRIANGLE-DOCX."),
        ("Board practical wiring", "Connect battery positive to ammeter positive (red), ammeter negative to one end "
         "of the unknown resistor, the other end through the rheostat back to battery negative. "
         "Voltmeter goes in parallel across the resistor: red where current enters, black where it leaves. MARKER-WIRING-DOCX."),
    ]
    doc = Document()
    for i, (h, b) in enumerate(sections):
        doc.add_heading(h, 1 if i == 0 else 2)
        doc.add_paragraph(b)
    doc.add_heading("Measured V-I pairs (R = 250 ohm)", 2)
    table = doc.add_table(rows=5, cols=3)
    for j, htxt in enumerate(["voltage_V", "current_mA", "resistance_ohm"]):
        table.cell(0, j).text = htxt
    for i, row in enumerate([("3.0", "12.0", "250"), ("6.0", "24.0", "250"),
                             ("9.0", "36.0", "250"), ("12.0", "48.0", "250")], start=1):
        for j, cell in enumerate(row):
            table.cell(i, j).text = cell
    doc.add_paragraph("Figure: the board circuit.")
    doc.add_picture(str(imgs["circuit"]), width=Inches(5.5))
    doc.save(out / "lab_notes.docx")

    doc = Document()
    doc.add_heading("Worksheet: Ohm's law problems", 1)
    doc.add_paragraph("Use V = I x R. Show the triangle step, then the numbers. MARKER-SHEET-DOCX.")
    table = doc.add_table(rows=6, cols=3)
    for j, htxt in enumerate(["#", "given", "find"]):
        table.cell(0, j).text = htxt
    problems = [("1", "12V, 250 ohm", "I"), ("2", "6V, 250 ohm", "I"),
                ("3", "48mA, 250 ohm", "V"), ("4", "9V, 100 ohm", "I"), ("5", "24mA, 250 ohm", "V")]
    for i, row in enumerate(problems, start=1):
        for j, cell in enumerate(row):
            table.cell(i, j).text = cell
    doc.add_paragraph("Hint diagram: voltmeter in parallel.")
    doc.add_picture(str(imgs["circuit"]), width=Inches(5.5))
    doc.save(out / "worksheet.docx")

    doc = Document()
    doc.add_heading("Quick revision Q&A", 1)
    for q, a in [("What is V=IR?", "Voltage = current x resistance."),
                 ("Ammeter or voltmeter in series?", "Ammeter in series; voltmeter in parallel."),
                 ("12V across 250 ohm?", "0.048A = 48mA."),
                 ("Red or black positive?", "Red positive, black negative.")]:
        doc.add_heading(q, 2)
        doc.add_paragraph(a)
    doc.add_paragraph("MARKER-REVISION-DOCX.")
    table = doc.add_table(rows=4, cols=3)
    for j, htxt in enumerate(["symbol", "quantity", "unit"]):
        table.cell(0, j).text = htxt
    for i, row in enumerate([("V", "voltage", "volts"), ("I", "current", "amps"),
                             ("R", "resistance", "ohms")], start=1):
        for j, cell in enumerate(row):
            table.cell(i, j).text = cell
    doc.save(out / "revision.docx")


# ---------------------------------------------------------------- pptx
def _pptx_table(slide, headers: list[str], rows: list[tuple[str, ...]]) -> None:
    gfx = slide.shapes.add_table(len(rows) + 1, len(headers),
                                 int(72 * 800), int(72 * 2600),
                                 int(72 * 8000), int(72 * 400)).table
    for j, htxt in enumerate(headers):
        gfx.cell(0, j).text = htxt
    for i, row in enumerate(rows, start=1):
        for j, cell in enumerate(row):
            gfx.cell(i, j).text = cell


def build_pptx(out: Path, imgs: dict[str, Path]) -> None:
    from pptx import Presentation
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    slides = [
        ("Ohm's law in ~5 minutes", "Two videos, one topic. MARKER-PPT-TITLE. Theory + board practical wiring."),
        ("The basic circuit", "Battery, wire, resistor in a loop. MARKER-PPT-CIRCUIT. Current leaves + terminal, returns to battery."),
        ("Hose analogy", "Pressure = voltage, valve = resistor, flow = current. MARKER-PPT-HOSE."),
        ("V = I x R triangle", "Cover-and-read: V=I*R, I=V/R, R=V/I. MARKER-PPT-TRIANGLE."),
        ("Symbols and units", "V volts, R / ohm-sign ohms, I amps. MARKER-PPT-UNITS."),
        ("Worked example", "12V across 250 ohm gives 0.048A = 48mA. MARKER-PPT-EXAMPLE."),
        ("Series vs parallel", "Ammeter in series; voltmeter in parallel across the resistor. MARKER-PPT-SERIES."),
        ("Practical checklist", "Red positive, black negative, tight connections, rheostat in series. MARKER-PPT-WIRING."),
    ]
    prs = Presentation()
    blank = prs.slide_layouts[6]
    for title, body in slides:
        slide = prs.slides.add_slide(blank)
        tx = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(8), Inches(4.5)).text_frame
        tx.text = title
        tx.add_paragraph().text = body
    pic_slide = prs.slides[1]
    pic_slide.shapes.add_picture(str(imgs["circuit"]), Inches(0.8), Inches(3.4), width=Inches(5.0))
    _pptx_table(prs.slides[5], ["voltage_V", "current_mA"],
                [("3.0", "12.0"), ("6.0", "24.0"), ("9.0", "36.0"), ("12.0", "48.0")])
    chart_slide = prs.slides.add_slide(blank)
    tx = chart_slide.shapes.add_textbox(Inches(0.8), Inches(0.3), Inches(8), Inches(1)).text_frame
    tx.text = "V-I is a straight line through the origin"
    tx.add_paragraph().text = "Slope is 1/R. MARKER-PPT-CHART."
    cd = ChartData()
    cd.categories = ["3V", "6V", "9V", "12V"]
    cd.add_series("current_mA", [12.0, 24.0, 36.0, 48.0])
    chart_slide.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0.8), Inches(1.6),
                                 Inches(8), Inches(4.5), cd)
    prs.save(out / "lesson.pptx")

    steps = [
        ("Practical: goal", "Wire the Ohm's law board circuit in minutes. MARKER-PRAC-P1."),
        ("Battery to ammeter", "Battery+ to ammeter+ (red). Tight connections. MARKER-PRAC-P2."),
        ("Resistor and rheostat", "Ammeter- to resistor, resistor via rheostat to battery-. MARKER-PRAC-P3."),
        ("Voltmeter across R", "Red where current enters, black where it leaves. Parallel only. MARKER-PRAC-P4."),
        ("Read the loop", "Battery+ ammeter+ resistor rheostat battery-. Vary, record, check V/I. MARKER-PRAC-P5."),
    ]
    prs = Presentation()
    for title, body in steps:
        slide = prs.slides.add_slide(blank)
        tx = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(8), Inches(4.5)).text_frame
        tx.text = title
        tx.add_paragraph().text = body
    _pptx_table(prs.slides[3], ["from", "to"],
                [("battery +", "ammeter + (red)"), ("ammeter -", "resistor A"),
                 ("resistor B", "rheostat"), ("rheostat", "battery -"),
                 ("voltmeter red", "resistor A"), ("voltmeter black", "resistor B")])
    prs.slides[1].shapes.add_picture(str(imgs["circuit"]), Inches(0.8), Inches(3.4), width=Inches(5.0))
    prs.save(out / "practical.pptx")

    qa = [
        ("Revision: formulae", "V=I*R, I=V/R, R=V/I. MARKER-REV-P1."),
        ("Revision: units", "Volts, ohms, amps. MARKER-REV-P2."),
        ("Revision: example", "12V/250ohm = 48mA. MARKER-REV-P3."),
        ("Revision: wiring", "Ammeter series, voltmeter parallel. MARKER-REV-P4."),
    ]
    prs = Presentation()
    for title, body in qa:
        slide = prs.slides.add_slide(blank)
        tx = slide.shapes.add_textbox(Inches(0.8), Inches(0.6), Inches(8), Inches(4.5)).text_frame
        tx.text = title
        tx.add_paragraph().text = body
    _pptx_table(prs.slides[0], ["symbol", "unit"], [("V", "volts"), ("I", "amps"), ("R", "ohms")])
    prs.save(out / "revision.pptx")


# ---------------------------------------------------------------- xlsx / csv / txt / md
def build_xlsx(out: Path) -> None:
    import openpyxl
    from openpyxl.chart import LineChart, Reference

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "V-I Readings"
    for row in [("voltage_V", "current_mA", "resistance_ohm"),
                (3.0, 12.0, 250), (6.0, 24.0, 250),
                (9.0, 36.0, 250), (12.0, 48.0, 250)]:
        ws.append(list(row))
    chart = LineChart()
    chart.title = "V-I characteristic (R=250 ohm)"
    chart.y_axis.title = "current_mA"
    chart.x_axis.title = "voltage_V"
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=5), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
    ws.add_chart(chart, "E2")
    ws2 = wb.create_sheet("Resistors")
    for row in [("resistor", "value_ohm"), ("R1 unknown", 250), ("R2", 100), ("R3", 470)]:
        ws2.append(list(row))
    wb.save(out / "measurements.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Problems"
    for row in [("n", "given", "find"),
                (1, "12V, 250 ohm", "I"), (2, "6V, 250 ohm", "I"),
                (3, "48mA, 250 ohm", "V"), (4, "9V, 100 ohm", "I"), (5, "24mA, 250 ohm", "V")]:
        ws.append(list(row))
    ws2 = wb.create_sheet("Answers")
    for row in [("n", "answer"),
                (1, "0.048A = 48mA"), (2, "0.024A = 24mA"), (3, "12V"),
                (4, "0.09A = 90mA"), (5, "6V")]:
        ws2.append(list(row))
    wb.save(out / "problems.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventory"
    for row in [("part", "qty", "unit_cost"),
                ("resistor 250 ohm", 12, 4), ("ammeter", 3, 350),
                ("voltmeter", 3, 320), ("rheostat", 4, 180), ("battery 12V", 5, 220)]:
        ws.append(list(row))
    ws.append(["TOTAL", "=SUM(B2:B6)", "=SUM(C2:C6)"])
    wb.save(out / "components.xlsx")


def build_rest(out: Path) -> None:
    with (out / "readings.csv").open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([("voltage_V", "current_mA"), ("3.0", "12.0"),
                                  ("6.0", "24.0"), ("9.0", "36.0"), ("12.0", "48.0")])
    with (out / "rheostat_run.csv").open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([("setting", "current_mA"), ("low", "12.0"),
                                  ("mid", "30.0"), ("high", "48.0")])
    with (out / "series_parallel.csv").open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([("config", "resistance_ohm", "current_at_12V_mA"),
                                  ("single 250", "250", "48.0"),
                                  ("series 100+150", "250", "48.0"),
                                  ("parallel 500+500", "250", "48.0")])
    (out / "glossary.txt").write_text(
        "Glossary (MARKER-GLOSSARY-TXT)\n"
        "voltage: potential difference in volts (V).\n"
        "current: electron flow in amps (I).\n"
        "resistance: opposition in ohms (R, omega sign).\n"
        "ammeter: meter joined in series to read current.\n"
        "voltmeter: meter joined in parallel to read voltage.\n"
        "rheostat: variable resistor that sets the current.\n"
        "series: one loop, same current everywhere.\n"
        "parallel: shared endpoints, same voltage across branches.\n", encoding="utf-8")
    (out / "safety.txt").write_text(
        "Lab safety (MARKER-SAFETY-TXT)\n"
        "Keep connections tight; loose wires spark and spoil readings.\n"
        "Red is positive, black is negative - never reverse the battery.\n"
        "Start the rheostat at maximum resistance, then lower it slowly.\n", encoding="utf-8")
    (out / "symbols.txt").write_text(
        "Symbols (MARKER-SYMBOLS-TXT)\nV voltage volts. I current amps. R resistance ohms.\n"
        "A in a circle: ammeter. V in a circle: voltmeter. Zigzag: resistor.\n", encoding="utf-8")
    (out / "summary.md").write_text(
        "# Ohm's law summary (MARKER-SUMMARY-MD)\n\n"
        "- V = I x R; triangle: cover V (I*R), I (V/R), R (V/I).\n"
        "- Units: volts (V), ohms (R), amps (I).\n"
        "- Example: 12V / 250 ohm = 0.048A = 48mA.\n"
        "- Wiring: battery+ to ammeter+ (red), ammeter- to resistor, resistor via rheostat to battery-; "
        "voltmeter in parallel across the resistor.\n", encoding="utf-8")
    (out / "revision.md").write_text(
        "# Revision checklist (MARKER-REVISION-MD)\n\n"
        "- [ ] Write V=IR from the triangle without looking.\n"
        "- [ ] Convert 0.048A to milliamps (48mA).\n"
        "- [ ] Wire battery-ammeter-resistor-rheostat from memory.\n"
        "- [ ] Place the voltmeter in parallel, red where current enters.\n", encoding="utf-8")
    (out / "experiment_log.md").write_text(
        "# Experiment log (MARKER-LOG-MD)\n\n"
        "Run 1: rheostat low, 12V rail, readings match 250 ohm within meter tolerance.\n"
        "Run 2: series 100+150 behaves like single 250. V/I constant every run.\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument("--out", default="eval/dataset/files")
    args = ap.parse_args()
    _ = args.seed
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for stale in ("physics.pdf", "notes.docx", "slides.pptx", "costs.xlsx",
                  "diagram.png", "lecture.wav", "lecture.mp4"):
        p = out / stale
        if p.exists():
            p.unlink()

    imgs = build_images(out)
    build_pdfs(out, imgs)
    build_docx(out, imgs)
    build_pptx(out, imgs)
    build_xlsx(out)
    build_rest(out)
    print("note: media comes from fetch_media.py (gitignored): "
          "ohms_law_video.mp4 + circuit_demo_audio.m4a")
    for f in sorted(out.iterdir()):
        print(f"wrote {f.name} ({f.stat().st_size} bytes)")
    print("CORPUS READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
