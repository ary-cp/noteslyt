"""
Generate a 6-slide sample lecture PDF (linear algebra) for demos and tests.

    python scripts/make_sample_pdf.py sample_lecture.pdf
"""

from __future__ import annotations

import sys
import textwrap

SLIDES = [
    ("1. Vectors", "A vector is an ordered list of numbers. Vectors can be added component-wise and scaled by real numbers. The set of all linear combinations of a collection of vectors is called their span."),
    ("2. Vector spaces", "A vector space is a set closed under addition and scalar multiplication. Important examples include R^n and the space of polynomials of degree at most n. A basis is a linearly independent spanning set."),
    ("3. Matrix operations", "Matrices are rectangular arrays of numbers. Matrix multiplication is associative but not commutative. The transpose swaps rows and columns. A square matrix is invertible if and only if its determinant is non-zero."),
    ("4. Determinants", "The determinant of a 2x2 matrix [[a, b], [c, d]] is ad - bc. Geometrically the determinant measures how a linear map scales area or volume. A zero determinant means the map collapses space onto a lower dimension."),
    ("5. Eigenvalues", "An eigenvector of a matrix A is a non-zero vector v such that Av = lambda v. The scalar lambda is the eigenvalue. Eigenvalues are the roots of the characteristic polynomial det(A - lambda I) = 0."),
    ("6. Diagonalisation", "A matrix with n linearly independent eigenvectors can be written as A = P D P^-1 where D is diagonal. Symmetric matrices always have real eigenvalues and orthogonal eigenvectors (spectral theorem)."),
]

WIDTH, HEIGHT = 842, 595


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _page_content(title: str, body: str) -> bytes:
    lines = [f"BT /F1 28 Tf 60 {HEIGHT - 90} Td ({_escape(title)}) Tj ET"]
    y = HEIGHT - 140
    for wrap in textwrap.wrap(body, width=70):
        lines.append(f"BT /F1 16 Tf 60 {y} Td ({_escape(wrap)}) Tj ET")
        y -= 24
    return "\n".join(lines).encode("latin-1")


def build_pdf(path: str) -> int:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add(b"")  # placeholder, filled later
    page_ids = []
    for title, body in SLIDES:
        content = _page_content(title, body)
        content_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
        page = b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" % (
            pages_id, WIDTH, HEIGHT, font_id, content_id)
        page_ids.append(add(page))
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog_id, xref_pos)

    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return len(page_ids)


def main(path: str) -> None:
    n = build_pdf(path)
    print(f"wrote {path} ({n} pages)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_lecture.pdf")
