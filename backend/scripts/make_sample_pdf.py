"""
Generate a 6-slide sample lecture PDF (linear algebra) for demos and tests.

    python scripts/make_sample_pdf.py sample_lecture.pdf
"""

from __future__ import annotations

import sys

import pymupdf

SLIDES = [
    ("1. Vectors", "A vector is an ordered list of numbers. Vectors can be added component-wise and scaled by real numbers. The set of all linear combinations of a collection of vectors is called their span."),
    ("2. Vector spaces", "A vector space is a set closed under addition and scalar multiplication. Important examples include R^n and the space of polynomials of degree at most n. A basis is a linearly independent spanning set."),
    ("3. Matrix operations", "Matrices are rectangular arrays of numbers. Matrix multiplication is associative but not commutative. The transpose swaps rows and columns. A square matrix is invertible if and only if its determinant is non-zero."),
    ("4. Determinants", "The determinant of a 2x2 matrix [[a, b], [c, d]] is ad - bc. Geometrically the determinant measures how a linear map scales area or volume. A zero determinant means the map collapses space onto a lower dimension."),
    ("5. Eigenvalues", "An eigenvector of a matrix A is a non-zero vector v such that Av = lambda v. The scalar lambda is the eigenvalue. Eigenvalues are the roots of the characteristic polynomial det(A - lambda I) = 0."),
    ("6. Diagonalisation", "A matrix with n linearly independent eigenvectors can be written as A = P D P^-1 where D is diagonal. Symmetric matrices always have real eigenvalues and orthogonal eigenvectors (spectral theorem)."),
]


def main(path: str) -> None:
    doc = pymupdf.open()
    for title, body in SLIDES:
        page = doc.new_page(width=842, height=595)  # 16:9-ish landscape slide
        page.insert_text((60, 90), title, fontsize=28, fontname="helv")
        writer = pymupdf.TextWriter(page.rect)
        writer.fill_textbox(pymupdf.Rect(60, 140, 780, 540), body, fontsize=18, font=pymupdf.Font("helv"))
        writer.write_text(page)
    doc.save(path)
    print(f"wrote {path} ({doc.page_count} pages)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_lecture.pdf")
