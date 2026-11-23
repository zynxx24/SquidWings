"""Formulas the model writes in LaTeX read as plain text in the terminal."""

from utils.latex_text import convert, to_text


def test_school_and_business_formulas_become_readable() -> None:
    assert convert(r"\text{BEP (unit)} = \frac{FC}{P - VC}") == "BEP (unit) = FC/(P − VC)"
    assert convert(r"\text{BEP (Rp)} = \frac{FC}{1 - \frac{VC}{P}}") == "BEP (Rp) = FC/(1 − VC/P)"
    assert convert(r"x^2 + y_1 \times 3 \le \sqrt{a+b}") == "x² + y₁ × 3 ≤ √(a+b)"


def test_only_math_is_converted() -> None:
    text = to_text("Harga $5 dan $10, kode `$x$`, rumus $P \\times Q$")
    assert text == "Harga $5 dan $10, kode `$x$`, rumus P × Q"
    assert "**FC/(P − VC)**" in to_text("BEP: $$\\frac{FC}{P - VC}$$")
