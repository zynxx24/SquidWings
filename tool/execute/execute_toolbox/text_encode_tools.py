"""Text analysis and transformation, encodings, hashes, and random values."""

from __future__ import annotations

import base64
import binascii
import codecs
import difflib
import hashlib
import html
import json
import re
import secrets
import string
import unicodedata
import uuid
from collections import Counter
from typing import Any
from urllib.parse import quote, quote_plus, unquote, unquote_plus

from .catalog import group, param, spec

group("text", "Text analysis and editing",
      "teks, text, kata, word, kalimat, sentence, paragraf, paragraph, karakter, character, huruf, letter, "
      "jumlah kata, word count, regex, regexp, pola, pattern, bandingkan teks, compare text, diff, duplikat, "
      "urutkan, sort, kapital, uppercase, lowercase, camelcase, snake_case, slug, ekstrak, extract, email, url")
group("encode", "Encoding, hashing and random values",
      "base64, encode, decode, enkode, dekode, url encode, hash, md5, sha1, sha256, sha512, checksum, uuid, guid, "
      "password, kata sandi, sandi, acak, random, lempar dadu, dice, koin, coin, undian, jwt, token, hex, morse, rot13, "
      "caesar, unicode, emoji")

_MAX_TEXT = 2_000_000


def _text(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) > _MAX_TEXT:
        raise ValueError("text longer than 2 MB; read the file in parts")
    return text


_SENT = re.compile(r"[^.!?…]+[.!?…]+|[^.!?…]+$")
_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


@spec("text_stats", "Count characters (with and without spaces), words, sentences, paragraphs, lines, unique words, "
      "average word and sentence length, and reading/speaking time. Exact, unlike counting by eye.",
      "text", [param("text"), param("words_per_minute", "int", required=False, default=200)],
      keywords="jumlah kata, word count, hitung kata, berapa kata, karakter, characters, panjang teks, reading time", lead=True)
def text_stats(text: str, words_per_minute: int = 200) -> dict[str, Any]:
    t = _text(text)
    words = _WORD.findall(t)
    sentences = [s for s in _SENT.findall(t) if s.strip()]
    paragraphs = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    wpm = max(int(words_per_minute), 1)
    return {"characters": len(t), "characters_no_spaces": len(re.sub(r"\s", "", t)), "words": len(words),
            "unique_words": len({w.lower() for w in words}), "sentences": len(sentences), "paragraphs": len(paragraphs),
            "lines": t.count("\n") + 1 if t else 0,
            "avg_word_length": round(sum(map(len, words)) / len(words), 2) if words else 0,
            "avg_sentence_words": round(len(words) / len(sentences), 2) if sentences else 0,
            "reading_minutes": round(len(words) / wpm, 2), "speaking_minutes": round(len(words) / 130, 2)}


_STOP = set(["yang", "dan", "di", "ke", "dari", "ini", "itu", "dengan", "untuk", "pada", "adalah", "dalam", "tidak", "akan", "juga", "atau", "ada", "karena", "oleh", "sebagai", "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that", "for", "on", "with", "as", "was", "are", "be", "this", "by", "at", "from"])


@spec("word_frequency", "Most frequent words (or n-grams) in a text, optionally without common Indonesian and English stop words.",
      "text", [param("text"), param("top", "int", required=False, default=20), param("ngram", "int", "1 for words, 2 for word pairs.", required=False, default=1),
               param("skip_stopwords", "bool", required=False, default=True)],
      keywords="frekuensi kata, word frequency, kata paling sering, most common words, kata kunci, keywords")
def word_frequency(text: str, top: int = 20, ngram: int = 1, skip_stopwords: bool = True) -> dict[str, Any]:
    words = [w.lower() for w in _WORD.findall(_text(text))]
    if skip_stopwords:
        words = [w for w in words if w not in _STOP]
    n = max(1, min(int(ngram), 5))
    grams = [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
    return {"total": len(grams), "top": [{"term": t, "count": c} for t, c in Counter(grams).most_common(int(top))]}


_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


def _regex(pattern: str, flags: str) -> re.Pattern[str]:
    value = 0
    for f in flags or "":
        if f.lower() not in _FLAGS:
            raise ValueError(f"unknown flag {f}; use i, m, s, x")
        value |= _FLAGS[f.lower()]
    if len(pattern) > 2000:
        raise ValueError("pattern too long")
    try:
        return re.compile(pattern, value)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc


@spec("regex_test", "Test a regular expression on text: every match with its position and groups, or a replacement preview. "
      "Use before writing a regex into code.",
      "text", [param("pattern"), param("text"), param("flags", description="Any of i, m, s, x.", required=False, default=""),
               param("replace", description="If given, also return the text with matches replaced (\\1 for groups).", required=False),
               param("max_matches", "int", required=False, default=50)],
      keywords="regex, regexp, regular expression, pola, pattern, cocokkan, match", lead=True)
def regex_test(pattern: str, text: str, flags: str = "", replace: str | None = None, max_matches: int = 50) -> dict[str, Any]:
    rx = _regex(pattern, flags)
    t = _text(text)
    matches = []
    for m in rx.finditer(t):
        matches.append({"match": m.group(0), "start": m.start(), "end": m.end(), "groups": list(m.groups()),
                        "named": m.groupdict()})
        if len(matches) >= int(max_matches):
            break
    out: dict[str, Any] = {"count": len(matches), "matches": matches}
    if replace is not None:
        out["replaced"] = rx.sub(replace, t)
    return out


@spec("text_diff", "Line-by-line difference between two texts as a unified diff, plus a similarity ratio.",
      "text", [param("a", description="Original text."), param("b", description="New text."),
               param("context", "int", required=False, default=2)],
      keywords="diff, perbedaan, beda, bandingkan teks, compare text, perubahan, changes", lead=True)
def text_diff(a: str, b: str, context: int = 2) -> dict[str, Any]:
    la, lb = _text(a).splitlines(), _text(b).splitlines()
    diff = list(difflib.unified_diff(la, lb, "a", "b", n=int(context), lineterm=""))
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    return {"similarity": round(ratio, 4), "added_lines": added, "removed_lines": removed, "diff": "\n".join(diff[:2000])}


@spec("text_similarity", "How alike two strings are (0-1): character ratio, word overlap (Jaccard), and Levenshtein edit distance.",
      "text", [param("a"), param("b"), param("ignore_case", "bool", required=False, default=True)],
      keywords="kemiripan, similarity, mirip, similar, plagiat, plagiarism, levenshtein, edit distance")
def text_similarity(a: str, b: str, ignore_case: bool = True) -> dict[str, Any]:
    x, y = (_text(a).lower(), _text(b).lower()) if ignore_case else (_text(a), _text(b))
    wa, wb = set(_WORD.findall(x)), set(_WORD.findall(y))
    jaccard = len(wa & wb) / len(wa | wb) if wa | wb else 1.0
    return {"ratio": round(difflib.SequenceMatcher(None, x, y).ratio(), 4), "word_jaccard": round(jaccard, 4),
            "levenshtein": _levenshtein(x[:5000], y[:5000])}


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _split_words(text: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


@spec("case_convert", "Convert text case: upper, lower, title, sentence, camel, pascal, snake, kebab, constant, "
      "or all of them at once. Handles identifiers like 'userIDValue'.",
      "text", [param("text"), param("to", enum=["upper", "lower", "title", "sentence", "camel", "pascal", "snake", "kebab", "constant",
                                         "slug", "all"], required=False, default="all")],
      keywords="camelcase, camel case, snake_case, snake case, kebab, pascalcase, huruf besar, huruf kecil, kapital, title case, slug")
def case_convert(text: str, to: str = "all") -> dict[str, Any]:
    t = _text(text)
    words = _split_words(unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode())
    lw = [w.lower() for w in words]
    forms = {
        "upper": t.upper(), "lower": t.lower(), "title": t.title(),
        "sentence": t[:1].upper() + t[1:].lower() if t else t,
        "camel": (lw[0] + "".join(w.capitalize() for w in lw[1:])) if lw else "",
        "pascal": "".join(w.capitalize() for w in lw), "snake": "_".join(lw), "kebab": "-".join(lw),
        "constant": "_".join(lw).upper(), "slug": "-".join(lw)[:120],
    }
    return {"result": forms} if to == "all" else {"result": forms[to]}


@spec("lines_tool", "Line operations on text: sort, sort_numeric, reverse, unique (keep first), duplicates (list repeated lines), "
      "shuffle, number, trim, remove_empty, count.",
      "text", [param("text"), param("operation", enum=["sort", "sort_numeric", "reverse", "unique", "duplicates", "shuffle", "number",
                                                "trim", "remove_empty", "count"]),
               param("ignore_case", "bool", required=False, default=False), param("descending", "bool", required=False, default=False)],
      keywords="urutkan baris, sort lines, hapus duplikat, remove duplicates, baris unik, unique lines, nomori, number lines")
def lines_tool(text: str, operation: str, ignore_case: bool = False, descending: bool = False) -> dict[str, Any]:
    lines = _text(text).splitlines()
    key = (lambda s: s.lower()) if ignore_case else None
    op = operation.lower()
    if op == "sort":
        out = sorted(lines, key=key, reverse=descending)
    elif op == "sort_numeric":
        def num(s: str) -> float:
            m = re.search(r"-?\d+(?:[.,]\d+)?", s)
            return float(m.group(0).replace(",", ".")) if m else float("inf")
        out = sorted(lines, key=num, reverse=descending)
    elif op == "reverse":
        out = list(reversed(lines))
    elif op == "unique":
        seen: set[str] = set()
        out = []
        for line in lines:
            k = line.lower() if ignore_case else line
            if k not in seen:
                seen.add(k)
                out.append(line)
    elif op == "duplicates":
        counts = Counter(line.lower() if ignore_case else line for line in lines)
        return {"duplicates": [{"line": k, "count": c} for k, c in counts.items() if c > 1]}
    elif op == "shuffle":
        out = list(lines)
        secrets.SystemRandom().shuffle(out)
    elif op == "number":
        out = [f"{i}. {line}" for i, line in enumerate(lines, 1)]
    elif op == "trim":
        out = [line.strip() for line in lines]
    elif op == "remove_empty":
        out = [line for line in lines if line.strip()]
    elif op == "count":
        return {"lines": len(lines), "non_empty": sum(1 for line in lines if line.strip())}
    else:
        raise ValueError("unknown operation")
    return {"result": "\n".join(out), "lines": len(out)}


_PATTERNS = {
    "emails": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "urls": r"https?://[^\s<>\"')\]]+",
    "phones": r"(?:\+?62|0)[\s.-]?8\d{1,3}[\s.-]?\d{3,4}[\s.-]?\d{3,5}|\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}",
    "numbers": r"-?\d+(?:[.,]\d+)*",
    "hashtags": r"(?<!\w)#\w+",
    "mentions": r"(?<!\w)@\w+",
    "dates": r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b",
    "money": r"(?:Rp\.?|IDR|\$|USD|€|EUR)\s?\d[\d.,]*(?:\s?(?:rb|ribu|jt|juta|m|miliar|k))?",
    "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}


@spec("extract_patterns", "Pull every email, URL, phone number, number, hashtag, mention, date, money amount, or IPv4 "
      "address out of a text. Returns unique values in order.",
      "text", [param("text"), param("kinds", "list", "Which to extract; default all.", required=False, items="str")],
      keywords="ekstrak email, extract emails, ambil link, extract urls, nomor telepon, phone numbers, ambil angka", lead=True)
def extract_patterns(text: str, kinds: list[str] | None = None) -> dict[str, Any]:
    t = _text(text)
    wanted = kinds or list(_PATTERNS)
    out: dict[str, Any] = {}
    for kind in wanted:
        if kind not in _PATTERNS:
            raise ValueError(f"unknown kind {kind}; use {', '.join(_PATTERNS)}")
        found = list(dict.fromkeys(m.group(0).rstrip(".,;:") for m in re.finditer(_PATTERNS[kind], t, re.IGNORECASE)))
        out[kind] = found[:500]
    return {"found": out}


_INVISIBLE = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad]")
_QUOTES = {ord(k): v for k, v in {"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'",
                                  "\u2013": "-", "\u2014": "-", "\u2026": "..."}.items()}


@spec("text_clean", "Clean text: collapse spaces, strip HTML tags, remove accents, remove emoji, normalise quotes and dashes, "
      "fix line endings, remove zero-width characters. Choose operations or use all.",
      "text", [param("text"), param("operations", "list", "Any of spaces, html, accents, emoji, quotes, invisible, lines; default all safe ones.",
                             required=False, items="str")],
      keywords="bersihkan teks, clean text, hapus spasi, remove spaces, hapus html, strip html, hapus emoji, remove emoji")
def text_clean(text: str, operations: list[str] | None = None) -> dict[str, Any]:
    t = _text(text)
    ops = operations or ["invisible", "quotes", "lines", "spaces"]
    if "html" in ops:
        t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    if "invisible" in ops:
        t = _INVISIBLE.sub("", t)
    if "quotes" in ops:
        t = t.translate(_QUOTES)
    if "accents" in ops:
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    if "emoji" in ops:
        t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)
    if "lines" in ops:
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"\n{3,}", "\n\n", t)
    if "spaces" in ops:
        t = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in t.split("\n"))
    return {"result": t.strip(), "chars_before": len(_text(text)), "chars_after": len(t.strip())}


@spec("text_template", "Fill a template with values: {name} placeholders, for each row of data. Good for many similar "
      "messages or letters. Missing keys are reported, not silently left blank.",
      "text", [param("template", description="e.g. 'Halo {nama}, tagihanmu {jumlah}.'"),
               param("rows", "list", "One object per output, e.g. [{\"nama\": \"Ani\", \"jumlah\": \"Rp10.000\"}].", items="obj")],
      keywords="template, mail merge, isi template, placeholder, surat massal, bulk message")
def text_template(template: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = set(re.findall(r"{(\w+)}", template))
    out, problems = [], []
    for i, row in enumerate(rows[:1000]):
        missing = keys - set(row)
        if missing:
            problems.append({"row": i, "missing": sorted(missing)})
        out.append(_fill(template, row))
    return {"results": out, "placeholders": sorted(keys), "problems": problems}


def _fill(template: str, row: dict[str, Any]) -> str:
    def value(m: re.Match[str]) -> str:
        return str(row.get(m.group(1), "{" + m.group(1) + "}"))

    return re.sub(r"{(\w+)}", value, template)


@spec("text_wrap", "Wrap text to a width, truncate it to a length with an ellipsis, or split it into chunks of N characters "
      "(for SMS, tweets, or model context).",
      "text", [param("text"), param("mode", enum=["wrap", "truncate", "chunks"], required=False, default="wrap"),
               param("width", "int", required=False, default=80)],
      keywords="potong teks, truncate, wrap, bungkus, pecah teks, split text, chunk, batas karakter, character limit")
def text_wrap(text: str, mode: str = "wrap", width: int = 80) -> dict[str, Any]:
    import textwrap

    t, w = _text(text), max(int(width), 1)
    if mode == "wrap":
        return {"result": "\n".join(textwrap.fill(p, w) for p in t.split("\n"))}
    if mode == "truncate":
        return {"result": t if len(t) <= w else t[: max(w - 1, 0)].rstrip() + "…", "truncated": len(t) > w}
    chunks = textwrap.wrap(t, w, break_long_words=True, replace_whitespace=False)
    return {"chunks": chunks, "count": len(chunks)}


# ── Encodings ─────────────────────────────────────────────────────────────────
@spec("encode_decode", "Encode or decode text: base64, base64url, base32, hex, url (percent), url_plus, html entities, "
      "unicode escapes, rot13, binary.",
      "encode", [param("text"), param("codec", enum=["base64", "base64url", "base32", "hex", "url", "url_plus", "html", "unicode_escape",
                                              "rot13", "binary"]),
                 param("direction", enum=["encode", "decode"], required=False, default="encode")],
      keywords="base64, encode, decode, enkode, dekode, urlencode, url encode, percent encoding, html entity, hex", lead=True)
def encode_decode(text: str, codec: str, direction: str = "encode") -> dict[str, Any]:
    t = _text(text)
    enc = direction == "encode"
    try:
        if codec == "base64":
            r = base64.b64encode(t.encode()).decode() if enc else base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "replace")
        elif codec == "base64url":
            r = base64.urlsafe_b64encode(t.encode()).decode() if enc else base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "replace")
        elif codec == "base32":
            r = base64.b32encode(t.encode()).decode() if enc else base64.b32decode(t.upper() + "=" * (-len(t) % 8)).decode("utf-8", "replace")
        elif codec == "hex":
            r = t.encode().hex() if enc else bytes.fromhex(re.sub(r"[\s:]|0x", "", t)).decode("utf-8", "replace")
        elif codec == "url":
            r = quote(t, safe="") if enc else unquote(t)
        elif codec == "url_plus":
            r = quote_plus(t) if enc else unquote_plus(t)
        elif codec == "html":
            r = html.escape(t) if enc else html.unescape(t)
        elif codec == "unicode_escape":
            r = t.encode("unicode_escape").decode("ascii") if enc else codecs.decode(t, "unicode_escape")
        elif codec == "rot13":
            r = codecs.encode(t, "rot13")
        elif codec == "binary":
            r = " ".join(f"{b:08b}" for b in t.encode()) if enc else bytes(int(x, 2) for x in t.split()).decode("utf-8", "replace")
        else:
            raise ValueError("unknown codec")
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"cannot {direction} as {codec}: {exc}") from exc
    return {"result": r}


@spec("hash_text", "Hash text with md5, sha1, sha256, sha512, sha3_256, blake2b, or crc32. Optional HMAC with a key.",
      "encode", [param("text"), param("algorithm", enum=["md5", "sha1", "sha256", "sha512", "sha3_256", "blake2b", "crc32", "all"],
                               required=False, default="sha256"),
                 param("hmac_key", description="Set to compute an HMAC instead.", required=False)],
      keywords="hash, md5, sha1, sha256, sha512, checksum, hmac, sidik jari, fingerprint", lead=True)
def hash_text(text: str, algorithm: str = "sha256", hmac_key: str | None = None) -> dict[str, Any]:
    import hmac
    import zlib

    data = _text(text).encode()
    algos = ["md5", "sha1", "sha256", "sha512", "sha3_256", "blake2b", "crc32"] if algorithm == "all" else [algorithm]
    out = {}
    for a in algos:
        if a == "crc32":
            out[a] = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
        elif hmac_key:
            out[a] = hmac.new(hmac_key.encode(), data, a).hexdigest()
        else:
            out[a] = hashlib.new(a, data).hexdigest()
    return {"hashes": out, "hmac": bool(hmac_key)}


@spec("uuid_generate", "Generate UUIDs (v4 random, v1 time, v5 name-based) or short random ids.",
      "encode", [param("version", enum=["4", "1", "5", "short"], required=False, default="4"),
                 param("count", "int", required=False, default=1), param("name", description="For v5: the name to hash.", required=False)],
      keywords="uuid, guid, id unik, unique id, random id")
def uuid_generate(version: str = "4", count: int = 1, name: str | None = None) -> dict[str, Any]:
    n = max(1, min(int(count), 100))
    v = str(version)
    if v == "5":
        if not name:
            raise ValueError("v5 needs name")
        return {"uuids": [str(uuid.uuid5(uuid.NAMESPACE_URL, name))]}
    make = {"4": lambda: str(uuid.uuid4()), "1": lambda: str(uuid.uuid1()), "short": lambda: secrets.token_urlsafe(8)}[v]
    return {"uuids": [make() for _ in range(n)]}


@spec("password_generate", "Generate strong random passwords or passphrases (cryptographically secure), with an entropy estimate. "
      "Also rates an existing password when 'check' is given.",
      "encode", [param("length", "int", required=False, default=16), param("count", "int", required=False, default=1),
                 param("symbols", "bool", required=False, default=True), param("passphrase_words", "int", "If > 0, make a passphrase.",
                                                                        required=False, default=0),
                 param("check", description="A password to rate instead.", required=False)],
      keywords="password, kata sandi, sandi, passphrase, generate password, buat password, password kuat, strong password", lead=True)
def password_generate(length: int = 16, count: int = 1, symbols: bool = True, passphrase_words: int = 0,
                      check: str | None = None) -> dict[str, Any]:
    import math

    if check is not None:
        pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, string.punctuation]
        size = sum(len(p) for p in pools if any(c in p for c in check)) or 1
        bits = len(check) * math.log2(size)
        rating = "weak" if bits < 40 else "fair" if bits < 60 else "strong" if bits < 90 else "very strong"
        return {"length": len(check), "entropy_bits": round(bits, 1), "rating": rating,
                "common_pattern": bool(re.search(r"(1234|password|qwerty|admin|sayang|bismillah)", check.lower()))}
    n = max(1, min(int(count), 50))
    if int(passphrase_words) > 0:
        words = ["api", "air", "batu", "bunga", "daun", "elang", "gajah", "hujan", "ikan", "jeruk", "kopi", "langit", "malam", "nanas", "ombak", "padi", "rusa", "sapi", "teh", "udara", "volt", "wortel", "yoga", "zebra", "awan", "bulan", "cahaya", "dinding", "embun"]
        k = max(3, min(int(passphrase_words), 12))
        return {"passwords": ["-".join(secrets.choice(words) for _ in range(k)) + str(secrets.randbelow(100)) for _ in range(n)],
                "entropy_bits": round(k * math.log2(len(words)) + math.log2(100), 1)}
    length = max(8, min(int(length), 256))
    alphabet = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{}?" if symbols else "")
    out = []
    for _ in range(n):
        while True:
            pw = "".join(secrets.choice(alphabet) for _ in range(length))
            if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
                break
        out.append(pw)
    return {"passwords": out, "entropy_bits": round(length * math.log2(len(alphabet)), 1)}


@spec("random_pick", "Fair randomness: roll dice ('2d6'), flip coins, pick from a list (lottery, giveaway), shuffle a list, "
      "random integer in a range, or split names into teams.",
      "encode", [param("mode", enum=["dice", "coin", "choice", "shuffle", "integer", "teams"]),
                 param("items", "list", "Items for choice/shuffle/teams.", required=False, items="str"),
                 param("count", "int", "How many picks / teams / coins.", required=False, default=1),
                 param("dice", description="Dice like '2d6' or '1d20'.", required=False, default="1d6"),
                 param("low", "int", required=False, default=1), param("high", "int", required=False, default=100)],
      keywords="acak, random, undian, kocok, lempar dadu, dice, koin, coin flip, pilih acak, random pick, bagi tim, split teams, giveaway", lead=True)
def random_pick(mode: str, items: list[str] | None = None, count: int = 1, dice: str = "1d6", low: int = 1,
                high: int = 100) -> dict[str, Any]:
    rng = secrets.SystemRandom()
    n = max(1, min(int(count), 1000))
    if mode == "dice":
        m = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", dice.strip().lower())
        if not m:
            raise ValueError("dice like 2d6 or 1d20+3")
        k, sides, bonus = int(m.group(1) or 1), int(m.group(2)), int(m.group(3) or 0)
        if not (1 <= k <= 100 and 2 <= sides <= 1000):
            raise ValueError("1-100 dice with 2-1000 sides")
        rolls = [rng.randint(1, sides) for _ in range(k)]
        return {"rolls": rolls, "total": sum(rolls) + bonus}
    if mode == "coin":
        flips = [rng.choice(["heads/angka", "tails/gambar"]) for _ in range(n)]
        return {"flips": flips}
    if mode == "integer":
        return {"numbers": [rng.randint(int(low), int(high)) for _ in range(n)]}
    pool = list(items or [])
    if not pool:
        raise ValueError(f"{mode} needs items")
    if mode == "choice":
        if n > len(pool):
            raise ValueError("count is larger than the list")
        return {"picked": rng.sample(pool, n)}
    if mode == "shuffle":
        rng.shuffle(pool)
        return {"shuffled": pool}
    if mode == "teams":
        rng.shuffle(pool)
        teams = [pool[i::n] for i in range(n)]
        return {"teams": teams}
    raise ValueError("unknown mode")


@spec("jwt_decode", "Decode a JWT's header and payload WITHOUT verifying the signature, and explain exp/iat/nbf times. "
      "For debugging only; never trust an unverified token.",
      "encode", [param("token")], keywords="jwt, json web token, bearer token, decode token")
def jwt_decode(token: str) -> dict[str, Any]:
    from datetime import UTC, datetime

    parts = token.strip().removeprefix("Bearer ").split(".")
    if len(parts) < 2:
        raise ValueError("a JWT has three dot-separated parts")

    def part(p: str) -> Any:
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))

    header, payload = part(parts[0]), part(parts[1])
    times = {}
    for key in ("exp", "iat", "nbf"):
        if isinstance(payload.get(key), (int, float)):
            times[key] = datetime.fromtimestamp(payload[key], UTC).isoformat()
    expired = "exp" in payload and isinstance(payload["exp"], (int, float)) and payload["exp"] < datetime.now(UTC).timestamp()
    return {"header": header, "payload": payload, "times_utc": times, "expired": expired, "signature_verified": False}


_MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..",
          "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
          "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
          "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
          "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "!": "-.-.--", "/": "-..-.", "@": ".--.-."}


@spec("cipher", "Classic ciphers for puzzles and lessons: morse, caesar (shift), atbash, vigenere. Encode or decode.",
      "encode", [param("text"), param("method", enum=["morse", "caesar", "atbash", "vigenere"]),
                 param("direction", enum=["encode", "decode"], required=False, default="encode"),
                 param("key", description="Shift for caesar (number) or keyword for vigenere.", required=False, default="3")],
      keywords="morse, sandi morse, caesar, atbash, vigenere, kriptografi, cipher, sandi rahasia")
def cipher(text: str, method: str, direction: str = "encode", key: str = "3") -> dict[str, Any]:
    t = _text(text)
    if method == "morse":
        if direction == "encode":
            return {"result": " / ".join(" ".join(_MORSE.get(c, "?") for c in w) for w in t.upper().split())}
        back = {v: k for k, v in _MORSE.items()}
        return {"result": " ".join("".join(back.get(c, "?") for c in w.split()) for w in t.split("/"))}

    def shift(c: str, s: int) -> str:
        if c.isalpha() and c.isascii():
            base = ord("A") if c.isupper() else ord("a")
            return chr((ord(c) - base + s) % 26 + base)
        return c

    sign = 1 if direction == "encode" else -1
    if method == "caesar":
        s = int(key) * sign
        return {"result": "".join(shift(c, s) for c in t)}
    if method == "atbash":
        return {"result": "".join(chr(155 - ord(c)) if "A" <= c <= "Z" else chr(219 - ord(c)) if "a" <= c <= "z" else c for c in t)}
    if method == "vigenere":
        k = [ord(c) - 65 for c in str(key).upper() if c.isalpha()]
        if not k:
            raise ValueError("vigenere needs a keyword of letters")
        out, i = [], 0
        for c in t:
            if c.isalpha() and c.isascii():
                out.append(shift(c, k[i % len(k)] * sign))
                i += 1
            else:
                out.append(c)
        return {"result": "".join(out)}
    raise ValueError("unknown method")


@spec("unicode_info", "Inspect characters: code point, name, category, UTF-8 bytes. Finds hidden or look-alike characters "
      "(zero-width spaces, Cyrillic 'а' in a Latin word).",
      "encode", [param("text"), param("only_unusual", "bool", "List only non-ASCII characters.", required=False, default=False)],
      keywords="unicode, karakter aneh, hidden character, zero width, homoglyph, emoji, codepoint, utf-8")
def unicode_info(text: str, only_unusual: bool = False) -> dict[str, Any]:
    chars = []
    for i, c in enumerate(_text(text)[:2000]):
        if only_unusual and c.isascii() and c.isprintable():
            continue
        chars.append({"index": i, "char": c, "codepoint": f"U+{ord(c):04X}", "name": unicodedata.name(c, "<unnamed>"),
                      "category": unicodedata.category(c), "utf8": c.encode().hex(" ")})
        if len(chars) >= 300:
            break
    scripts = Counter(unicodedata.name(c, "X").split()[0] for c in _text(text) if c.isalpha())
    return {"characters": chars, "scripts": dict(scripts), "mixed_scripts": len(scripts) > 1}
