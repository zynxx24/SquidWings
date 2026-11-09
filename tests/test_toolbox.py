"""The hidden toolbox: every tool gives exact answers, fails readably, and stays hidden until asked for.

Each test is one case a cheap model would otherwise get wrong by working it out in its head.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import pytest

from tool.execute.execute_toolbox import CATALOG, GROUPS, deferred_names, find_tools, match, search


def run(name: str, **kwargs: Any) -> dict[str, Any]:
    """Call a toolbox tool the way the registry does (through its safety wrapper)."""
    func = CATALOG[name].func
    out = func(**kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    assert isinstance(out, dict) and "ok" in out, out
    return out


def ok(name: str, **kwargs: Any) -> dict[str, Any]:
    out = run(name, **kwargs)
    assert out["ok"], out
    return out


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    from utils import workspace_context

    monkeypatch.setattr(workspace_context, "_active_workspace", workspace_context._active_workspace)
    token = workspace_context._active_workspace.set(str(tmp_path))
    yield tmp_path
    workspace_context._active_workspace.reset(token)


# ── Catalog ───────────────────────────────────────────────────────────────────
def test_more_than_a_hundred_tools_in_named_groups():
    assert len(CATALOG) >= 100
    assert all(entry.group in GROUPS for entry in CATALOG.values())
    assert all(entry.description and len(entry.description) > 30 for entry in CATALOG.values())


def test_every_schema_is_valid_openai_function_format():
    for entry in CATALOG.values():
        schema = entry.schema().to_openai_schema()
        fn = schema["function"]
        assert fn["name"] == entry.name and fn["parameters"]["type"] == "object"
        for prop in fn["parameters"]["properties"].values():
            assert prop["type"] in {"string", "integer", "number", "boolean", "array", "object"}
            if prop["type"] == "array":
                assert "items" in prop, entry.name


def test_toolbox_names_never_shadow_core_tools():
    from tool.register_tools import register_all_tools

    reg = register_all_tools()
    assert set(CATALOG) <= set(reg.get_all_names())
    assert "find_tools" in reg.get_all_names()


def test_a_bad_argument_comes_back_as_an_error_with_the_expected_call():
    out = run("calc", expression="2 +* 3")
    assert out["ok"] is False and "calc" in out["error"] and out["usage"].startswith("calc(")


def test_extra_arguments_do_not_crash_a_tool():
    assert ok("calc", expression="1+1", workspace_path="/tmp", nonsense=1)["result"] == 2


# ── Matching: hidden until the request calls for it ───────────────────────────
@pytest.mark.parametrize(("request_text", "expected"), [
    ("berapa cicilan kpr 500 juta bunga 7% 20 tahun", "loan_payment"),
    ("konversi 10 mil ke km", "unit_convert"),
    ("hari apa tanggal 17 agustus 2045", "date_info"),
    ("jam berapa di tokyo kalau di jakarta jam 9 wib", "timezone_convert"),
    ("berapa jumlah kata paragraf ini", "text_stats"),
    ("buat password kuat", "password_generate"),
    ("ubah 1250000 jadi terbilang", "terbilang"),
    ("cek kontras warna #777 di putih", "color_contrast"),
    ("apotek terdekat dari monas", "osm_nearby"),
    ("cuaca bandung besok", "weather"),
    ("kurs dollar ke rupiah hari ini", "currency_convert"),
    ("berapa lot kalau risk 1% sl 30 pip", "position_size"),
    ("rekap csv penjualan per kota", "table_query"),
    ("bmi saya 70kg 170cm", "bmi_calc"),
    ("libur nasional 2026", "public_holidays"),
    ("validasi json ini", "json_tool"),
    ("subnet 192.168.1.0/24 ada berapa host", "ip_cidr"),
    ("luas lingkaran jari-jari 7", "geometry"),
    ("selisih hari 1 januari sampai 9 november", "date_diff"),
    ("hasil scrape google maps ini tolong import", "gmaps_import"),
    ("cari klinik rating tertinggi yang belum punya website", "places_search"),
    ("format nomor hp 0812 3456 7890 jadi wa", "phone_id"),
    ("hitung zakat mal saya", "zakat_calc"),
    ("patungan makan 4 orang", "split_bill"),
])
def test_the_request_wakes_the_right_tool(request_text, expected):
    assert expected in match(request_text)


@pytest.mark.parametrize("request_text", [
    "halo apa kabar", "tolong buatkan essay tentang banjir", "perbaiki bug login di api",
    "tambahkan endpoint api baru di fastapi", "buat halaman html dengan css", "aku lagi sedih banget",
    "fix source maps webpack error",
])
def test_ordinary_requests_wake_nothing(request_text):
    assert match(request_text) == []


def test_match_is_capped():
    assert len(match("hitung persen statistik rata-rata median modus faktorial prima kpk fpb", limit=5)) <= 5


def test_find_tools_searches_in_both_languages():
    assert "loan_payment" in [e.name for e in search("loan installment")]
    assert "geo_distance" in [e.name for e in search("jarak dua kota")]
    assert {e.name for e in search("", group_name="indonesia")} >= {"terbilang", "rupiah", "phone_id"}


def test_find_tools_unlocks_for_this_session_only(tmp_path, monkeypatch):
    from utils import session_settings

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr("tool.execute.execute_toolbox.get_active_workspace", lambda: str(a))
    out = asyncio.run(find_tools(query="cicilan pinjaman bunga"))
    assert "loan_payment" in out["unlocked"] and out["tools"][0]["call"].startswith(out["tools"][0]["name"])
    assert "loan_payment" in session_settings.load(a).tools
    assert session_settings.load(b).tools == []


def test_unlocked_tools_are_bounded(tmp_path):
    from utils import session_settings

    for i in range(30):
        session_settings.unlock_tools(tmp_path, [f"t{i}"])
    tools = session_settings.load(tmp_path).tools
    assert len(tools) == session_settings.MAX_UNLOCKED_TOOLS and tools[-1] == "t29"


def test_find_tools_with_no_match_lists_the_groups():
    out = asyncio.run(find_tools(query="qwzx", unlock=False))
    assert out["tools"] == [] and "maps" in out["groups"]


# ── Math ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("expression", "result"), [
    ("2+3*4", 14), ("(2+3)*4", 20), ("2^10", 1024), ("15% * 2000000", 300000), ("5!", 120),
    ("sqrt(144)", 12), ("10 % 3", 1), ("7 // 2", 3), ("2(3+4)", 14), ("1e3 + 1", 1001),
    ("12 × 3 ÷ 4", 9), ("comb(5,2)", 10), ("round(2.675, 2)", 2.67), ("sind(30)", 0.5), ("log(8, 2)", 3),
])
def test_calc_is_exact(expression, result):
    assert ok("calc", expression=expression)["result"] == pytest.approx(result)


def test_calc_keeps_fractions_exact():
    out = ok("calc", expression="1/3 + 1/6")
    assert out["fraction"] == "1/2" and out["result"] == 0.5


def test_calc_refuses_code_and_huge_numbers():
    assert run("calc", expression="__import__('os').system('ls')")["ok"] is False
    assert run("calc", expression="9**999999")["ok"] is False
    assert run("calc", expression="1/0")["ok"] is False
    assert run("calc", expression="factorial(100000)")["ok"] is False


def test_calc_many_reports_each_result_or_error():
    out = ok("calc_many", expressions=["1+1", "1/0", "2*3"])
    assert out["results"][0]["result"] == 2 and "error" in out["results"][1] and out["results"][2]["result"] == 6


def test_stats_describe():
    out = ok("stats_describe", values="4 8 15 16 23 42")
    assert out["mean"] == pytest.approx(18) and out["median"] == pytest.approx(15.5) and out["count"] == 6
    assert out["stdev_sample"] == pytest.approx(13.4907, rel=1e-4)


def test_stats_describe_accepts_a_list_and_reports_mode():
    assert ok("stats_describe", values=[1, 2, 2, 3])["mode"] == [2]


def test_linear_regression_finds_the_line():
    out = ok("linear_regression", x=[1, 2, 3, 4], y=[3, 5, 7, 9], predict=[10])
    assert out["slope"] == pytest.approx(2) and out["intercept"] == pytest.approx(1)
    assert out["predictions"][0]["y"] == pytest.approx(21) and out["r_squared"] == pytest.approx(1)


@pytest.mark.parametrize(("mode", "a", "b", "result"), [
    ("of", 200, 15, 30), ("change", 80, 100, 25), ("ratio", 30, 120, 25), ("add", 100, 11, 111),
    ("subtract", 100, 20, 80), ("reverse", 111, 11, 100),
])
def test_percent(mode, a, b, result):
    assert ok("percent", mode=mode, a=a, b=b)["result"] == pytest.approx(result)


def test_number_theory():
    out = ok("number_theory", numbers=[12, 18])
    assert out["gcd_fpb"] == 6 and out["lcm_kpk"] == 36
    assert out["numbers"][0]["factors"] == "2^2 × 3"
    assert ok("number_theory", numbers=[97], operation="is_prime")["numbers"][0]["is_prime"] is True
    assert ok("number_theory", numbers=[10], operation="fibonacci")["fibonacci"] == 55
    assert ok("number_theory", numbers=[28])["numbers"][0]["is_perfect"] is True


def test_base_convert():
    assert ok("base_convert", value="ff", from_base=16, to_base=2)["result"] == "11111111"
    assert ok("base_convert", value="0b1010", to_base=10)["decimal"] == 10


def test_roman_numerals_both_ways():
    assert ok("roman_numeral", value="2026")["roman"] == "MMXXVI"
    assert ok("roman_numeral", value="mcmxciv")["arabic"] == 1994
    assert run("roman_numeral", value="IIII")["ok"] is False


def test_fraction_with_mixed_numbers():
    out = ok("fraction", expression="1 1/2 + 3/4")
    assert out["fraction"] == "9/4" and out["mixed"] == "2 1/4"


def test_combinatorics():
    assert ok("combinatorics", n=10, r=3, kind="combination")["result"] == 120
    assert ok("combinatorics", n=10, r=3, kind="permutation")["result"] == 720


def test_matrix():
    assert ok("matrix", operation="determinant", a=[[1, 2], [3, 4]])["result"] == pytest.approx(-2)
    assert ok("matrix", operation="solve", a="2 1; 1 3", b=[3, 5])["x"] == pytest.approx([0.8, 1.4])


@pytest.mark.parametrize(("equation", "roots"), [
    ("x^2 - 5x + 6 = 0", [2, 3]), ("2x + 3 = 11", [4]), ("x^2 = 9", [-3, 3]), ("x^3 - x = 0", [-1, 0, 1]),
])
def test_solve_equation(equation, roots):
    assert ok("solve_equation", equation=equation)["real_solutions"] == pytest.approx(roots)


def test_solve_equation_reports_complex_roots():
    out = ok("solve_equation", equation="x^2 + 1 = 0")
    assert out["real_solutions"] == [] and len(out["complex_solutions"]) == 2 and out["discriminant"] == -4


def test_geometry():
    out = ok("geometry", shape="circle", dims={"radius": 7})
    assert out["area"] == pytest.approx(153.938, rel=1e-4)
    assert ok("geometry", shape="cuboid", dims={"length": 2, "width": 3, "height": 4})["volume"] == 24
    assert run("geometry", shape="circle", dims={})["ok"] is False


def test_weighted_average_for_a_gpa():
    out = ok("weighted_average", values=["A", "B+", "B"], weights=[3, 2, 3])
    assert out["weighted_average"] == pytest.approx((4 * 3 + 3.3 * 2 + 3 * 3) / 8)


def test_number_sequence():
    assert ok("number_sequence", kind="arithmetic", first=2, step=3, n=10)["nth_term"] == 29
    assert ok("number_sequence", kind="geometric", first=1, step=2, n=10)["sum_first_n"] == 1023


def test_round_number_half_up():
    assert ok("round_number", value=2.675, digits=2)["result"] == 2.68
    assert ok("round_number", value=123456, mode="significant", digits=2)["result"] == 120000


# ── Units and time ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("value", "a", "b", "result"), [
    (10, "mi", "km", 16.09344), (100, "celsius", "fahrenheit", 212), (1, "kg", "ons", 10), (1, "ha", "m2", 10000),
    (100, "km/jam", "m/s", 27.7778), (1, "gb", "mb", 1000), (1, "GiB", "MiB", 1024), (1, "kwh", "kcal", 860.4207),
    (180, "deg", "rad", math.pi), (0, "kelvin", "celsius", -273.15),
])
def test_unit_convert(value, a, b, result):
    assert ok("unit_convert", value=value, from_unit=a, to_unit=b)["result"] == pytest.approx(result, rel=1e-4)


def test_unit_convert_refuses_mixed_dimensions_with_a_reason():
    out = run("unit_convert", value=1, from_unit="kg", to_unit="km")
    assert out["ok"] is False and "mass" in out["error"] and "length" in out["error"]


def test_date_info_reads_indonesian_dates():
    out = ok("date_info", value="17 Agustus 1945")
    assert out["date"] == "1945-08-17" and out["hari"] == "Jumat"


@pytest.mark.parametrize("text", ["2026-08-17", "17/08/2026", "17-08-2026", "17 Agustus 2026", "17 agt 2026"])
def test_parse_date_formats(text):
    from tool.execute.execute_toolbox.units_time_tools import parse_date

    assert parse_date(text).date().isoformat() == "2026-08-17"


def test_date_diff():
    out = ok("date_diff", start="2026-01-01", end="2026-11-09")
    assert out["days"] == 312 and out["breakdown"] == "0 years 10 months 8 days"
    assert out["working_days"] == 222


def test_date_add_working_days_skips_weekends_and_holidays():
    out = ok("date_add", start="2026-11-06", amount=2, unit="workdays", holidays=["2026-11-09"])
    assert out["date"] == "2026-11-11"
    assert ok("date_add", start="2026-01-31", amount=1, unit="months")["date"] == "2026-02-28"


def test_timezone_convert():
    out = ok("timezone_convert", value="2026-11-09 09:00", from_tz="WIB", to_tz=["Asia/Tokyo", "WITA", "UTC"])
    times = {c["zone"]: c["time"] for c in out["converted"]}
    assert times == {"Asia/Tokyo": "2026-11-09 11:00", "WITA": "2026-11-09 10:00", "UTC": "2026-11-09 02:00"}


def test_unknown_timezone_is_explained():
    assert "IANA" in run("timezone_convert", value="now", from_tz="Mars/Base")["error"]


def test_unix_time_both_ways():
    assert ok("unix_time", value="0", timezone="UTC")["date"] == "1970-01-01"
    assert ok("unix_time", value="1700000000000", timezone="UTC")["date"] == "2023-11-14"
    assert ok("unix_time", value="2023-11-14 22:13:20", timezone="UTC")["timestamp"] == 1700000000


def test_age_calc():
    out = ok("age_calc", birth_date="2000-02-29", on_date="2026-11-09")
    assert out["years"] == 26 and out["months"] == 8 and out["born_on"] == "Selasa"


def test_hijri_and_weton():
    assert ok("hijri_date", value="2026-02-18")["month_name"] == "Ramadan"
    assert ok("weton", value="1945-08-17")["weton"] == "Jumat Legi"
    assert ok("weton", value="1921-06-08")["weton"] == "Rabu Kliwon"
    assert ok("weton", value="1901-06-06")["weton"] == "Kamis Pon"


def test_calendar_month():
    out = ok("calendar_month", year=2026, month=2)
    assert out["days_in_month"] == 28 and out["first_day"] == "Minggu"


@pytest.mark.parametrize(("expression", "first"), [
    ("0 9 * * mon-fri", "2026-11-10 09:00 Tue"), ("*/15 * * * *", "2026-11-09 10:15 Mon"),
    ("@daily", "2026-11-10 00:00 Tue"), ("0 0 1 * *", "2026-12-01 00:00 Tue"),
])
def test_cron_next(expression, first):
    out = ok("cron_next", expression=expression, start="2026-11-09 10:05", count=2)
    assert out["next_runs"][0] == first


def test_cron_rejects_bad_fields():
    assert run("cron_next", expression="61 * * * *")["ok"] is False
    assert run("cron_next", expression="* * *")["ok"] is False


def test_countdown_has_a_direction():
    assert ok("countdown", target="2000-01-01")["past"] is True


def test_time_now_honours_the_zone():
    from tool.execute.execute_local_ops.local_ops_tool import time_now

    jakarta = asyncio.run(time_now("WIB"))
    tokyo = asyncio.run(time_now("Asia/Tokyo"))
    assert jakarta["tz"] == "Asia/Jakarta" and jakarta["utc_offset"] == "UTC+7" and tokyo["utc_offset"] == "UTC+9"
    assert jakarta["hari"] in ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
    assert "note" in asyncio.run(time_now("Nowhere/Land"))


# ── Text and encodings ────────────────────────────────────────────────────────
def test_text_stats_counts_exactly():
    out = ok("text_stats", text="Saya suka kopi. Kamu suka teh!\n\nKita sama-sama suka.")
    # "sama-sama" is one word (reduplication), as Indonesian word counts treat it.
    assert out["words"] == 9 and out["sentences"] == 3 and out["paragraphs"] == 2


def test_word_frequency_skips_stopwords():
    out = ok("word_frequency", text="kopi dan teh dan kopi dan susu", top=2)
    assert out["top"][0] == {"term": "kopi", "count": 2}


def test_regex_test_and_replace():
    out = ok("regex_test", pattern=r"(\d+)-(\d+)", text="a 12-34 b 5-6", replace=r"\2-\1")
    assert out["count"] == 2 and out["matches"][0]["groups"] == ["12", "34"] and out["replaced"] == "a 34-12 b 6-5"
    assert run("regex_test", pattern="(", text="x")["ok"] is False


def test_text_diff_and_similarity():
    out = ok("text_diff", a="a\nb\nc", b="a\nB\nc")
    assert out["added_lines"] == 1 and out["removed_lines"] == 1
    assert ok("text_similarity", a="kitten", b="sitting", ignore_case=False)["levenshtein"] == 3


def test_case_convert_splits_identifiers():
    out = ok("case_convert", text="userIDValue")["result"]
    assert out["snake"] == "user_id_value" and out["kebab"] == "user-id-value" and out["pascal"] == "UserIdValue"


def test_lines_tool():
    assert ok("lines_tool", text="b\na\nb\nc", operation="unique")["result"] == "b\na\nc"
    assert ok("lines_tool", text="10\n9\n100", operation="sort_numeric")["result"] == "9\n10\n100"
    assert ok("lines_tool", text="x\ny\nx", operation="duplicates")["duplicates"] == [{"line": "x", "count": 2}]


def test_extract_patterns():
    text = "Hubungi ani@contoh.co.id atau 0812-3456-7890, lihat https://contoh.id/promo. Harga Rp 1.500.000 #promo"
    found = ok("extract_patterns", text=text)["found"]
    assert found["emails"] == ["ani@contoh.co.id"] and found["urls"] == ["https://contoh.id/promo"]
    assert found["phones"] and found["hashtags"] == ["#promo"] and found["money"]


def test_text_clean_removes_invisible_characters():
    out = ok("text_clean", text="Halo\u200b   dunia “ok”", operations=["invisible", "spaces", "quotes"])
    assert out["result"] == 'Halo dunia "ok"'


def test_text_template_reports_missing_keys():
    out = ok("text_template", template="Halo {nama}, {jumlah}", rows=[{"nama": "Ani", "jumlah": "Rp5"}, {"nama": "Budi"}])
    assert out["results"][0] == "Halo Ani, Rp5" and out["problems"] == [{"row": 1, "missing": ["jumlah"]}]


def test_text_wrap_modes():
    assert ok("text_wrap", text="abcdefghij", mode="truncate", width=5)["result"] == "abcd…"
    assert ok("text_wrap", text="satu dua tiga empat", mode="chunks", width=9)["count"] == 3


@pytest.mark.parametrize("codec", ["base64", "base64url", "base32", "hex", "url", "url_plus", "html", "unicode_escape", "binary"])
def test_encode_decode_round_trips(codec):
    text = "Halo dunia & <ü> 100%"
    encoded = ok("encode_decode", text=text, codec=codec)["result"]
    assert ok("encode_decode", text=encoded, codec=codec, direction="decode")["result"] == text


def test_hash_text_known_values():
    assert ok("hash_text", text="abc", algorithm="sha256")["hashes"]["sha256"].startswith("ba7816bf")
    assert ok("hash_text", text="abc", algorithm="md5")["hashes"]["md5"] == "900150983cd24fb0d6963f7d28e17f72"


def test_uuid_and_passwords():
    assert len(ok("uuid_generate", count=3)["uuids"]) == 3
    pw = ok("password_generate", length=20)["passwords"][0]
    assert len(pw) == 20 and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
    assert ok("password_generate", check="password123")["common_pattern"] is True
    assert ok("password_generate", passphrase_words=4)["passwords"][0].count("-") == 3


def test_random_pick_is_within_bounds():
    out = ok("random_pick", mode="dice", dice="3d6+2")
    assert len(out["rolls"]) == 3 and 5 <= out["total"] <= 20
    teams = ok("random_pick", mode="teams", items=list("abcdef"), count=2)["teams"]
    assert sorted(sum(teams, [])) == list("abcdef")
    assert run("random_pick", mode="choice", items=["a"], count=2)["ok"] is False


def test_jwt_decode_without_verifying():
    import base64

    def part(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    token = f"{part({'alg': 'HS256'})}.{part({'sub': '42', 'exp': 1})}.sig"
    out = ok("jwt_decode", token=token)
    assert out["payload"]["sub"] == "42" and out["expired"] is True and out["signature_verified"] is False


def test_ciphers():
    assert ok("cipher", text="SOS", method="morse")["result"] == "... --- ..."
    assert ok("cipher", text="... --- ...", method="morse", direction="decode")["result"] == "SOS"
    assert ok("cipher", text="abc", method="caesar", key="3")["result"] == "def"
    enc = ok("cipher", text="Attack at dawn", method="vigenere", key="LEMON")["result"]
    assert enc == "Lxfopv ef rnhr"
    assert ok("cipher", text=enc, method="vigenere", key="LEMON", direction="decode")["result"] == "Attack at dawn"


def test_unicode_info_finds_homoglyphs():
    out = ok("unicode_info", text="pаypal", only_unusual=True)
    assert out["characters"][0]["name"].startswith("CYRILLIC") and out["mixed_scripts"] is True


# ── Data ──────────────────────────────────────────────────────────────────────
CSV = "kota,produk,harga,qty\nBandung,Kopi,20000,3\nJakarta,Teh,15000,5\nBandung,Teh,15000,2\nSurabaya,Kopi,22000,1\n"


def test_table_inspect_from_text():
    out = ok("table_inspect", text=CSV)
    assert out["rows"] == 4 and [c["name"] for c in out["columns"]] == ["kota", "produk", "harga", "qty"]


def test_table_query_group_and_sort(ws):
    (ws / "jual.csv").write_text(CSV, encoding="utf-8")
    out = ok("table_query", path="jual.csv", group_by=["kota"], aggregate={"qty": "sum"}, sort_by=["-qty", "kota"])
    assert out["rows"] == [{"kota": "Bandung", "qty": 5}, {"kota": "Jakarta", "qty": 5}, {"kota": "Surabaya", "qty": 1}]


def test_table_query_filters_and_saves(ws):
    (ws / "jual.csv").write_text(CSV, encoding="utf-8")
    out = ok("table_query", path="jual.csv", filters=[{"column": "harga", "op": ">=", "value": 20000}],
             columns=["kota", "harga"], save_as="out/mahal.xlsx")
    assert out["matched_rows"] == 2 and (ws / "out" / "mahal.xlsx").exists()
    bad = run("table_query", path="jual.csv", filters=[{"column": "nope", "op": "=", "value": 1}])
    assert bad["ok"] is False and "kota" in bad["error"]


def test_table_query_semicolon_csv(ws):
    (ws / "eu.csv").write_text("a;b\n1;2\n3;4\n", encoding="utf-8")
    assert ok("table_query", path="eu.csv", filters=[{"column": "a", "op": ">", "value": 2}])["rows"] == [{"a": 3, "b": 4}]


def test_table_convert(ws):
    out = ok("table_convert", text=CSV, to="markdown")
    assert out["result"].startswith("| kota | produk")
    assert ok("table_convert", text=CSV, to="json", output="x.json")["saved"] == "x.json"


def test_table_tools_stay_inside_the_workspace(ws):
    out = run("table_inspect", path="../../etc/passwd")
    assert out["ok"] is False and ("escapes" in out["error"] or "does not exist" in out["error"])
    assert run("table_convert", text=CSV, to="csv", output=".git/hooks/pre-commit")["ok"] is False


def test_markdown_table():
    out = ok("markdown_table", rows=[{"nama": "Ani", "nilai": 90}, {"nama": "Budi", "nilai": 85}])
    assert out["markdown"].splitlines()[1] == "|---|---:|"


def test_json_tool():
    bad = ok("json_tool", operation="validate", text='{"a": 1,}')
    assert bad["valid"] is False and bad["line"] == 1
    assert ok("json_tool", operation="get", text='{"a": {"b": [10, 20]}}', query="a.b[1]")["value"] == 20
    assert "a.b" in " ".join(ok("json_tool", operation="paths", text='{"a": {"b": 1}}')["paths"])


def test_json_diff():
    out = ok("json_diff", a='{"a": 1, "b": [1, 2]}', b='{"a": 2, "b": [1], "c": true}')
    changes = {c["path"]: c["change"] for c in out["changes"]}
    assert changes == {"a": "changed", "b[1]": "removed", "c": "added"}


def test_format_convert_yaml_toml_xml():
    assert json.loads(ok("format_convert", text="a: 1\nb: [x, y]\n")["result"]) == {"a": 1, "b": ["x", "y"]}
    assert ok("format_convert", text='[tool]\nname = "x"\n')["detected"] == "toml"
    xml = ok("format_convert", text='<r a="1"><i>x</i><i>y</i></r>')
    assert json.loads(xml["result"]) == {"r": {"@a": "1", "i": ["x", "y"]}}


def test_xml_entity_bomb_is_refused():
    bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><l>&b;</l>'
    assert run("format_convert", text=bomb, from_format="xml")["ok"] is False


def test_sqlite_query_is_read_only(ws):
    import sqlite3

    db = ws / "toko.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE barang (nama TEXT, harga INT)")
    conn.execute("INSERT INTO barang VALUES ('kopi', 20000), ('teh', 15000)")
    conn.commit()
    conn.close()
    assert ok("sqlite_query", path="toko.db")["tables"]["barang"]["rows"] == 2
    assert ok("sqlite_query", path="toko.db", query="SELECT nama FROM barang WHERE harga > 16000")["rows"] == [{"nama": "kopi"}]
    assert run("sqlite_query", path="toko.db", query="DELETE FROM barang")["ok"] is False
    assert run("sqlite_query", path="toko.db", query="SELECT 1; DROP TABLE barang")["ok"] is False


def test_sql_check():
    assert ok("sql_check", query="SELECT nama FROM t", schema="CREATE TABLE t (nama TEXT)")["valid"] is True
    out = ok("sql_check", query="SELEC nama FROM t")
    assert out["valid"] is False and "syntax" in out["error"]


def test_json_schema_infer():
    schema = ok("json_schema_infer", text='{"id": 1, "email": "a@b.co", "tags": ["x"]}')["schema"]
    assert schema["properties"]["email"]["format"] == "email" and schema["properties"]["tags"]["items"]["type"] == "string"


def test_env_inspect_never_prints_values(ws):
    (ws / ".env").write_text("API_KEY=sk-secret-123456\nEMPTY=\nAPI_KEY=again\n", encoding="utf-8")
    (ws / ".env.example").write_text("API_KEY=\nDB_URL=\n", encoding="utf-8")
    out = ok("env_inspect", path=".env")
    assert "secret" not in json.dumps(out) and "sk-" not in json.dumps(out)
    assert out["duplicates"] == ["API_KEY"] and out["missing_vs_example"] == ["DB_URL"]


# ── Developer helpers ─────────────────────────────────────────────────────────
def test_url_parse_and_strip_tracking():
    out = ok("url_parse", url="https://toko.id/p?id=5&utm_source=ig&utm_medium=x#top", remove_params=["utm_*"])
    assert out["query"]["id"] == "5" and out["url"] == "https://toko.id/p?id=5#top"


def test_http_status_and_mime():
    assert ok("http_status", code=429)["phrase"] == "Too Many Requests"
    assert run("http_status", code=799)["ok"] is False
    assert ok("mime_type", value="laporan.xlsx")["mime"].endswith("spreadsheetml.sheet")


def test_semver():
    assert ok("semver", operation="compare", versions=["1.10.0", "1.9.9"])["result"] == 1
    assert ok("semver", operation="sort", versions=["1.0.0", "1.0.0-rc.1", "0.9.0"])["sorted"] == ["0.9.0", "1.0.0-rc.1", "1.0.0"]
    assert ok("semver", operation="bump", versions=["1.4.2"], part="minor")["to"] == "1.5.0"
    sat = ok("semver", operation="satisfies", versions=["1.4.0", "2.0.0", "1.2.9"], range="^1.3")["results"]
    assert sat == {"1.4.0": True, "2.0.0": False, "1.2.9": False}


def test_ip_cidr():
    out = ok("ip_cidr", value="192.168.1.0/24", contains="192.168.1.77", split_prefix=26)
    assert out["usable_hosts"] == 254 and out["contains"]["192.168.1.77"] is True and out["subnet_count"] == 4
    assert ok("ip_cidr", value="8.8.8.8")["global"] is True


def test_port_info():
    assert ok("port_info", value="5432")["service"] == "PostgreSQL"
    assert 6379 in ok("port_info", value="redis")["ports"]


def test_colors():
    out = ok("color_convert", color="#1e90ff")
    assert out["rgb"] == "rgb(30, 144, 255)" and out["hsl"].startswith("hsl(210")
    contrast = ok("color_contrast", foreground="#777777", background="white")
    assert contrast["ratio"] == pytest.approx(4.48, abs=0.01) and contrast["AA_normal"] is False
    assert "suggested_foreground" in contrast
    assert ok("color_contrast", foreground="#000", background="#fff")["ratio"] == 21


def test_css_units():
    assert ok("css_units", value=24, from_unit="px")["rem"] == 1.5


@pytest.mark.parametrize(("language", "code", "valid"), [
    ("python", "def f(:\n  pass", False), ("python", "x = 1\n", True), ("json", '{"a": }', False),
    ("yaml", "a: [1, 2", False), ("toml", 'a = "x"', True),
])
def test_syntax_check(language, code, valid):
    out = ok("syntax_check", language=language, code=code)
    assert out["valid"] is valid


def test_syntax_check_points_at_the_python_error():
    out = ok("syntax_check", language="python", code="x = (1,\ny = 2\n")
    assert out["valid"] is False and out["line"] in (1, 2)


def test_python_inspect_flags_mistakes():
    code = "import os\nimport json\n\ndef f(a=[]):\n    try:\n        print(a)\n    except:\n        pass\n\nclass C:\n    def m(self): return json\n"
    out = ok("python_inspect", code=code)
    issues = " ".join(i["issue"] for i in out["issues"])
    assert "mutable default" in issues and "bare except" in issues and "'os'" in issues and "'json'" not in issues
    assert out["classes"][0]["methods"] == ["m"]


def test_dependencies_list(ws):
    (ws / "requirements.txt").write_text("fastapi>=0.110  # web\n-r other.txt\nhttpx\n", encoding="utf-8")
    (ws / "package.json").write_text('{"dependencies": {"react": "^19"}, "scripts": {"dev": "vite"}}', encoding="utf-8")
    out = ok("dependencies_list")
    assert out["files"]["requirements.txt"] == ["fastapi>=0.110", "httpx"]
    assert out["files"]["package.json"]["dependencies"] == {"react": "^19"}


def test_html_to_text_and_markdown_render():
    out = ok("html_to_text", html="<html><head><title>T</title><script>x()</script></head><body><h1>Judul</h1><a href='/a'>A</a></body></html>")
    assert out["title"] == "T" and "x()" not in out["result"] and out["links"][0]["href"] == "/a"
    assert "<table>" in ok("markdown_render", markdown="|a|b|\n|-|-|\n|1|2|")["html"]
    assert "<script>" not in ok("markdown_render", markdown="<script>alert(1)</script>")["html"]


def test_gitignore_and_code_stats(ws):
    out = ok("gitignore_template", stacks=["python", "node"])
    assert "node_modules/" in out["gitignore"] and ".env" in out["gitignore"]
    (ws / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "big.js").write_text("x\n" * 1000, encoding="utf-8")
    stats = ok("code_stats")
    assert stats["languages"] == {"Python": {"files": 1, "lines": 3}}


# ── Money and Indonesia ───────────────────────────────────────────────────────
def test_loan_payment_annuity_matches_the_formula():
    out = ok("loan_payment", principal=100_000_000, annual_rate=12, months=12)
    assert out["first_payment"] == pytest.approx(8_884_878.82, abs=0.05)
    assert out["schedule"][-1]["balance"] == pytest.approx(0, abs=1)


def test_loan_payment_flat_interest_like_a_motorbike_loan():
    out = ok("loan_payment", principal=20_000_000, annual_rate=10, months=24, method="flat")
    assert out["first_payment"] == pytest.approx(20_000_000 / 24 + 20_000_000 * 0.10 / 12, abs=0.01)
    assert out["effective_annual_rate_equivalent"] > 10


def test_compound_interest_and_savings_goal():
    out = ok("compound_interest", principal=1000, annual_rate=10, years=2, periods_per_year=1)
    assert out["final"] == pytest.approx(1210)
    assert ok("compound_interest", principal=1000, annual_rate=10, years=2, simple=True)["final"] == 1200
    goal = ok("savings_goal", target=12_000_000, months=12)
    assert goal["monthly_needed"] == pytest.approx(1_000_000)
    assert ok("savings_goal", target=10_000_000, monthly=1_000_000)["months_needed"] == 10


def test_npv_irr():
    out = ok("npv_irr", cash_flows=[-1000, 500, 500, 500], discount_rate=10)
    assert out["npv"] == pytest.approx(243.43, abs=0.01) and out["irr_percent"] == pytest.approx(23.38, abs=0.01)
    assert out["payback_periods"] == pytest.approx(2.0)


def test_profit_margin_and_break_even():
    out = ok("profit_margin", cost=7000, target_margin=30, fixed_costs=1_500_000)
    assert out["price"] == pytest.approx(10000) and out["break_even_units"] == 500
    assert ok("profit_margin", cost=8000, price=10000)["markup_percent"] == 25


def test_stacked_discounts_are_not_added():
    out = ok("discount_calc", price=100_000, discounts=[50, 20])
    assert out["after_discounts"] == 40_000 and out["effective_discount_percent"] == pytest.approx(60)


def test_vat_included_and_excluded():
    assert ok("tax_vat", amount=100_000, rate=11)["total"] == 111_000
    assert ok("tax_vat", amount=111_000, rate=11, included=True)["base_dpp"] == pytest.approx(100_000)


def test_zakat():
    out = ok("zakat_calc", kind="mal", amount=200_000_000, gold_price_per_gram=1_500_000)
    assert out["due"] is True and out["zakat"] == 5_000_000
    assert ok("zakat_calc", kind="fitrah", amount=4)["rice_kg"] == 10
    assert run("zakat_calc", kind="mal", amount=1)["ok"] is False


def test_inflation_depreciation_split_and_salary():
    assert ok("inflation_adjust", amount=100, annual_inflation=10, years=2)["future_cost"] == pytest.approx(121)
    dep = ok("depreciation", cost=10_000, salvage=1_000, years=3)["schedule"]
    assert dep[-1]["book_value"] == pytest.approx(1000)
    ddb = ok("depreciation", cost=10_000, salvage=1_000, years=5, method="double_declining")["schedule"]
    assert ddb[-1]["book_value"] == pytest.approx(1000)
    split = ok("split_bill", total=300_000, people=["a", "b", "c"], tax_percent=10, round_to=1000)
    assert [s["share"] for s in split["shares"]] == [110_000, 110_000, 110_000]
    salary = ok("salary_net", gross_monthly=10_000_000)
    assert salary["bpjs_kesehatan"] == 100_000 and salary["jht"] == 200_000 and salary["jp"] == 100_000


@pytest.mark.parametrize(("number", "words"), [
    ("0", "Nol"), ("11", "Sebelas"), ("15", "Lima belas"), ("100", "Seratus"), ("1000", "Seribu"),
    ("1250000", "Satu juta dua ratus lima puluh ribu"), ("1.250.000", "Satu juta dua ratus lima puluh ribu"),
    ("2026", "Dua ribu dua puluh enam"), ("111111", "Seratus sebelas ribu seratus sebelas"),
    ("3,25", "Tiga koma dua lima"), ("1000000000", "Satu miliar"), ("-45", "Minus empat puluh lima"),
])
def test_terbilang(number, words):
    assert ok("terbilang", number=number)["terbilang"] == words


def test_terbilang_with_rupiah():
    assert ok("terbilang", number="1500", currency=True)["terbilang"] == "Seribu lima ratus rupiah"


@pytest.mark.parametrize(("text", "number"), [
    ("Rp 1,5 jt", 1_500_000), ("2,3M", 2_300_000_000), ("750rb", 750_000), ("Rp1.250.000", 1_250_000),
    ("Rp1.250.000,50", 1_250_000.5), ("IDR 5000", 5000), ("12k", 12_000),
])
def test_parse_rupiah(text, number):
    assert ok("rupiah", value=text, mode="parse")["number"] == pytest.approx(number)


def test_format_rupiah():
    assert ok("rupiah", value="1250000")["result"] == "Rp1.250.000"
    assert ok("rupiah", value="1250000.5", decimals=2)["result"] == "Rp1.250.000,50"
    assert ok("rupiah", value="1250000", mode="short")["result"] == "Rp1,25 juta"


def test_phone_id():
    out = ok("phone_id", numbers=["0812-3456-7890", "+62 857 1234 5678", "12"])["numbers"]
    assert out[0]["e164"] == "+6281234567890" and out[0]["whatsapp"] == "https://wa.me/6281234567890"
    assert out[0]["operator_hint"] == "Telkomsel" and out[1]["operator_hint"] == "Indosat" and out[2]["valid"] is False


def test_plat_nomor():
    assert ok("plat_nomor", plate="AB 1234 XY")["region"] == "Yogyakarta"
    assert "Jakarta" in ok("plat_nomor", plate="B 1 RI")["region"]


def test_health_numbers():
    assert ok("bmi_calc", weight_kg=70, height_cm=170)["category_asia_pacific"] == "overweight"
    assert ok("calorie_needs", weight_kg=70, height_cm=170, age=30, sex="male")["bmr"] == 1618
    assert ok("heart_rate_zones", age=30)["max_hr"] == 187
    assert ok("pregnancy_dates", last_period="2026-01-01")["due_date_hpl"] == "2026-10-08"


# ── Trading risk ──────────────────────────────────────────────────────────────
def test_position_size_eurusd():
    out = ok("position_size", balance=10_000, risk_percent=1, stop_loss_pips=20, symbol="EURUSD")
    assert out["lots"] == 0.5 and out["risk_amount"] == 100


def test_position_size_gold_and_jpy():
    gold = ok("position_size", balance=1000, risk_percent=1, stop_loss_pips=50, symbol="XAUUSD")
    assert gold["lots"] == 0.02 and gold["pip_value_per_lot"] == 10
    jpy = ok("position_size", balance=10_000, risk_percent=1, stop_loss_pips=20, symbol="USDJPY", price=150)
    assert jpy["lots"] == 0.75 and not jpy["warnings"]
    assert ok("position_size", balance=10_000, risk_percent=1, stop_loss_pips=20, symbol="EURGBP")["warnings"]


def test_risk_reward():
    out = ok("risk_reward", entry=1.1000, stop_loss=1.0980, take_profit=1.1040)
    assert out["ratio"] == "1:2.00" and out["breakeven_win_rate_percent"] == pytest.approx(33.3)
    assert run("risk_reward", entry=1.1, stop_loss=1.12, take_profit=1.14)["ok"] is False


def test_trading_levels_and_stats():
    fib = ok("fibonacci_levels", high=2000, low=1900)["retracement"]
    assert fib["61.8%"] == pytest.approx(1938.2)
    pivots = ok("pivot_points", high=110, low=90, close=100)["levels"]
    assert pivots["P"] == 100 and pivots["R1"] == 110
    stats = ok("drawdown_stats", values=[100, -50, -50, 200], starting_balance=1000)
    assert stats["max_drawdown"] == 100 and stats["win_rate_percent"] == 50 and stats["max_losing_streak"] == 2
    assert ok("pip_converter", value=0.0025, from_unit="price")["pips"] == 25
    assert ok("margin_calc", lots=1, price=1.1, leverage=100)["margin_required"] == 1100


# ── Maps and places ───────────────────────────────────────────────────────────
def test_geo_distance_jakarta_bandung():
    out = ok("geo_distance", points=["-6.1754, 106.8272", "-6.9175, 107.6191"])
    assert 118 < out["total_km"] < 123 and out["legs"][0]["arah"] == "tenggara"


def test_parse_point_formats():
    from tool.execute.execute_toolbox.geo_maps_tools import parse_point

    assert parse_point("6°10'31.4\"S 106°49'37.9\"E") == pytest.approx((-6.17539, 106.82719), abs=1e-4)
    assert parse_point([-6.2, 106.8]) == (-6.2, 106.8)
    assert parse_point({"lat": 1, "lng": 2}) == (1, 2)
    with pytest.raises(ValueError, match="latitude first"):
        parse_point("106.8, -6.2")


def test_coord_convert_and_area_and_links():
    assert ok("coord_convert", value="-6.175392, 106.827153")["dms"].startswith("6°10'")
    area = ok("geo_area", points=["0,0", "0,2"], radius_km=10)
    assert area["midpoint"] == pytest.approx([0, 1], abs=1e-6)
    link = ok("maps_link", origin="Monas", query="Kota Tua", mode="walking")
    assert link["directions"].startswith("https://www.google.com/maps/dir/?api=1&origin=Monas&destination=Kota+Tua")
    assert run("maps_link")["ok"] is False


GMAPS_CSV = (
    "title,category,address,website,phone,emails,review_rating,review_count,latitude,longtitude,place_id,link,status\n"
    "Klinik Gigi Sehat,Dentist,\"Jl. Merdeka 1, Kota Bandung, Jawa Barat\",https://sehat.example/?utm=1,0812000,a@b.c,4.7,900,-6.91,107.61,P1,https://maps/1,\n"
    "Klinik Gigi Sehat,Dentist,\"Jl. Dago 9, Kota Bandung, Jawa Barat\",,0812001,,4.9,40,-6.88,107.61,P2,,\n"
    "Klinik Gigi Sehat,Dentist,\"Jl. Merdeka 1, Kota Bandung, Jawa Barat\",https://sehat.example,0812000,,4.7,900,-6.91,107.61,P1,,\n"
    "Dental Baru,Dentist,\"Jl. Asia Afrika, Kota Bandung, Jawa Barat\",,,,5.0,3,-6.92,107.60,P3,,\n"
    "Tutup Dental,Dentist,\"Jl. X, Kota Bandung, Jawa Barat\",,,,4.0,10,-6.93,107.62,P4,,permanently closed\n"
    ",Dentist,no name,,,,,,,,,,\n"
)


@pytest.fixture()
def places(ws):
    (ws / "hasil.csv").write_text(GMAPS_CSV, encoding="utf-8")
    out = ok("gmaps_import", path="hasil.csv", query="klinik gigi bandung")
    return ws, out


def test_gmaps_import_cleans_dedupes_and_keeps_contacts_out(places):
    ws, out = places
    assert out["places"] == 4 and out["duplicates_removed"] == 1 and out["skipped_rows"] == 1
    assert out["with_coordinates"] == 4 and out["contacts_kept"] is False
    stored = (ws / ".agent_memory" / "places" / "klinik-gigi-bandung.json").read_text(encoding="utf-8")
    assert "0812000" not in stored and "a@b.c" not in stored
    notes = list((ws / ".vault").rglob("*.md"))
    assert len([n for n in notes if "klinik-gigi-sehat" in n.name]) == 2


def test_gosom_longtitude_column_is_read(places):
    out = ok("places_search", text="dago")
    assert out["places"][0]["lon"] == pytest.approx(107.61)


def test_places_search_ranks_by_bayesian_score_not_raw_stars(places):
    names = [p["name"] for p in ok("places_search", sort="score")["places"]]
    assert names[0] == "Klinik Gigi Sehat" and "Tutup Dental" not in names
    assert ok("places_search", sort="rating")["places"][0]["name"] == "Dental Baru"


def test_places_search_by_distance_and_website(places):
    near = ok("places_search", near="-6.88, 107.61", sort="distance", radius_km=2)
    assert near["places"][0]["address"].startswith("Jl. Dago") and near["matched"] == 1
    leads = ok("places_search", has_website=False)
    assert all("website_domain" not in p for p in leads["places"]) and leads["matched"] == 2


def test_places_stats_and_export(places):
    ws, _ = places
    stats = ok("places_stats")
    assert stats["count"] == 4 and stats["cities"] == {"Bandung": 4} and stats["with_website_percent"] == 25.0
    out = ok("places_export", output="hasil/leads.geojson")
    geo = json.loads((ws / "hasil" / "leads.geojson").read_text(encoding="utf-8"))
    # The permanently closed clinic is left out of an export by default.
    assert out["places"] == 3 and len(geo["features"]) == 3
    assert ok("places_export", output="hasil/leads.xlsx", has_website=False)["places"] == 2
    assert ok("places_datasets")["datasets"][0]["name"] == "klinik-gigi-bandung"


def test_places_need_an_import_first(ws):
    out = run("places_search", text="x")
    assert out["ok"] is False and "gmaps_import" in out["error"]


def test_places_are_per_session(tmp_path):
    from utils import workspace_context

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "hasil.csv").write_text(GMAPS_CSV, encoding="utf-8")
    token = workspace_context._active_workspace.set(str(a))
    try:
        ok("gmaps_import", path="hasil.csv", query="q", to_vault=False)
    finally:
        workspace_context._active_workspace.reset(token)
    token = workspace_context._active_workspace.set(str(b))
    try:
        assert run("places_search")["ok"] is False
    finally:
        workspace_context._active_workspace.reset(token)


def test_gmaps_import_reads_json_lines(ws):
    rows = [{"title": "Kafe A", "categories": ["Cafe", "Coffee"], "review_rating": 4.5, "review_count": 10,
             "complete_address": {"street": "Jl. A", "city": "Depok"}, "latitude": -6.4, "longtitude": 106.8}]
    (ws / "out.json").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = ok("gmaps_import", path="out.json", query="kafe depok", to_vault=False)
    assert out["places"] == 1
    hit = ok("places_search", city="depok")["places"][0]
    assert hit["category"] == "Cafe, Coffee" and hit["city"] == "Depok"


def test_gmaps_scrape_explains_when_not_installed(ws, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.delenv("GMAPS_SCRAPER_BIN", raising=False)
    out = run("gmaps_scrape", queries=["klinik gigi bandung"])
    assert out["ok"] is False and "install" in out and "gmaps_import" in out["alternative"]


# ── Live tools: offline, with the network stubbed ─────────────────────────────
@pytest.fixture()
def fake_net(monkeypatch):
    from tool.execute.execute_toolbox import _net

    calls: list[tuple[str, str]] = []
    answers: dict[str, Any] = {}

    async def fake(host: str, path: str, params: dict[str, Any] | None = None, **_: Any) -> Any:
        calls.append((host, path))
        for key, value in answers.items():
            if key in f"{host}{path}":
                return value
        raise _net.NetError("no fake answer")

    monkeypatch.setattr(_net, "get_json", fake)
    return answers, calls


def test_geocode_and_reverse(fake_net):
    answers, _ = fake_net
    answers["nominatim.openstreetmap.org/search"] = [{"display_name": "Monas", "lat": "-6.1754", "lon": "106.8272", "type": "monument"}]
    answers["nominatim.openstreetmap.org/reverse"] = {"display_name": "Gambir, Jakarta", "address": {"city": "Jakarta"}}
    out = ok("geocode", query="Monas")
    assert out["results"][0]["lat"] == -6.1754 and "google.com/maps" in out["results"][0]["google_maps"]
    assert ok("reverse_geocode", point="-6.1754,106.8272")["address"] == "Gambir, Jakarta"


def test_osm_nearby_sorts_by_distance_and_accepts_indonesian(fake_net):
    answers, _ = fake_net
    answers["overpass-api.de"] = {"elements": [
        {"lat": -6.20, "lon": 106.80, "tags": {"name": "Apotek Jauh"}},
        {"center": {"lat": -6.1755, "lon": 106.8273}, "tags": {"name": "Apotek Dekat"}},
    ]}
    out = ok("osm_nearby", kind="apotek", near="-6.1754,106.8272")
    assert out["kind"] == "pharmacy" and out["places"][0]["name"] == "Apotek Dekat"
    assert run("osm_nearby", kind="spaceport", near="0,0")["ok"] is False


def test_weather(fake_net):
    answers, _ = fake_net
    answers["geocoding-api.open-meteo.com"] = {"results": [{"latitude": -6.9, "longitude": 107.6, "name": "Bandung", "country": "Indonesia"}]}
    answers["api.open-meteo.com"] = {"timezone": "Asia/Jakarta", "current": {"temperature_2m": 24, "weather_code": 61},
                                     "daily": {"time": ["2026-11-09"], "weather_code": [63], "temperature_2m_min": [19],
                                               "temperature_2m_max": [28], "precipitation_sum": [12],
                                               "precipitation_probability_max": [80], "uv_index_max": [9]}}
    out = ok("weather", place="Bandung")
    assert out["now"]["condition"] == "hujan ringan" and out["forecast"][0]["rain_chance_percent"] == 80


def test_currency_holidays_wiki(fake_net):
    answers, _ = fake_net
    answers["api.frankfurter.app/latest"] = {"date": "2026-11-06", "rates": {"IDR": 16000.0}}
    assert ok("currency_convert", amount=10, from_currency="usd", to_currency=["IDR"])["converted"]["IDR"] == 160000
    answers["date.nager.at"] = [{"date": "2026-08-17", "localName": "Hari Kemerdekaan", "name": "Independence Day"}]
    hol = ok("public_holidays", year=2026)
    assert hol["holidays"][0]["weekday"] == "Monday" and hol["long_weekends"]
    answers["id.wikipedia.org"] = {"title": "Monas", "extract": "Monumen Nasional...", "content_urls": {"desktop": {"page": "u"}}}
    assert ok("wiki_summary", topic="Monas")["language"] == "id"


def test_live_tools_fail_readably_offline(fake_net):
    out = run("weather", place="-6.9,107.6")
    assert out["ok"] is False and "weather" in out["error"]


def test_net_refuses_unlisted_hosts():
    from tool.execute.execute_toolbox import _net

    with pytest.raises(_net.NetError, match="not an allowed host"):
        asyncio.run(_net.get_json("169.254.169.254", "/latest/meta-data"))


def test_net_cache_is_keyed_by_session(monkeypatch):
    from tool.execute.execute_toolbox import _net

    _net.clear_cache()
    hits = []

    class FakeResponse:
        status_code = 200

        def json(self) -> Any:
            return {"n": len(hits)}

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None: ...
        async def __aenter__(self) -> FakeClient:
            return self
        async def __aexit__(self, *a: Any) -> None: ...
        async def get(self, url: str, params: Any = None) -> FakeResponse:
            hits.append(url)
            return FakeResponse()

    monkeypatch.setattr(_net.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(_net, "get_active_session", lambda: "s1")
    asyncio.run(_net.get_json("api.frankfurter.app", "/latest"))
    asyncio.run(_net.get_json("api.frankfurter.app", "/latest"))
    monkeypatch.setattr(_net, "get_active_session", lambda: "s2")
    asyncio.run(_net.get_json("api.frankfurter.app", "/latest"))
    assert len(hits) == 2
    _net.clear_cache()


def test_every_network_tool_is_marked():
    for name in ("geocode", "reverse_geocode", "osm_nearby", "weather", "currency_convert", "public_holidays",
                 "wiki_summary", "dictionary_en", "gmaps_scrape"):
        assert CATALOG[name].network, name
    from tool.execute.execute_toolbox import read_only_tools

    assert "weather" not in read_only_tools() and "calc" in read_only_tools()


def test_deferred_names_cover_the_catalog():
    assert deferred_names() == frozenset(CATALOG)
