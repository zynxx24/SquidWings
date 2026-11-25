"""Exact arithmetic, statistics, number theory, and school maths."""

from __future__ import annotations

import ast
import math
import operator
import re
import statistics
from collections.abc import Callable
from fractions import Fraction
from typing import Any

from .catalog import group, param, spec

group("math", "Math and statistics",
      "hitung, hitungkan, berapa, kalkulasi, calculate, calc, compute, matematika, math, rumus, formula, "
      "statistik, statistics, rata-rata, average, mean, median, modus, persen, percent, persentase, akar, "
      "pangkat, kuadrat, faktorial, prima, kpk, fpb, gcd, lcm, pecahan, fraction, matriks, matrix, "
      "luas, keliling, volume, ipk, gpa, nilai")

_MAX_POW = 10_000
_MAX_FACT = 1_000
_MAX_INT_DIGITS = 4_000

_BIN: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY: dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CMP: dict[type, Callable[[Any, Any], Any]] = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}


def _fact(n: Any) -> int:
    if int(n) != n or n < 0 or n > _MAX_FACT:
        raise ValueError(f"factorial needs a whole number from 0 to {_MAX_FACT}")
    return math.factorial(int(n))


_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt, "akar": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "atan2": math.atan2, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "sind": lambda d: math.sin(math.radians(d)), "cosd": lambda d: math.cos(math.radians(d)),
    "tand": lambda d: math.tan(math.radians(d)),
    "log": lambda x, b=None: math.log(x) if b is None else math.log(x, b), "ln": math.log,
    "log10": math.log10, "log2": math.log2, "exp": math.exp, "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc, "min": min, "max": max,
    "sum": lambda *a: sum(a), "factorial": _fact, "fact": _fact, "comb": math.comb, "perm": math.perm,
    "gcd": math.gcd, "lcm": math.lcm, "hypot": math.hypot, "radians": math.radians, "degrees": math.degrees,
    "mean": lambda *a: statistics.fmean(a), "median": lambda *a: statistics.median(a),
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "phi": (1 + 5 ** 0.5) / 2, "inf": math.inf}


def _normalise(expression: str) -> str:
    text = expression.strip()
    for old, new in (("×", "*"), ("÷", "/"), ("−", "-"), ("–", "-"), ("^", "**"), ("√", "sqrt"),
                     ("π", "pi"), (" x ", " * "), (" X ", " * "), (" : ", " / ")):
        text = text.replace(old, new)
    # 15% -> (15/100), but "10 % 3" stays modulo.
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(.])", r"(\1/100)", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*!", r"factorial(\1)", text)
    # 2(3+4) and 2pi -> explicit multiplication.
    text = re.sub(r"(\d)\s*(\(|pi\b|e\b|sqrt|phi\b)", r"\1*\2", text)
    text = re.sub(r"\)\s*\(", ")*(", text)
    return text


class _Eval:
    def __init__(self, exact: bool) -> None:
        self.exact = exact

    def num(self, value: Any) -> Any:
        if self.exact and isinstance(value, (int, float)) and not isinstance(value, bool):
            return Fraction(value) if isinstance(value, int) else Fraction(str(value))
        return value

    def __call__(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return self.num(node.value)
        if isinstance(node, ast.Name):
            if node.id in _CONSTS:
                if self.exact:
                    raise _NotExactError
                return _CONSTS[node.id]
            raise ValueError(f"unknown name '{node.id}'")
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](self(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = self(node.left), self(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > _MAX_POW:
                    raise ValueError("exponent too large")
                if self.exact and (not isinstance(right, Fraction) or right.denominator != 1):
                    raise _NotExactError
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ValueError("division by zero")
            result = _BIN[type(node.op)](left, right)
            if isinstance(result, int) and result.bit_length() > _MAX_INT_DIGITS * 3.33:
                raise ValueError("result too large")
            return result
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
            return _CMP[type(node.ops[0])](self(node.left), self(node.comparators[0]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            if self.exact:
                raise _NotExactError
            args = [self(a) for a in node.args]
            return _FUNCS[node.func.id](*args)
        if isinstance(node, (ast.Tuple, ast.List)):
            return [self(e) for e in node.elts]
        raise ValueError(f"unsupported syntax: {ast.dump(node)[:60]}")


class _NotExactError(Exception):
    pass


def _plain(value: Any) -> Any:
    if isinstance(value, Fraction):
        return value.numerator if value.denominator == 1 else float(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return int(value)
        return float(f"{value:.12g}")
    return value


def evaluate(expression: str) -> dict[str, Any]:
    text = _normalise(expression)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"cannot read the expression '{expression}' ({exc.msg})") from exc
    out: dict[str, Any] = {"expression": expression}
    try:
        exact = _Eval(True)(tree)
        if isinstance(exact, Fraction):
            out["result"] = _plain(exact)
            if exact.denominator != 1:
                out["fraction"] = f"{exact.numerator}/{exact.denominator}"
            return out
    except _NotExactError:
        pass
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError(str(exc)) from exc
    try:
        value = _Eval(False)(tree)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError(str(exc)) from exc
    out["result"] = [_plain(v) for v in value] if isinstance(value, list) else _plain(value)
    return out


@spec("calc", "Evaluate a math expression exactly: + - * / // % ^, 15%, 5!, sqrt, sin/cos/tan (radians; sind for degrees), "
      "log, ln, comb, perm, gcd, lcm, pi, e. Fractions stay exact (1/3+1/6 = 1/2). Use it for every sum instead of mental math.",
      "math", [param("expression", description="e.g. '(12.5*4)^2 / 3' or '15% * 2000000'")],
      keywords="calc, kalkulator, calculator, hitung, hitungkan, kalkulasi, calculate, jumlahkan, dikali, dibagi, sum of", lead=True)
def calc(expression: str) -> dict[str, Any]:
    return evaluate(expression)


@spec("calc_many", "Evaluate several math expressions at once, in order. Returns each result or its error.",
      "math", [param("expressions", "list", "List of expressions.")], keywords="banyak soal, several sums")
def calc_many(expressions: list[str]) -> dict[str, Any]:
    results = []
    for expression in expressions[:100]:
        try:
            results.append(evaluate(str(expression)))
        except ValueError as exc:
            results.append({"expression": expression, "error": str(exc)})
    return {"results": results}


def _numbers(values: Any) -> list[float]:
    if isinstance(values, str):
        values = re.split(r"[\s;,]+", values.strip())
    out = []
    for v in values:
        if v in ("", None):
            continue
        out.append(float(str(v).replace(",", ".")) if isinstance(v, str) and v.count(",") == 1 and "." not in v else float(v))
    if not out:
        raise ValueError("give at least one number")
    return out


@spec("stats_describe", "Descriptive statistics of a list of numbers: count, sum, mean, median, mode, min, max, range, "
      "variance and standard deviation (sample and population), quartiles, IQR.",
      "math", [param("values", "list", "Numbers, or a string like '4 8 15 16 23 42'.", items="num")],
      keywords="statistik, statistics, rata-rata, rerata, mean, median, modus, mode, standar deviasi, std, varians, "
               "variance, kuartil, quartile, describe", lead=True)
def stats_describe(values: Any) -> dict[str, Any]:
    nums = sorted(_numbers(values))
    n = len(nums)
    out: dict[str, Any] = {"count": n, "sum": _plain(math.fsum(nums)), "mean": _plain(statistics.fmean(nums)),
                           "median": _plain(statistics.median(nums)), "min": _plain(nums[0]),
                           "max": _plain(nums[-1]), "range": _plain(nums[-1] - nums[0])}
    modes = statistics.multimode(nums)
    out["mode"] = [_plain(m) for m in modes] if len(modes) < n else []
    if n > 1:
        out["variance_sample"] = _plain(statistics.variance(nums))
        out["stdev_sample"] = _plain(statistics.stdev(nums))
        out["variance_population"] = _plain(statistics.pvariance(nums))
        out["stdev_population"] = _plain(statistics.pstdev(nums))
        q = statistics.quantiles(nums, n=4, method="inclusive")
        out["q1"], out["q3"] = _plain(q[0]), _plain(q[2])
        out["iqr"] = _plain(q[2] - q[0])
    return out


@spec("linear_regression", "Fit y = a + b*x by least squares. Returns slope, intercept, r, r squared, and optional predictions.",
      "math", [param("x", "list", "x values.", items="num"), param("y", "list", "y values, same length.", items="num"),
               param("predict", "list", "x values to predict y for.", required=False, items="num")],
      keywords="regresi, regression, korelasi, correlation, trend, tren, garis lurus, prediksi")
def linear_regression(x: list[float], y: list[float], predict: list[float] | None = None) -> dict[str, Any]:
    xs, ys = _numbers(x), _numbers(y)
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("x and y need the same length, at least 2")
    slope, intercept = statistics.linear_regression(xs, ys)
    r = statistics.correlation(xs, ys) if len(set(xs)) > 1 and len(set(ys)) > 1 else 0.0
    out: dict[str, Any] = {"slope": _plain(slope), "intercept": _plain(intercept), "r": _plain(r), "r_squared": _plain(r * r),
                           "equation": f"y = {_plain(intercept)} + {_plain(slope)}x"}
    if predict:
        out["predictions"] = [{"x": _plain(v), "y": _plain(intercept + slope * v)} for v in _numbers(predict)]
    return out


@spec("percent", "Percentage helpers: 'of' (p% of x), 'change' (from a to b), 'ratio' (a is what % of b), "
      "'add' (x plus p%), 'subtract' (x minus p%), 'reverse' (x is the value after +p%, find the original).",
      "math", [param("mode", enum=["of", "change", "ratio", "add", "subtract", "reverse"]),
               param("a", "num", "First number (x, or the 'from' value)."), param("b", "num", "Second number (p, or the 'to' value).")],
      keywords="persen, persentase, percent, percentage, naik, turun, kenaikan, penurunan, diskon, growth", lead=True)
def percent(mode: str, a: float, b: float) -> dict[str, Any]:
    mode = mode.lower()
    if mode == "of":
        return {"result": _plain(a * b / 100), "explain": f"{b}% of {a}"}
    if mode == "change":
        if a == 0:
            raise ValueError("cannot compute a change from 0")
        return {"result": _plain((b - a) / abs(a) * 100), "unit": "%", "explain": f"change from {a} to {b}"}
    if mode == "ratio":
        if b == 0:
            raise ValueError("b must not be 0")
        return {"result": _plain(a / b * 100), "unit": "%", "explain": f"{a} as a share of {b}"}
    if mode == "add":
        return {"result": _plain(a * (1 + b / 100)), "explain": f"{a} + {b}%"}
    if mode == "subtract":
        return {"result": _plain(a * (1 - b / 100)), "explain": f"{a} - {b}%"}
    if mode == "reverse":
        return {"result": _plain(a / (1 + b / 100)), "explain": f"original before +{b}% that gives {a}"}
    raise ValueError("mode must be of, change, ratio, add, subtract or reverse")


def _factorise(n: int) -> dict[int, int]:
    factors: dict[int, int] = {}
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors[d] = factors.get(d, 0) + 1
            n //= d
        d += 1 if d == 2 else 2
    if n > 1:
        factors[n] = factors.get(n, 0) + 1
    return factors


@spec("number_theory", "Facts about whole numbers: is_prime, prime factors, divisors, gcd (FPB) and lcm (KPK) of a list, "
      "next/previous prime, fibonacci n, perfect number check.",
      "math", [param("numbers", "list", "One or more whole numbers.", items="int"),
               param("operation", enum=["factorize", "is_prime", "divisors", "gcd", "lcm", "next_prime", "fibonacci", "all"],
                 required=False, default="all")],
      keywords="prima, prime, faktor, faktorisasi, factorize, pembagi, divisor, fpb, kpk, gcd, lcm, fibonacci", lead=True)
def number_theory(numbers: list[int], operation: str = "all") -> dict[str, Any]:
    nums = [int(float(n)) for n in (numbers if isinstance(numbers, list) else [numbers])]
    if not nums:
        raise ValueError("give at least one whole number")
    if any(abs(n) > 10 ** 13 for n in nums):
        raise ValueError("numbers above 10^13 are too slow to factor here")
    op = operation.lower()
    out: dict[str, Any] = {}
    if op in ("gcd", "lcm", "all") and len(nums) > 1:
        out["gcd_fpb"] = math.gcd(*nums)
        out["lcm_kpk"] = math.lcm(*nums)
    if op == "fibonacci":
        n = nums[0]
        if not 0 <= n <= 1000:
            raise ValueError("fibonacci index from 0 to 1000")
        a, b = 0, 1
        for _ in range(n):
            a, b = b, a + b
        return {"n": n, "fibonacci": a}
    per = []
    for n in nums:
        item: dict[str, Any] = {"n": n}
        m = abs(n)
        factors = _factorise(m) if m > 1 else {}
        if op in ("is_prime", "all", "factorize"):
            item["is_prime"] = m > 1 and factors == {m: 1}
        if op in ("factorize", "all") and m > 1:
            item["factors"] = " × ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in factors.items())
        if op in ("divisors", "all") and 0 < m <= 10 ** 9:
            divs = sorted({d for i in range(1, math.isqrt(m) + 1) if m % i == 0 for d in (i, m // i)})
            item["divisors"] = divs[:200]
            item["is_perfect"] = sum(divs) - m == m
        if op == "next_prime":
            k = m + 1
            while _factorise(k) != {k: 1}:
                k += 1
            item["next_prime"] = k
        per.append(item)
    out["numbers"] = per
    return out


@spec("base_convert", "Convert an integer between bases 2-36 (binary, octal, decimal, hex), and show it in the common bases.",
      "math", [param("value", description="The number as text, e.g. 'ff', '0b1010', '255'."),
               param("from_base", "int", "Base of value (default 10; prefixes 0x/0b/0o are detected).", required=False, default=10),
               param("to_base", "int", "Target base (default 2).", required=False, default=2)],
      keywords="biner, binary, heksadesimal, hexadecimal, hex, oktal, octal, desimal, decimal, basis bilangan, base")
def base_convert(value: str, from_base: int = 10, to_base: int = 2) -> dict[str, Any]:
    text = str(value).strip().lower().replace("_", "")
    negative = text.startswith("-")
    text = text.lstrip("-")
    for prefix, base in (("0x", 16), ("0b", 2), ("0o", 8)):
        if text.startswith(prefix):
            text, from_base = text[2:], base
    n = int(text, int(from_base))
    if negative:
        n = -n
    if not 2 <= int(to_base) <= 36:
        raise ValueError("to_base must be 2..36")
    return {"decimal": n, "result": _to_base(n, int(to_base)), "binary": bin(n), "octal": oct(n), "hex": hex(n)}


def _to_base(n: int, base: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    sign, n = ("-", -n) if n < 0 else ("", n)
    out = []
    while n:
        n, r = divmod(n, base)
        out.append(digits[r])
    return sign + "".join(reversed(out))


_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
          (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


@spec("roman_numeral", "Convert between Arabic numbers (1-3999) and Roman numerals, either direction.",
      "math", [param("value", description="e.g. '2026' or 'MMXXVI'.")], keywords="romawi, roman, angka romawi")
def roman_numeral(value: str) -> dict[str, Any]:
    text = str(value).strip().upper()
    if text.isdigit():
        n = int(text)
        if not 1 <= n <= 3999:
            raise ValueError("Roman numerals cover 1..3999")
        out, rest = [], n
        for v, s in _ROMAN:
            while rest >= v:
                out.append(s)
                rest -= v
        return {"arabic": n, "roman": "".join(out)}
    if not re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", text) or not text:
        raise ValueError(f"'{value}' is not a valid Roman numeral")
    total, i = 0, 0
    for v, s in _ROMAN:
        while text.startswith(s, i):
            total += v
            i += len(s)
    return {"roman": text, "arabic": total}


@spec("fraction", "Exact fraction arithmetic and simplification: '3/4 + 5/6', '12/18', mixed numbers '1 1/2'. "
      "Returns the simplest fraction, mixed number, and decimal.",
      "math", [param("expression", description="Fractions with + - * / and parentheses.")],
      keywords="pecahan, fraction, sederhanakan, simplify, pecahan campuran, mixed number")
def fraction(expression: str) -> dict[str, Any]:
    text = re.sub(r"(\d+)\s+(\d+)/(\d+)", r"(\1+\2/\3)", str(expression))
    result = evaluate(text)
    value = result["result"]
    frac = Fraction(result["fraction"]) if "fraction" in result else Fraction(str(value))
    whole, rest = divmod(abs(frac.numerator), frac.denominator)
    sign = "-" if frac < 0 else ""
    mixed = f"{sign}{whole} {rest}/{frac.denominator}" if whole and rest else f"{sign}{whole}" if not rest else f"{sign}{rest}/{frac.denominator}"
    return {"fraction": f"{frac.numerator}/{frac.denominator}" if frac.denominator != 1 else str(frac.numerator),
            "mixed": mixed, "decimal": float(frac)}


@spec("combinatorics", "Counting: factorial n!, permutations param(n,r), combinations C(n,r), with or without repetition.",
      "math", [param("n", "int", "Total items."), param("r", "int", "Items chosen (ignored for factorial).", required=False, default=0),
               param("kind", enum=["factorial", "permutation", "combination", "permutation_repeat", "combination_repeat"],
                 required=False, default="combination")],
      keywords="kombinasi, combination, permutasi, permutation, peluang, probability, faktorial, factorial, ncr, npr")
def combinatorics(n: int, r: int = 0, kind: str = "combination") -> dict[str, Any]:
    n, r = int(n), int(r)
    if n < 0 or r < 0 or n > 5000:
        raise ValueError("n from 0 to 5000 and r >= 0")
    kind = kind.lower()
    if kind == "factorial":
        return {"result": _fact(n), "formula": f"{n}!"}
    if kind == "permutation":
        return {"result": math.perm(n, r), "formula": f"param({n},{r}) = {n}!/({n}-{r})!"}
    if kind == "combination":
        return {"result": math.comb(n, r), "formula": f"C({n},{r}) = {n}!/({r}!({n}-{r})!)"}
    if kind == "permutation_repeat":
        return {"result": n ** r, "formula": f"{n}^{r}"}
    if kind == "combination_repeat":
        return {"result": math.comb(n + r - 1, r), "formula": f"C({n}+{r}-1,{r})"}
    raise ValueError("unknown kind")


def _matrix(rows: Any) -> list[list[float]]:
    if isinstance(rows, str):
        rows = [[float(x) for x in re.split(r"[\s,]+", line.strip()) if x] for line in rows.strip().split(";")]
    m = [[float(x) for x in row] for row in rows]
    if not m or any(len(r) != len(m[0]) for r in m):
        raise ValueError("a matrix needs rows of equal length")
    return m


@spec("matrix", "Matrix maths on small matrices: determinant, inverse, transpose, multiply (a×b), add, solve (a·x = b).",
      "math", [param("operation", enum=["determinant", "inverse", "transpose", "multiply", "add", "solve"]),
               param("a", "list", "Matrix as a list of rows, or '1 2; 3 4'.", items="list"),
               param("b", "list", "Second matrix, or the right-hand side vector for solve.", required=False, items="list")],
      keywords="matriks, matrix, determinan, determinant, invers, inverse, transpose, spl, persamaan linear, linear system")
def matrix(operation: str, a: Any, b: Any = None) -> dict[str, Any]:
    import numpy as np

    left = np.array(_matrix(a))
    op = operation.lower()
    if left.size > 400:
        raise ValueError("matrices up to 20x20")
    if op == "determinant":
        return {"result": _plain(float(np.linalg.det(left)))}
    if op == "inverse":
        return {"result": [[_plain(float(v)) for v in row] for row in np.linalg.inv(left)]}
    if op == "transpose":
        return {"result": left.T.tolist()}
    if b is None:
        raise ValueError(f"{op} needs b")
    if op == "solve":
        vec = np.array([float(v[0]) if isinstance(v, list) else float(v) for v in (b if not isinstance(b, str) else b.split())])
        return {"x": [_plain(float(v)) for v in np.linalg.solve(left, vec)]}
    right = np.array(_matrix(b))
    if op == "multiply":
        return {"result": [[_plain(float(v)) for v in row] for row in left @ right]}
    if op == "add":
        return {"result": [[_plain(float(v)) for v in row] for row in left + right]}
    raise ValueError("unknown operation")


@spec("solve_equation", "Solve a polynomial equation in x up to degree 4, e.g. 'x^2 - 5x + 6 = 0' or '2x + 3 = 11'. "
      "Returns real and complex roots.",
      "math", [param("equation", description="Equation in x with an '=' (or an expression equal to 0).")],
      keywords="persamaan, equation, akar persamaan, roots, kuadrat, quadratic, solve, selesaikan, cari x, nilai x")
def solve_equation(equation: str) -> dict[str, Any]:
    import numpy as np

    left, _, right = str(equation).partition("=")
    coeffs = _poly(left)
    for power, value in _poly(right or "0").items():
        coeffs[power] = coeffs.get(power, 0) - value
    degree = max((p for p, c in coeffs.items() if abs(c) > 1e-12), default=0)
    if degree == 0:
        return {"solutions": [], "note": "always true" if abs(coeffs.get(0, 0)) < 1e-12 else "no solution"}
    if degree > 4:
        raise ValueError("degree above 4 is not supported")
    poly = [coeffs.get(p, 0) for p in range(degree, -1, -1)]
    roots = np.roots(poly)
    real = sorted({_plain(round(float(r.real), 10)) for r in roots if abs(r.imag) < 1e-9})
    complex_roots = [f"{_plain(round(r.real, 10))}{'+' if r.imag >= 0 else '-'}{_plain(round(abs(r.imag), 10))}i"
                     for r in roots if abs(r.imag) >= 1e-9]
    out: dict[str, Any] = {"degree": degree, "real_solutions": real, "complex_solutions": complex_roots}
    if degree == 2:
        a, b, c = poly
        out["discriminant"] = _plain(b * b - 4 * a * c)
    return out


def _poly(expr: str) -> dict[int, float]:
    text = _normalise(expr).replace(" ", "").replace("**", "^")
    if not text:
        return {0: 0.0}
    text = text.replace("-", "+-")
    coeffs: dict[int, float] = {}
    for term in filter(None, text.split("+")):
        m = re.fullmatch(r"(-?[\d.]*)\*?(x(?:\^(\d+))?)?", term)
        if not m:
            raise ValueError(f"cannot read the term '{term}'; write it like 3x^2")
        number, has_x, power = m.groups()
        coef = float(number) if number not in ("", "-") else (-1.0 if number == "-" else 1.0)
        p = int(power) if power else (1 if has_x else 0)
        coeffs[p] = coeffs.get(p, 0) + coef
    return coeffs


_SHAPES = {
    "square": ("side",), "rectangle": ("length", "width"), "triangle": ("base", "height"),
    "circle": ("radius",), "trapezoid": ("a", "b", "height"), "parallelogram": ("base", "height"),
    "rhombus": ("d1", "d2"), "cube": ("side",), "cuboid": ("length", "width", "height"),
    "cylinder": ("radius", "height"), "cone": ("radius", "height"), "sphere": ("radius",),
    "pyramid_square": ("side", "height"), "prism_triangle": ("base", "tri_height", "length"),
}


@spec("geometry", "Area, perimeter, volume and surface area of common shapes: square, rectangle, triangle, circle, trapezoid, "
      "parallelogram, rhombus, cube, cuboid (balok), cylinder (tabung), cone (kerucut), sphere (bola), pyramid_square, prism_triangle.",
      "math", [param("shape", enum=sorted(_SHAPES)), param("dims", "obj", "Dimensions by name, e.g. {\"radius\": 7}. "
                                                    + "; ".join(f"{k}: {', '.join(v)}" for k, v in _SHAPES.items()))],
      keywords="luas, keliling, volume, isi, area, perimeter, lingkaran, segitiga, persegi, balok, kubus, tabung, kerucut, bola, "
               "trapesium, jajar genjang, belah ketupat, limas, prisma, circle, triangle, sphere, cylinder")
def geometry(shape: str, dims: dict[str, Any]) -> dict[str, Any]:
    s = shape.lower()
    if s not in _SHAPES:
        raise ValueError(f"unknown shape; use one of {', '.join(_SHAPES)}")
    d = {k: float(v) for k, v in (dims or {}).items()}
    missing = [k for k in _SHAPES[s] if k not in d]
    if missing:
        raise ValueError(f"{s} needs {', '.join(_SHAPES[s])}")
    pi = math.pi
    out: dict[str, Any] = {"shape": s}
    if s == "square":
        out.update(area=d["side"] ** 2, perimeter=4 * d["side"])
    elif s == "rectangle":
        out.update(area=d["length"] * d["width"], perimeter=2 * (d["length"] + d["width"]),
                   diagonal=math.hypot(d["length"], d["width"]))
    elif s == "triangle":
        out.update(area=0.5 * d["base"] * d["height"])
    elif s == "circle":
        out.update(area=pi * d["radius"] ** 2, circumference=2 * pi * d["radius"], diameter=2 * d["radius"])
    elif s == "trapezoid":
        out.update(area=(d["a"] + d["b"]) / 2 * d["height"])
    elif s == "parallelogram":
        out.update(area=d["base"] * d["height"])
    elif s == "rhombus":
        out.update(area=d["d1"] * d["d2"] / 2, side=math.hypot(d["d1"] / 2, d["d2"] / 2),
                   perimeter=4 * math.hypot(d["d1"] / 2, d["d2"] / 2))
    elif s == "cube":
        out.update(volume=d["side"] ** 3, surface_area=6 * d["side"] ** 2)
    elif s == "cuboid":
        ln, w, h = d["length"], d["width"], d["height"]
        out.update(volume=ln * w * h, surface_area=2 * (ln * w + ln * h + w * h), diagonal=math.sqrt(ln * ln + w * w + h * h))
    elif s == "cylinder":
        r, h = d["radius"], d["height"]
        out.update(volume=pi * r * r * h, surface_area=2 * pi * r * (r + h), lateral_area=2 * pi * r * h)
    elif s == "cone":
        r, h = d["radius"], d["height"]
        slant = math.hypot(r, h)
        out.update(volume=pi * r * r * h / 3, surface_area=pi * r * (r + slant), slant_height=slant)
    elif s == "sphere":
        r = d["radius"]
        out.update(volume=4 / 3 * pi * r ** 3, surface_area=4 * pi * r * r)
    elif s == "pyramid_square":
        a, h = d["side"], d["height"]
        slant = math.hypot(a / 2, h)
        out.update(volume=a * a * h / 3, surface_area=a * a + 2 * a * slant)
    elif s == "prism_triangle":
        out.update(volume=0.5 * d["base"] * d["tri_height"] * d["length"])
    out = {k: (_plain(v) if isinstance(v, float) else v) for k, v in out.items()}
    out["note"] = "pi used exactly; with pi = 22/7 or 3.14 the textbook answer differs slightly"
    return out


@spec("weighted_average", "Weighted average, e.g. a report-card score or GPA (IPK): values with weights or credits (SKS). "
      "Letter grades A, A-, B+, B, B-, C+, C, D, E are converted on the 4.0 scale.",
      "math", [param("values", "list", "Scores or letter grades.", items="str"),
               param("weights", "list", "Weights or credits, same length. Default: all 1.", required=False, items="num")],
      keywords="ipk, ip, gpa, sks, nilai rapor, rapor, bobot, weighted, rata-rata tertimbang, weighted average", lead=True)
def weighted_average(values: list[Any], weights: list[float] | None = None) -> dict[str, Any]:
    letters = {"A": 4.0, "A-": 3.7, "AB": 3.5, "B+": 3.3, "B": 3.0, "B-": 2.7, "BC": 2.5, "C+": 2.3, "C": 2.0,
               "C-": 1.7, "D": 1.0, "E": 0.0, "F": 0.0}
    nums = []
    for v in values:
        key = str(v).strip().upper()
        nums.append(letters[key] if key in letters else float(str(v).replace(",", ".")))
    ws = [float(w) for w in weights] if weights else [1.0] * len(nums)
    if len(ws) != len(nums) or not nums:
        raise ValueError("values and weights must have the same length")
    total = sum(ws)
    if total == 0:
        raise ValueError("weights must not all be zero")
    avg = sum(v * w for v, w in zip(nums, ws, strict=True)) / total
    return {"weighted_average": round(avg, 4), "total_weight": _plain(total), "values_used": nums}


@spec("number_sequence", "Arithmetic or geometric sequences: n-th term and sum of the first n terms (barisan dan deret).",
      "math", [param("kind", enum=["arithmetic", "geometric"]), param("first", "num", "First term a."),
               param("step", "num", "Difference b (arithmetic) or ratio r (geometric)."), param("n", "int", "Term number / count.")],
      keywords="barisan, deret, sequence, series, aritmatika, arithmetic, geometri, geometric, suku ke")
def number_sequence(kind: str, first: float, step: float, n: int) -> dict[str, Any]:
    n = int(n)
    if not 1 <= n <= 1_000_000:
        raise ValueError("n from 1 to 1,000,000")
    if kind == "arithmetic":
        term = first + (n - 1) * step
        total = n / 2 * (first + term)
    else:
        term = first * step ** (n - 1)
        total = first * n if step == 1 else first * (1 - step ** n) / (1 - step)
    out: dict[str, Any] = {"nth_term": _plain(float(term)), "sum_first_n": _plain(float(total)),
                           "first_terms": [_plain(float(first + i * step if kind == "arithmetic" else first * step ** i))
                                           for i in range(min(n, 8))]}
    if kind == "geometric" and abs(step) < 1:
        out["sum_to_infinity"] = _plain(first / (1 - step))
    return out


@spec("round_number", "Round a number: to decimals, to significant figures, up (ceil) or down (floor), or to a multiple.",
      "math", [param("value", "num"), param("mode", enum=["decimals", "significant", "up", "down", "multiple"], required=False, default="decimals"),
               param("digits", "num", "Decimals, significant figures, or the multiple.", required=False, default=2)],
      keywords="bulatkan, pembulatan, round, rounding, angka penting, significant figures, desimal")
def round_number(value: float, mode: str = "decimals", digits: float = 2) -> dict[str, Any]:
    from decimal import ROUND_HALF_UP, Decimal

    d = Decimal(str(value))
    if mode == "decimals":
        q = Decimal(1).scaleb(-int(digits))
        return {"result": float(d.quantize(q, rounding=ROUND_HALF_UP)), "rule": "half up (0.5 rounds away from zero)"}
    if mode == "significant":
        if value == 0:
            return {"result": 0}
        places = int(digits) - int(math.floor(math.log10(abs(value)))) - 1
        return {"result": float(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)) if places > 0
                else float(round(value, places))}
    if mode == "up":
        return {"result": math.ceil(value)}
    if mode == "down":
        return {"result": math.floor(value)}
    if mode == "multiple":
        if not digits:
            raise ValueError("multiple must not be 0")
        return {"result": _plain(round(value / digits) * digits)}
    raise ValueError("unknown mode")
