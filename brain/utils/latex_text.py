"""LaTeX math as plain Unicode text, for the terminal (the web page renders it with KaTeX).

The model writes formulas as $...$ and $$...$$ because the chat page renders them. A terminal
cannot, so `\\frac{FC}{P - VC}` would show as code noise. This turns the common school and
business notation into readable text: ``BEP = FC/(P − VC)``. Anything it does not know is left
as it was, minus the backslash. Code spans and fences are not touched.
"""

from __future__ import annotations

import re

_SYMBOLS = {
    r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\pm": "±", r"\mp": "∓", r"\le": "≤", r"\leq": "≤",
    r"\ge": "≥", r"\geq": "≥", r"\neq": "≠", r"\ne": "≠", r"\approx": "≈", r"\equiv": "≡", r"\infty": "∞",
    r"\to": "→", r"\rightarrow": "→", r"\Rightarrow": "⇒", r"\leftarrow": "←", r"\Leftrightarrow": "⇔",
    r"\sum": "Σ", r"\prod": "Π", r"\int": "∫", r"\partial": "∂", r"\nabla": "∇", r"\Delta": "Δ", r"\delta": "δ",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ", r"\pi": "π",
    r"\sigma": "σ", r"\Sigma": "Σ", r"\omega": "ω", r"\Omega": "Ω", r"\phi": "φ", r"\epsilon": "ε", r"\rho": "ρ",
    r"\%": "%", r"\$": "$", r"\{": "{", r"\}": "}", r"\,": " ", r"\;": " ", r"\:": " ", r"\!": "", r"\quad": "  ",
    r"\qquad": "    ", r"\ldots": "…", r"\dots": "…", r"\cdots": "⋯", r"\circ": "°", r"\degree": "°",
    r"\in": "∈", r"\subset": "⊂", r"\cup": "∪", r"\cap": "∩", r"\forall": "∀", r"\exists": "∃",
    r"\left": "", r"\right": "", r"\displaystyle": "",
}
_SUPER = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_BRACED = r"\{((?:[^{}]|\{[^{}]*\})*)\}"
_MATH_RE = re.compile(r"\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?![\s$])((?:\\\$|[^$\n])+?)(?<![\s\\])\$(?![\d$])")
_CODE_RE = re.compile(r"(^|\n) {0,3}(```+|~~~+)[\s\S]*?(?:\n {0,3}\2[ \t]*(?=\n|$)|$)|(`+)(?!`)[\s\S]*?[^`]\3(?!`)")


def _group(text: str) -> str:
    """Brackets around a fraction part only when it has more than one term."""
    stripped = text.strip()
    return stripped if re.fullmatch(r"[\w.,×·²³⁰¹⁴-⁹]+", stripped) else f"({stripped})"


def convert(tex: str) -> str:
    """One formula as Unicode text."""
    out = tex
    for _ in range(4):   # nested \frac and \sqrt, innermost first
        before = out
        out = re.sub(r"\\text\w*" + _BRACED, r"\1", out)
        out = re.sub(r"\\(?:mathrm|mathbf|mathit|operatorname|textbf|boldsymbol)" + _BRACED, r"\1", out)
        out = re.sub(r"\\[dt]?frac" + _BRACED + _BRACED, lambda m: f"{_group(m.group(1))}/{_group(m.group(2))}", out)
        out = re.sub(r"\\sqrt" + _BRACED, lambda m: f"√{_group(m.group(1))}", out)
        if out == before:
            break
    out = re.sub(r"\^" + _BRACED, lambda m: _sup(m.group(1)), out)
    out = re.sub(r"\^(\w)", lambda m: _sup(m.group(1)), out)
    out = re.sub(r"_" + _BRACED, lambda m: _sub(m.group(1)), out)
    out = re.sub(r"_(\d)", lambda m: _sub(m.group(1)), out)
    for name in sorted(_SYMBOLS, key=len, reverse=True):
        # A literal replacement: backslashes in the symbol are escaped so re.sub does not read them as groups.
        out = re.sub(re.escape(name) + r"(?![A-Za-z])", _SYMBOLS[name].replace("\\", "\\\\"), out)
    out = re.sub(r"\\([A-Za-z]+)", r"\1", out)          # an unknown command: its name, without the backslash
    out = out.replace("{", "").replace("}", "")
    out = re.sub(r"(?<=\S) - (?=\S)", " − ", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _sup(body: str) -> str:
    converted = body.translate(_SUPER)
    return converted if converted != body or not body else f"^({body})"


def _sub(body: str) -> str:
    converted = body.translate(_SUB)
    return converted if all(c in "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎" for c in converted) else f"_{body}"


def to_text(markdown: str) -> str:
    """Every $...$ and $$...$$ in a Markdown answer as Unicode. Display formulas get their own line."""
    if "$" not in markdown and "\\(" not in markdown and "\\[" not in markdown:
        return markdown

    def swap(chunk: str) -> str:
        def one(m: re.Match[str]) -> str:
            block, bracket, paren, inline = m.groups()
            tex = block if block is not None else bracket if bracket is not None else paren if paren is not None else inline
            if not tex or not tex.strip():
                return m.group(0)
            text = convert(tex)
            # Bold on its own line: four spaces of indent would turn it into a code block.
            return f"\n\n**{text}**\n\n" if block is not None or bracket is not None else text
        return _MATH_RE.sub(one, chunk)

    out, last = [], 0
    for m in _CODE_RE.finditer(markdown):
        out.append(swap(markdown[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(swap(markdown[last:]))
    return "".join(out)


__all__ = ["convert", "to_text"]
