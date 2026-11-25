"""Money maths, Indonesian formats (terbilang, rupiah, phone numbers), and everyday health numbers."""

from __future__ import annotations

import math
import re
from typing import Any

from .catalog import group, param, spec

group("finance", "Money and business maths",
      "uang, money, bunga, interest, cicilan, angsuran, installment, kredit, credit, pinjaman, loan, kpr, mortgage, "
      "tabungan, savings, investasi, investment, deposito, roi, untung, laba, profit, rugi, loss, modal, capital, "
      "harga jual, selling price, diskon, discount, markup, margin, ppn, pajak, tax, zakat, inflasi, inflation, "
      "bep, break even, npv, irr, depresiasi, penyusutan, patungan, split bill, gaji, salary")
group("indonesia", "Indonesian formats",
      "terbilang, rupiah, rp, idr, nomor hp, nomor telepon, no hp, whatsapp, wa, +62, kode pos, plat nomor, "
      "format indonesia, bahasa indonesia, angka ke huruf, ejaan angka")
group("health", "Everyday health numbers",
      "bmi, imt, berat badan, weight, tinggi badan, height, kalori, calorie, bmr, tdee, diet, olahraga, exercise, "
      "detak jantung, heart rate, air minum, water intake, kehamilan, pregnancy, hpl")


def _money(v: float) -> float:
    return round(float(v), 2)


@spec("loan_payment", "Loan or installment plan (KPR, kredit motor, pinjaman): monthly payment, total paid, total interest, "
      "and an amortization schedule. Supports annuity (fixed payment), flat interest (bunga flat, common for "
      "Indonesian vehicle loans), and effective (declining principal).",
      "finance", [param("principal", "num", "Loan amount."), param("annual_rate", "num", "Interest per year in %."),
                  param("months", "int", "Tenor in months."),
                  param("method", enum=["annuity", "flat", "effective"], required=False, default="annuity"),
                  param("schedule_rows", "int", "Rows of the schedule to return.", required=False, default=12)],
      keywords="cicilan, angsuran, installment, kredit, kpr, pinjaman, loan, bunga flat, bunga efektif, anuitas, tenor", lead=True)
def loan_payment(principal: float, annual_rate: float, months: int, method: str = "annuity",
                 schedule_rows: int = 12) -> dict[str, Any]:
    p, n = float(principal), int(months)
    if p <= 0 or n <= 0:
        raise ValueError("principal and months must be positive")
    r = float(annual_rate) / 100 / 12
    rows, balance, total = [], p, 0.0
    for k in range(1, n + 1):
        if method == "flat":
            interest, principal_part = p * float(annual_rate) / 100 / 12, p / n
        elif method == "effective":
            principal_part, interest = p / n, balance * r
        else:
            pay = p / n if r == 0 else p * r / (1 - (1 + r) ** -n)
            interest = balance * r
            principal_part = pay - interest
        payment = principal_part + interest
        balance -= principal_part
        total += payment
        if k <= int(schedule_rows) or k == n:
            rows.append({"month": k, "payment": _money(payment), "principal": _money(principal_part),
                         "interest": _money(interest), "balance": _money(max(balance, 0))})
    first = rows[0]["payment"]
    out = {"method": method, "first_payment": first, "total_paid": _money(total), "total_interest": _money(total - p),
           "schedule": rows}
    if method == "flat":
        r_eff = _flat_to_effective(p, first, n)
        out["effective_annual_rate_equivalent"] = round(r_eff, 2) if r_eff is not None else None
    return out


def _flat_to_effective(p: float, pay: float, n: int) -> float | None:
    lo, hi = 0.0, 1.0
    if pay * n <= p:
        return 0.0
    for _ in range(200):
        mid = (lo + hi) / 2
        value = p * mid / (1 - (1 + mid) ** -n) if mid else p / n
        lo, hi = (mid, hi) if value < pay else (lo, mid)
    return lo * 12 * 100


@spec("compound_interest", "Future value with compound interest and optional regular deposits (tabungan, deposito, "
      "investasi rutin), with a yearly breakdown. Also simple interest.",
      "finance", [param("principal", "num", "Starting amount."), param("annual_rate", "num", "% per year."), param("years", "num"),
                  param("deposit", "num", "Added every period.", required=False, default=0),
                  param("periods_per_year", "int", "12 monthly, 1 yearly, 365 daily.", required=False, default=12),
                  param("simple", "bool", "Simple interest instead.", required=False, default=False),
                  param("tax_percent", "num", "Tax on interest (deposito in Indonesia: 20).", required=False, default=0)],
      keywords="bunga majemuk, compound interest, bunga berbunga, deposito, tabungan, investasi, nilai masa depan, future value", lead=True)
def compound_interest(principal: float, annual_rate: float, years: float, deposit: float = 0, periods_per_year: int = 12,
                      simple: bool = False, tax_percent: float = 0) -> dict[str, Any]:
    rate = float(annual_rate) / 100 * (1 - float(tax_percent) / 100)
    if simple:
        interest = float(principal) * rate * float(years)
        return {"final": _money(float(principal) + interest), "interest": _money(interest), "mode": "simple"}
    m = max(int(periods_per_year), 1)
    periods = int(round(float(years) * m))
    balance, contributed, yearly = float(principal), float(principal), []
    for k in range(1, periods + 1):
        balance = balance * (1 + rate / m) + float(deposit)
        contributed += float(deposit)
        if k % m == 0:
            yearly.append({"year": k // m, "balance": _money(balance)})
    return {"final": _money(balance), "contributed": _money(contributed), "interest_earned": _money(balance - contributed),
            "yearly": yearly[:50], "effective_annual_rate": round(((1 + rate / m) ** m - 1) * 100, 4)}


@spec("savings_goal", "How much to save each month to reach a target by a date, or how long it takes with a fixed monthly amount.",
      "finance", [param("target", "num"), param("annual_rate", "num", required=False, default=0),
                  param("months", "int", "Time available (to compute the monthly amount).", required=False),
                  param("monthly", "num", "Monthly saving (to compute the time).", required=False),
                  param("current", "num", "Already saved.", required=False, default=0)],
      keywords="target tabungan, savings goal, nabung, menabung, dana darurat, emergency fund, berapa lama nabung, dp rumah")
def savings_goal(target: float, annual_rate: float = 0, months: int | None = None, monthly: float | None = None,
                 current: float = 0) -> dict[str, Any]:
    r = float(annual_rate) / 100 / 12
    t, c = float(target), float(current)
    if months:
        n = int(months)
        grown = c * (1 + r) ** n
        need = (t - grown) / n if r == 0 else (t - grown) * r / ((1 + r) ** n - 1)
        return {"monthly_needed": _money(max(need, 0)), "months": n}
    if monthly:
        balance, n = c, 0
        while balance < t and n < 1200:
            balance = balance * (1 + r) + float(monthly)
            n += 1
        return {"months_needed": n if balance >= t else None, "years": round(n / 12, 2), "final": _money(balance)}
    raise ValueError("give months or monthly")


@spec("npv_irr", "Net present value at a discount rate, internal rate of return, and payback period of a cash flow "
      "(first value is usually the negative investment).",
      "finance", [param("cash_flows", "list", "e.g. [-10000000, 3000000, 4000000, 5000000].", items="num"),
                  param("discount_rate", "num", "% per period for NPV.", required=False, default=10)],
      keywords="npv, irr, payback, kelayakan investasi, feasibility, studi kelayakan, arus kas, cash flow, discounted")
def npv_irr(cash_flows: list[float], discount_rate: float = 10) -> dict[str, Any]:
    flows = [float(x) for x in cash_flows]
    if len(flows) < 2:
        raise ValueError("give at least two cash flows")
    d = float(discount_rate) / 100
    npv = sum(cf / (1 + d) ** t for t, cf in enumerate(flows))

    def f(rate: float) -> float:
        return sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))

    irr = None
    lo, hi = -0.99, 10.0
    if f(lo) * f(hi) < 0:
        for _ in range(300):
            mid = (lo + hi) / 2
            if f(lo) * f(mid) <= 0:
                hi = mid
            else:
                lo = mid
        irr = round(mid * 100, 4)
    cumulative, payback = 0.0, None
    for t, cf in enumerate(flows):
        previous = cumulative
        cumulative += cf
        if payback is None and cumulative >= 0 and t > 0:
            payback = round(t - 1 + (-previous / cf if cf else 0), 2)
    return {"npv": _money(npv), "irr_percent": irr, "payback_periods": payback, "decision": "accept" if npv > 0 else "reject"}


@spec("profit_margin", "Business pricing: profit, margin %, markup %, selling price from cost and target margin or markup, "
      "and break-even quantity from fixed costs.",
      "finance", [param("cost", "num", "Cost per unit (HPP/modal)."), param("price", "num", "Selling price per unit.", required=False),
                  param("target_margin", "num", "% of the selling price.", required=False),
                  param("target_markup", "num", "% on top of cost.", required=False),
                  param("fixed_costs", "num", "For break-even.", required=False)],
      keywords="harga jual, selling price, hpp, margin, markup, laba, untung, profit, bep, break even, titik impas, modal", lead=True)
def profit_margin(cost: float, price: float | None = None, target_margin: float | None = None,
                  target_markup: float | None = None, fixed_costs: float | None = None) -> dict[str, Any]:
    c = float(cost)
    if price is None:
        if target_margin is not None:
            if target_margin >= 100:
                raise ValueError("margin must be below 100%")
            price = c / (1 - float(target_margin) / 100)
        elif target_markup is not None:
            price = c * (1 + float(target_markup) / 100)
        else:
            raise ValueError("give price, target_margin, or target_markup")
    p = float(price)
    profit = p - c
    out: dict[str, Any] = {"price": _money(p), "profit_per_unit": _money(profit),
                           "margin_percent": round(profit / p * 100, 2) if p else None,
                           "markup_percent": round(profit / c * 100, 2) if c else None}
    if fixed_costs is not None:
        out["break_even_units"] = math.ceil(float(fixed_costs) / profit) if profit > 0 else None
        out["break_even_revenue"] = _money(out["break_even_units"] * p) if out["break_even_units"] else None
    return out


@spec("discount_calc", "Final price after one or more stacked discounts (e.g. 50% + 20%), with the total effective discount, "
      "optional tax added after, and the saving.",
      "finance", [param("price", "num"), param("discounts", "list", "Percent discounts applied in order.", items="num"),
                  param("tax_percent", "num", "Tax added after the discount (PPN).", required=False, default=0),
                  param("cashback", "num", "Fixed amount off at the end.", required=False, default=0)],
      keywords="diskon, discount, potongan harga, promo, sale, harga setelah diskon, 50+20, cashback", lead=True)
def discount_calc(price: float, discounts: list[float], tax_percent: float = 0, cashback: float = 0) -> dict[str, Any]:
    p = float(price)
    steps = []
    for d in discounts:
        p *= 1 - float(d) / 100
        steps.append(_money(p))
    after = p
    p = p * (1 + float(tax_percent) / 100) - float(cashback)
    return {"after_discounts": _money(after), "final": _money(max(p, 0)), "steps": steps,
            "effective_discount_percent": round((1 - after / float(price)) * 100, 4) if price else 0,
            "you_save": _money(float(price) - after + float(cashback))}


@spec("tax_vat", "Add or remove VAT/PPN from a price. The default rate is 12% (Indonesian PPN from 2025; many goods "
      "effectively use 11% via DPP nilai lain). Give the rate that applies.",
      "finance", [param("amount", "num"), param("rate", "num", required=False, default=12),
                  param("included", "bool", "True if the amount already includes tax.", required=False, default=False)],
      keywords="ppn, pajak, vat, tax, pajak pertambahan nilai, harga termasuk pajak, sebelum pajak, dpp")
def tax_vat(amount: float, rate: float = 12, included: bool = False) -> dict[str, Any]:
    a, r = float(amount), float(rate) / 100
    if included:
        base = a / (1 + r)
        return {"base_dpp": _money(base), "tax": _money(a - base), "total": _money(a), "rate_percent": rate}
    return {"base_dpp": _money(a), "tax": _money(a * r), "total": _money(a * (1 + r)), "rate_percent": rate}


@spec("zakat_calc", "Zakat mal (2.5% of wealth above the nisab of 85 g gold held a year), zakat penghasilan (2.5% of income "
      "above the monthly nisab), or zakat fitrah (per person, 2.5 kg rice or its value).",
      "finance", [param("kind", enum=["mal", "penghasilan", "fitrah"]),
                  param("amount", "num", "Wealth (mal), monthly income (penghasilan), or people (fitrah).", required=False, default=0),
                  param("gold_price_per_gram", "num", "Current gold price, needed for the nisab.", required=False),
                  param("rice_price_per_kg", "num", "For fitrah paid in money.", required=False),
                  param("debts", "num", "Debts due now, subtracted for mal.", required=False, default=0)],
      keywords="zakat, nisab, zakat mal, zakat penghasilan, zakat profesi, zakat fitrah, fitrah")
def zakat_calc(kind: str, amount: float = 0, gold_price_per_gram: float | None = None, rice_price_per_kg: float | None = None,
               debts: float = 0) -> dict[str, Any]:
    if kind == "fitrah":
        people = max(int(amount or 1), 1)
        out: dict[str, Any] = {"people": people, "rice_kg": 2.5 * people}
        if rice_price_per_kg:
            out["money"] = _money(2.5 * people * float(rice_price_per_kg))
        return out
    if not gold_price_per_gram:
        raise ValueError("give gold_price_per_gram so the nisab (85 g gold) can be computed")
    nisab_year = 85 * float(gold_price_per_gram)
    if kind == "mal":
        net = float(amount) - float(debts)
        due = net >= nisab_year
        return {"net_wealth": _money(net), "nisab": _money(nisab_year), "due": due, "zakat": _money(net * 0.025) if due else 0}
    nisab_month = nisab_year / 12
    due = float(amount) >= nisab_month
    return {"monthly_income": _money(amount), "nisab_monthly": _money(nisab_month), "due": due,
            "zakat_monthly": _money(float(amount) * 0.025) if due else 0}


@spec("inflation_adjust", "What an amount from one year is worth in another at an average inflation rate, or the future cost "
      "of something today.",
      "finance", [param("amount", "num"), param("annual_inflation", "num", "% per year (Indonesia is often 2-4%)."), param("years", "num")],
      keywords="inflasi, inflation, nilai uang, daya beli, purchasing power, harga di masa depan, future price")
def inflation_adjust(amount: float, annual_inflation: float, years: float) -> dict[str, Any]:
    f = (1 + float(annual_inflation) / 100) ** float(years)
    return {"future_cost": _money(float(amount) * f), "today_value_of_future_amount": _money(float(amount) / f),
            "factor": round(f, 6)}


@spec("depreciation", "Asset depreciation schedule: straight-line (garis lurus) or declining balance (saldo menurun).",
      "finance", [param("cost", "num"), param("salvage", "num", "Residual value."), param("years", "int"),
                  param("method", enum=["straight_line", "declining_balance", "double_declining"], required=False, default="straight_line")],
      keywords="penyusutan, depresiasi, depreciation, garis lurus, saldo menurun, nilai buku, book value, aset")
def depreciation(cost: float, salvage: float, years: int, method: str = "straight_line") -> dict[str, Any]:
    c, s, n = float(cost), float(salvage), int(years)
    if n <= 0:
        raise ValueError("years must be positive")
    rows, book = [], c
    rate = (2 / n) if method == "double_declining" else (1 - (s / c) ** (1 / n) if c > 0 and s > 0 else 1 / n)
    for y in range(1, n + 1):
        dep = (c - s) / n if method == "straight_line" else min(book * rate, max(book - s, 0))
        if y == n and method != "straight_line":
            dep = max(book - s, 0)
        book -= dep
        rows.append({"year": y, "depreciation": _money(dep), "book_value": _money(book)})
    return {"method": method, "schedule": rows}


@spec("split_bill", "Split a bill fairly (patungan): equal shares, or by what each person ordered, with tax and service "
      "shared in proportion, and rounding to a chosen unit.",
      "finance", [param("total", "num", "Bill total, if splitting equally.", required=False),
                  param("people", "list", "Names (equal split) or objects {name, amount} (by order).", items="obj"),
                  param("tax_percent", "num", required=False, default=0), param("service_percent", "num", required=False, default=0),
                  param("round_to", "num", "e.g. 1000 to round each share up to Rp1.000.", required=False, default=0)],
      keywords="patungan, split bill, bagi tagihan, bayar bareng, urunan, bagi rata, traktir")
def split_bill(people: list[Any], total: float | None = None, tax_percent: float = 0, service_percent: float = 0,
               round_to: float = 0) -> dict[str, Any]:
    extra = 1 + (float(tax_percent) + float(service_percent)) / 100
    shares: list[dict[str, Any]] = []
    if people and isinstance(people[0], dict) and "amount" in people[0]:
        for p in people:
            shares.append({"name": p.get("name", "?"), "share": float(p["amount"]) * extra})
    else:
        if total is None:
            raise ValueError("an equal split needs total")
        names = [p.get("name", "?") if isinstance(p, dict) else str(p) for p in people]
        each = float(total) * extra / max(len(names), 1)
        shares = [{"name": n, "share": each} for n in names]
    for s in shares:
        s["share"] = math.ceil(s["share"] / round_to) * round_to if round_to else _money(s["share"])
    return {"shares": shares, "sum": _money(sum(s["share"] for s in shares))}


@spec("salary_net", "Monthly salary breakdown for an Indonesian employee: BPJS Kesehatan (1%), JHT (2%), JP (1%) employee "
      "parts with their caps, and the PPh 21 TER category to look up. Estimates; confirm with payroll.",
      "finance", [param("gross_monthly", "num"), param("ptkp_status", enum=["TK/0", "TK/1", "TK/2", "TK/3", "K/0", "K/1", "K/2", "K/3"],
                                                required=False, default="TK/0"),
                  param("jp_cap", "num", "Wage cap for JP (changes yearly).", required=False, default=10_547_400),
                  param("bpjs_kes_cap", "num", "Wage cap for BPJS Kesehatan.", required=False, default=12_000_000)],
      keywords="gaji bersih, take home pay, thp, potongan gaji, bpjs, jht, pph 21, pph21, slip gaji, salary")
def salary_net(gross_monthly: float, ptkp_status: str = "TK/0", jp_cap: float = 10_547_400,
               bpjs_kes_cap: float = 12_000_000) -> dict[str, Any]:
    g = float(gross_monthly)
    kes = min(g, float(bpjs_kes_cap)) * 0.01
    jht = g * 0.02
    jp = min(g, float(jp_cap)) * 0.01
    category = "A" if ptkp_status in ("TK/0", "TK/1", "K/0") else "B" if ptkp_status in ("TK/2", "TK/3", "K/1", "K/2") else "C"
    return {"gross": _money(g), "bpjs_kesehatan": _money(kes), "jht": _money(jht), "jp": _money(jp),
            "after_bpjs": _money(g - kes - jht - jp), "pph21_ter_category": category,
            "note": "PPh 21 uses the TER table for this category and gross; rates and caps change, so check the current PMK"}


# ── Indonesian formats ────────────────────────────────────────────────────────
_SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]
_SCALES = [(10 ** 12, "triliun"), (10 ** 9, "miliar"), (10 ** 6, "juta"), (1000, "ribu")]


def terbilang_int(n: int) -> str:
    if n < 0:
        return "minus " + terbilang_int(-n)
    if n < 12:
        return _SATUAN[n] or "nol"
    if n < 20:
        return f"{_SATUAN[n - 10]} belas"
    if n < 100:
        tens, rest = divmod(n, 10)
        return f"{_SATUAN[tens]} puluh" + (f" {_SATUAN[rest]}" if rest else "")
    if n < 200:
        return "seratus" + (f" {terbilang_int(n - 100)}" if n > 100 else "")
    if n < 1000:
        h, rest = divmod(n, 100)
        return f"{_SATUAN[h]} ratus" + (f" {terbilang_int(rest)}" if rest else "")
    if n < 2000:
        return "seribu" + (f" {terbilang_int(n - 1000)}" if n > 1000 else "")
    for scale, name in _SCALES:
        if n >= scale:
            head, rest = divmod(n, scale)
            return f"{terbilang_int(head)} {name}" + (f" {terbilang_int(rest)}" if rest else "")
    return ""


@spec("terbilang", "Write a number in Indonesian words (terbilang), e.g. for kuitansi or cheques: 1250000 -> 'satu juta dua "
      "ratus lima puluh ribu rupiah'. Decimals are read digit by digit ('koma').",
      "indonesia", [param("number", description="The number; dots as thousands separators are fine ('1.250.000')."),
                    param("currency", "bool", "Append 'rupiah'.", required=False, default=False),
                    param("capitalize", "bool", required=False, default=True)],
      keywords="terbilang, angka ke huruf, number to words, kuitansi, ejaan angka, dibaca", lead=True)
def terbilang(number: str, currency: bool = False, capitalize: bool = True) -> dict[str, Any]:
    text = str(number).strip().replace("Rp", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", text):
        text = text.replace(".", "").replace(",", ".")
    elif text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    whole, _, frac = text.partition(".")
    if not re.fullmatch(r"-?\d+", whole) or (frac and not frac.isdigit()):
        raise ValueError(f"'{number}' is not a number")
    n = int(whole)
    if abs(n) >= 10 ** 15:
        raise ValueError("numbers up to 999 triliun")
    words = terbilang_int(n)
    if frac:
        words += " koma " + " ".join(_SATUAN[int(d)] or "nol" for d in frac)
    if currency:
        words += " rupiah"
    return {"terbilang": words[:1].upper() + words[1:] if capitalize else words}


def format_rupiah_value(amount: float, decimals: int = 0) -> str:
    sign = "-" if amount < 0 else ""
    s = f"{abs(amount):,.{decimals}f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}Rp{s}"


@spec("rupiah", "Format a number as Rupiah (Rp1.250.000,00), or read a Rupiah text back into a number "
      "('Rp 1,5 jt', '2,3 M', '750rb', '1.250.000'). Also abbreviates large amounts (1,25 juta).",
      "indonesia", [param("value", description="A number or a Rupiah text."),
                    param("mode", enum=["format", "parse", "short"], required=False, default="format"),
                    param("decimals", "int", required=False, default=0)],
      keywords="rupiah, rp, idr, format rupiah, format uang, juta, ribu, miliar, rb, jt", lead=True)
def rupiah(value: str, mode: str = "format", decimals: int = 0) -> dict[str, Any]:
    if mode == "parse" or (mode == "format" and re.search(r"[a-zA-Z]", str(value).replace("Rp", "").replace("rp", ""))):
        return {"number": parse_rupiah(str(value))}
    n = parse_rupiah(str(value)) if isinstance(value, str) else float(value)
    if mode == "short":
        for scale, name in ((10 ** 12, "triliun"), (10 ** 9, "miliar"), (10 ** 6, "juta"), (10 ** 3, "ribu")):
            if abs(n) >= scale:
                short = f"{n / scale:.2f}".rstrip("0").rstrip(".").replace(".", ",")
                return {"result": f"Rp{short} {name}"}
        return {"result": format_rupiah_value(n)}
    return {"result": format_rupiah_value(n, int(decimals)), "number": n}


_RP_SCALE = {"rb": 1e3, "ribu": 1e3, "k": 1e3, "jt": 1e6, "juta": 1e6, "m": 1e9, "miliar": 1e9, "milyar": 1e9,
             "t": 1e12, "triliun": 1e12}


def parse_rupiah(text: str) -> float:
    t = text.lower().replace("rp", "").replace("idr", "").replace(" ", "").strip(".:")
    m = re.fullmatch(r"(-?[\d.,]+)([a-z]*)", t)
    if not m:
        raise ValueError(f"cannot read '{text}' as Rupiah")
    digits, unit = m.groups()
    if unit and unit not in _RP_SCALE:
        raise ValueError(f"unknown unit '{unit}'")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", digits):
        number = float(digits.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", digits):
        number = float(digits.replace(",", ""))
    else:
        number = float(digits.replace(",", "."))
    return number * _RP_SCALE.get(unit, 1)


_OPERATORS = {"811": "Telkomsel", "812": "Telkomsel", "813": "Telkomsel", "821": "Telkomsel", "822": "Telkomsel",
              "823": "Telkomsel", "851": "Telkomsel", "852": "Telkomsel", "853": "Telkomsel", "814": "Indosat",
              "815": "Indosat", "816": "Indosat", "855": "Indosat", "856": "Indosat", "857": "Indosat", "858": "Indosat",
              "817": "XL", "818": "XL", "819": "XL", "859": "XL", "877": "XL", "878": "XL", "831": "Axis", "832": "Axis",
              "833": "Axis", "838": "Axis", "895": "Tri", "896": "Tri", "897": "Tri", "898": "Tri", "899": "Tri",
              "881": "Smartfren", "882": "Smartfren", "883": "Smartfren", "884": "Smartfren", "885": "Smartfren",
              "886": "Smartfren", "887": "Smartfren", "888": "Smartfren", "889": "Smartfren"}


@spec("phone_id", "Normalise Indonesian phone numbers to +62 / 08 / WhatsApp link form, validate their length, and name the "
      "likely mobile operator from the prefix (numbers can be ported, so it is a hint).",
      "indonesia", [param("numbers", "list", "One or more numbers in any format.", items="str")],
      keywords="nomor hp, no hp, nomor telepon, phone number, whatsapp, wa.me, +62, 08, operator, provider", lead=True)
def phone_id(numbers: list[str]) -> dict[str, Any]:
    out = []
    for raw in numbers if isinstance(numbers, list) else [numbers]:
        digits = re.sub(r"\D", "", str(raw))
        if digits.startswith("62"):
            local = digits[2:]
        elif digits.startswith("0"):
            local = digits[1:]
        else:
            local = digits
        mobile = local.startswith("8")
        valid = mobile and 9 <= len(local) <= 12 or (not mobile and 7 <= len(local) <= 11)
        item: dict[str, Any] = {"input": raw, "valid": valid, "e164": f"+62{local}", "local": f"0{local}", "mobile": mobile}
        if mobile:
            item["whatsapp"] = f"https://wa.me/62{local}"
            item["operator_hint"] = _OPERATORS.get(local[:3], "unknown")
        out.append(item)
    return {"numbers": out}


_PLATES = {"A": "Banten", "B": "Jakarta, Bogor, Depok, Tangerang, Bekasi", "D": "Bandung", "E": "Cirebon, Indramayu, Majalengka, Kuningan",
           "F": "Bogor, Cianjur, Sukabumi", "T": "Karawang, Purwakarta, Subang", "Z": "Garut, Tasikmalaya, Sumedang, Ciamis, Banjar",
           "G": "Pekalongan, Tegal, Brebes, Pemalang, Batang", "H": "Semarang, Kendal, Salatiga, Demak", "K": "Pati, Kudus, Jepara, Rembang, Blora, Grobogan",
           "R": "Banyumas, Cilacap, Purbalingga, Banjarnegara", "AA": "Magelang, Purworejo, Kebumen, Temanggung, Wonosobo",
           "AD": "Surakarta (Solo), Sukoharjo, Boyolali, Klaten, Wonogiri, Sragen, Karanganyar", "AB": "Yogyakarta",
           "L": "Surabaya", "M": "Madura", "N": "Malang, Pasuruan, Probolinggo", "P": "Besuki, Bondowoso, Situbondo, Jember, Banyuwangi",
           "S": "Bojonegoro, Tuban, Lamongan, Mojokerto, Jombang", "W": "Sidoarjo, Gresik", "AE": "Madiun, Ngawi, Magetan, Ponorogo, Pacitan",
           "AG": "Kediri, Blitar, Tulungagung, Nganjuk, Trenggalek", "BL": "Aceh", "BB": "Sumatera Utara (west)",
           "BK": "Sumatera Utara (east), Medan", "BA": "Sumatera Barat", "BM": "Riau", "BP": "Kepulauan Riau", "BH": "Jambi",
           "BG": "Sumatera Selatan", "BN": "Bangka Belitung", "BD": "Bengkulu", "BE": "Lampung", "DK": "Bali",
           "DR": "Lombok", "EA": "Sumbawa", "DH": "Timor", "EB": "Flores", "ED": "Sumba", "KB": "Kalimantan Barat",
           "DA": "Kalimantan Selatan", "KH": "Kalimantan Tengah", "KT": "Kalimantan Timur", "KU": "Kalimantan Utara",
           "DB": "Sulawesi Utara", "DL": "Sangihe, Talaud", "DM": "Gorontalo", "DN": "Sulawesi Tengah", "DD": "Sulawesi Selatan",
           "DC": "Sulawesi Barat", "DT": "Sulawesi Tenggara", "DE": "Maluku", "DG": "Maluku Utara", "PA": "Papua", "PB": "Papua Barat"}


@spec("plat_nomor", "Which region an Indonesian vehicle plate prefix belongs to (B = Jakarta area, D = Bandung, AB = Yogyakarta...).",
      "indonesia", [param("plate", description="e.g. 'AB 1234 XY' or just 'AD'.")],
      keywords="plat nomor, plat motor, plat mobil, nomor polisi, nopol, tnkb, kode wilayah kendaraan")
def plat_nomor(plate: str) -> dict[str, Any]:
    m = re.match(r"\s*([A-Za-z]{1,2})", plate)
    if not m:
        raise ValueError("a plate starts with one or two letters")
    code = m.group(1).upper()
    region = _PLATES.get(code) or (_PLATES.get(code[0]) if len(code) == 2 and code not in _PLATES else None)
    return {"prefix": code, "region": region or "unknown", "note": "regional codes are periodically revised"}


# ── Health ────────────────────────────────────────────────────────────────────
@spec("bmi_calc", "Body mass index with the Asia-Pacific (WHO, used in Indonesia) and international categories, and the "
      "healthy weight range for the height. General information, not medical advice.",
      "health", [param("weight_kg", "num"), param("height_cm", "num")],
      keywords="bmi, imt, indeks massa tubuh, berat badan ideal, ideal weight, obesitas, overweight, kurus", lead=True)
def bmi_calc(weight_kg: float, height_cm: float) -> dict[str, Any]:
    h = float(height_cm) / 100
    if h <= 0.5 or float(weight_kg) <= 0:
        raise ValueError("height in cm and weight in kg, both positive")
    bmi = float(weight_kg) / (h * h)
    asia = "underweight" if bmi < 18.5 else "normal" if bmi < 23 else "overweight" if bmi < 25 else "obese I" if bmi < 30 else "obese II"
    intl = "underweight" if bmi < 18.5 else "normal" if bmi < 25 else "overweight" if bmi < 30 else "obese"
    return {"bmi": round(bmi, 1), "category_asia_pacific": asia, "category_international": intl,
            "healthy_weight_kg_asia": [round(18.5 * h * h, 1), round(22.9 * h * h, 1)],
            "note": "general information, not a diagnosis"}


@spec("calorie_needs", "Daily calorie needs: BMR (Mifflin-St Jeor) and TDEE for an activity level, plus a gentle deficit or "
      "surplus target. General information, not medical advice.",
      "health", [param("weight_kg", "num"), param("height_cm", "num"), param("age", "int"), param("sex", enum=["male", "female"]),
                 param("activity", enum=["sedentary", "light", "moderate", "active", "very_active"], required=False, default="light")],
      keywords="kalori, calorie, bmr, tdee, kebutuhan kalori, diet, defisit kalori, calorie deficit, bulking, cutting")
def calorie_needs(weight_kg: float, height_cm: float, age: int, sex: str, activity: str = "light") -> dict[str, Any]:
    bmr = 10 * float(weight_kg) + 6.25 * float(height_cm) - 5 * int(age) + (5 if sex == "male" else -161)
    factor = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very_active": 1.9}[activity]
    tdee = bmr * factor
    return {"bmr": round(bmr), "tdee": round(tdee), "lose_weight": round(tdee - 500), "gain_weight": round(tdee + 300),
            "water_liters": round(float(weight_kg) * 0.033, 1), "note": "general information, not medical advice"}


@spec("pregnancy_dates", "Estimated due date (HPL, Naegele's rule) and current week of pregnancy from the first day of the "
      "last period (HPHT). General information; follow your midwife or doctor.",
      "health", [param("last_period", description="First day of the last menstrual period.")],
      keywords="hpl, hpht, kehamilan, hamil, pregnancy, due date, usia kehamilan, minggu kehamilan, perkiraan lahir")
def pregnancy_dates(last_period: str) -> dict[str, Any]:
    from datetime import datetime, timedelta

    from .units_time_tools import parse_date, zone

    tz = zone("WIB")
    lmp = parse_date(last_period, tz).date()
    today = datetime.now(tz).date()
    days = (today - lmp).days
    return {"due_date_hpl": (lmp + timedelta(days=280)).isoformat(), "weeks": days // 7, "days": days % 7,
            "trimester": 1 if days < 98 else 2 if days < 196 else 3, "note": "estimate only; an ultrasound date is more accurate"}


@spec("heart_rate_zones", "Training heart-rate zones from age (and resting heart rate for the Karvonen method).",
      "health", [param("age", "int"), param("resting_hr", "int", required=False)],
      keywords="detak jantung, heart rate, zona latihan, training zone, max heart rate, cardio, lari, running")
def heart_rate_zones(age: int, resting_hr: int | None = None) -> dict[str, Any]:
    mx = 208 - 0.7 * int(age)
    zones = {"1 recovery": (0.5, 0.6), "2 endurance": (0.6, 0.7), "3 tempo": (0.7, 0.8), "4 threshold": (0.8, 0.9), "5 max": (0.9, 1.0)}
    out = {}
    for name, (lo, hi) in zones.items():
        if resting_hr:
            reserve = mx - int(resting_hr)
            out[name] = [round(int(resting_hr) + lo * reserve), round(int(resting_hr) + hi * reserve)]
        else:
            out[name] = [round(lo * mx), round(hi * mx)]
    return {"max_hr": round(mx), "zones_bpm": out, "method": "Karvonen" if resting_hr else "percent of max (Tanaka)"}
