"""Developer helpers: URLs, HTTP facts, colours, versions, networks, code checks, and project facts."""

from __future__ import annotations

import ast
import colorsys
import ipaddress
import json
import mimetypes
import re
import tomllib
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import _files
from .catalog import group, param, spec

group("dev", "Developer and web helpers",
      "status code, kode status, http status, mime, content-type, semver, versi paket, package version, cidr, subnet, "
      "netmask, ip address, alamat ip, port, kode warna, color code, hex color, rgb, hsl, kontras, contrast, wcag, "
      "px ke rem, rem, syntax error, sintaks, dependensi, dependency, requirements.txt, package.json, pyproject, gitignore")


@spec("url_parse", "Split a URL into scheme, host, port, path, query parameters and fragment; or rebuild it with changed or "
      "removed query parameters (e.g. strip utm_ tracking).",
      "dev", [param("url"), param("set_params", "obj", "Query params to add or change.", required=False),
              param("remove_params", "list", "Param names to drop; 'utm_*' style wildcards allowed.", required=False, items="str")],
      keywords="url, parse url, query string, parameter url, utm, link tracking, bersihkan link", lead=True)
def url_parse(url: str, set_params: dict[str, Any] | None = None, remove_params: list[str] | None = None) -> dict[str, Any]:
    parts = urlsplit(url.strip() if "://" in url else f"https://{url.strip()}")
    params = parse_qsl(parts.query, keep_blank_values=True)
    out: dict[str, Any] = {"scheme": parts.scheme, "host": parts.hostname, "port": parts.port, "path": parts.path,
                           "query": dict(params), "fragment": parts.fragment, "username": parts.username}
    if set_params or remove_params:
        patterns = [re.compile("^" + re.escape(p).replace(r"\*", ".*") + "$") for p in remove_params or []]
        kept = [(k, v) for k, v in params if not any(p.match(k) for p in patterns) and k not in (set_params or {})]
        kept += [(k, str(v)) for k, v in (set_params or {}).items()]
        out["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return out


@spec("http_status", "Meaning of an HTTP status code (e.g. 401, 403, 429, 502) and the usual causes and fixes.",
      "dev", [param("code", "int")], keywords="http status, status code, kode status, error http, http error, error 404, 404 not found, bad gateway, too many requests, unauthorized, forbidden")
def http_status(code: int) -> dict[str, Any]:
    tips = {400: "the request body or parameters are malformed", 401: "missing or invalid credentials/token",
            403: "authenticated but not allowed; check permissions, CORS, or a WAF", 404: "wrong URL or route; check the path and method",
            405: "the route exists but not for this HTTP method", 408: "the client was too slow sending the request",
            409: "conflict with the current state (duplicate, version mismatch)", 413: "body too large; raise the server limit",
            415: "wrong Content-Type", 422: "validation failed; read the error body for the field",
            429: "rate limited; back off and retry after Retry-After", 500: "server bug; read the server log",
            502: "the proxy got a bad answer from the upstream server (it crashed or is down)",
            503: "server overloaded or in maintenance", 504: "upstream timed out behind a proxy"}
    try:
        status = HTTPStatus(int(code))
    except ValueError as exc:
        raise ValueError(f"{code} is not a standard HTTP status") from exc
    kind = {1: "informational", 2: "success", 3: "redirect", 4: "client error", 5: "server error"}[int(code) // 100]
    return {"code": status.value, "phrase": status.phrase, "description": status.description, "class": kind,
            "usual_cause": tips.get(status.value, "")}


@spec("mime_type", "MIME type for a file name or extension, or the usual extensions for a MIME type.",
      "dev", [param("value", description="e.g. 'report.xlsx', '.webp', or 'application/json'.")],
      keywords="mime, content-type, content type, tipe file, file type")
def mime_type(value: str) -> dict[str, Any]:
    v = value.strip()
    if "/" in v and not v.startswith("."):
        return {"mime": v, "extensions": mimetypes.guess_all_extensions(v)}
    guess, encoding = mimetypes.guess_type(v if "." in v else f"x.{v}")
    return {"file": v, "mime": guess or "application/octet-stream", "encoding": encoding}


_SEMVER = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def _sv(v: str) -> tuple[int, int, int, tuple[Any, ...]]:
    m = _SEMVER.match(v.strip())
    if not m:
        raise ValueError(f"'{v}' is not a version like 1.2.3")
    pre = tuple(int(p) if p.isdigit() else p for p in (m.group(4) or "").split(".") if p)
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), pre


def _cmp(a: str, b: str) -> int:
    x, y = _sv(a), _sv(b)
    if x[:3] != y[:3]:
        return -1 if x[:3] < y[:3] else 1
    if x[3] == y[3]:
        return 0
    if not x[3]:
        return 1
    if not y[3]:
        return -1
    for p, q in zip(x[3], y[3], strict=False):
        if p != q:
            if isinstance(p, int) and isinstance(q, int):
                return -1 if p < q else 1
            return -1 if str(p) < str(q) else 1
    return -1 if len(x[3]) < len(y[3]) else 1


@spec("semver", "Compare versions, sort a list of versions, bump a version (major/minor/patch), or check one against a "
      "range like '>=1.2,<2' or '^1.4' or '~2.3'.",
      "dev", [param("operation", enum=["compare", "sort", "bump", "satisfies"]),
              param("versions", "list", "One or more versions.", items="str"),
              param("part", enum=["major", "minor", "patch"], required=False, default="patch"),
              param("range", description="For satisfies.", required=False)],
      keywords="semver, versi, version, bump version, naikkan versi, compare version, upgrade, changelog")
def semver(operation: str, versions: list[str], part: str = "patch", range: str | None = None) -> dict[str, Any]:  # noqa: A002
    vs = versions if isinstance(versions, list) else [versions]
    if operation == "compare":
        if len(vs) != 2:
            raise ValueError("compare needs two versions")
        c = _cmp(vs[0], vs[1])
        return {"result": c, "meaning": f"{vs[0]} {'<' if c < 0 else '>' if c > 0 else '=='} {vs[1]}"}
    if operation == "sort":
        import functools

        return {"sorted": sorted(vs, key=functools.cmp_to_key(_cmp))}
    if operation == "bump":
        major, minor, patch, _ = _sv(vs[0])
        new = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[part]
        return {"from": vs[0], "to": new}
    if operation == "satisfies":
        if not range:
            raise ValueError("satisfies needs range")
        return {"results": {v: _satisfies(v, range) for v in vs}}
    raise ValueError("unknown operation")


def _satisfies(version: str, spec_text: str) -> bool:
    for clause in re.split(r"[,\s]+", spec_text.strip()):
        if not clause:
            continue
        if clause.startswith("^"):
            base = _sv(clause[1:])
            upper = f"{base[0] + 1}.0.0" if base[0] else (f"0.{base[1] + 1}.0" if base[1] else f"0.0.{base[2] + 1}")
            if not (_cmp(version, clause[1:]) >= 0 and _cmp(version, upper) < 0):
                return False
            continue
        if clause.startswith("~"):
            base = _sv(clause[1:])
            if not (_cmp(version, clause[1:]) >= 0 and _cmp(version, f"{base[0]}.{base[1] + 1}.0") < 0):
                return False
            continue
        m = re.match(r"(>=|<=|==|!=|>|<|=)?(.+)", clause)
        if m is None:
            raise ValueError(f"cannot read the range clause '{clause}'")
        op, target = (m.group(1) or "=="), m.group(2)
        c = _cmp(version, target)
        ok = {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0, "==": c == 0, "=": c == 0, "!=": c != 0}[op]
        if not ok:
            return False
    return True


@spec("ip_cidr", "IP address and subnet facts: network, broadcast, netmask, host range and count for a CIDR like "
      "192.168.1.0/24; whether an IP is private, public, loopback; whether an IP falls inside a subnet; split a subnet.",
      "dev", [param("value", description="An IP or CIDR."), param("contains", description="An IP to test against the subnet.", required=False),
              param("split_prefix", "int", "Split the network into subnets of this prefix length.", required=False)],
      keywords="ip, cidr, subnet, subnetting, netmask, ipv4, ipv6, ip address, alamat ip, jaringan, network", lead=True)
def ip_cidr(value: str, contains: str | None = None, split_prefix: int | None = None) -> dict[str, Any]:
    v = value.strip()
    if "/" not in v:
        ip = ipaddress.ip_address(v)
        return {"ip": str(ip), "version": ip.version, "private": ip.is_private, "global": ip.is_global,
                "loopback": ip.is_loopback, "multicast": ip.is_multicast, "reserved": ip.is_reserved,
                "reverse_pointer": ip.reverse_pointer}
    net = ipaddress.ip_network(v, strict=False)
    out: dict[str, Any] = {"network": str(net.network_address), "prefix": net.prefixlen, "netmask": str(net.netmask),
                           "hostmask": str(net.hostmask), "addresses": net.num_addresses, "version": net.version,
                           "private": net.is_private}
    if net.version == 4:
        out["broadcast"] = str(net.broadcast_address)
        hosts = net.num_addresses - 2 if net.prefixlen < 31 else net.num_addresses
        out["usable_hosts"] = max(hosts, 0)
        if net.prefixlen < 31:
            out["first_host"], out["last_host"] = str(net.network_address + 1), str(net.broadcast_address - 1)
    if contains:
        out["contains"] = {contains: ipaddress.ip_address(contains.strip()) in net}
    if split_prefix:
        subnets = list(net.subnets(new_prefix=int(split_prefix)))
        out["subnets"] = [str(s) for s in subnets[:256]]
        out["subnet_count"] = len(subnets)
    return out


_PORTS = {20: "FTP data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP", 80: "HTTP", 110: "POP3",
          123: "NTP", 143: "IMAP", 161: "SNMP", 389: "LDAP", 443: "HTTPS", 445: "SMB", 465: "SMTPS", 587: "SMTP submission",
          993: "IMAPS", 995: "POP3S", 1433: "MS SQL", 1521: "Oracle", 1883: "MQTT", 2049: "NFS", 2375: "Docker (unencrypted!)",
          3000: "dev servers (Node, Grafana)", 3306: "MySQL/MariaDB", 3389: "RDP", 5000: "Flask / dev", 5173: "Vite",
          5432: "PostgreSQL", 5672: "RabbitMQ", 5900: "VNC", 6379: "Redis", 6443: "Kubernetes API", 8000: "dev HTTP (Django, uvicorn)",
          8080: "HTTP alt / proxy", 8443: "HTTPS alt", 8888: "Jupyter", 9000: "PHP-FPM / MinIO", 9090: "Prometheus",
          9200: "Elasticsearch", 11211: "Memcached", 11434: "Ollama", 27017: "MongoDB"}


@spec("port_info", "What usually runs on a TCP/UDP port number, or which port a service uses (e.g. 'postgres').",
      "dev", [param("value", description="A port number or a service name.")], keywords="port, nomor port, port number, service port")
def port_info(value: str) -> dict[str, Any]:
    v = str(value).strip().lower()
    if v.isdigit():
        p = int(v)
        kind = "well-known" if p < 1024 else "registered" if p < 49152 else "dynamic/ephemeral"
        return {"port": p, "service": _PORTS.get(p, "unknown"), "range": kind}
    found = {p: s for p, s in _PORTS.items() if v in s.lower()}
    return {"query": value, "ports": found}


# ── Colour ────────────────────────────────────────────────────────────────────
_NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
          "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "gray": "#808080", "grey": "#808080",
          "pink": "#ffc0cb", "navy": "#000080", "teal": "#008080", "merah": "#ff0000", "hijau": "#008000",
          "biru": "#0000ff", "kuning": "#ffff00", "hitam": "#000000", "putih": "#ffffff", "ungu": "#800080",
          "abu": "#808080", "oranye": "#ffa500", "jingga": "#ffa500"}


def parse_color(value: str) -> tuple[int, int, int]:
    v = value.strip().lower()
    v = _NAMED.get(v, v)
    m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+).*\)", v)
    if m:
        return tuple(min(int(x), 255) for x in m.groups())  # type: ignore[return-value]
    m = re.fullmatch(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%.*\)", v)
    if m:
        h, s, l_ = (float(x) for x in m.groups())
        r, g, b = colorsys.hls_to_rgb(h / 360, l_ / 100, s / 100)
        return round(r * 255), round(g * 255), round(b * 255)
    raise ValueError(f"cannot read colour '{value}'; use #rrggbb, rgb(), hsl() or a basic name")


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        x = c / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@spec("color_convert", "Convert a colour between HEX, RGB, HSL and HSV, and give lighter/darker shades and its complement.",
      "dev", [param("color", description="e.g. '#1e90ff', 'rgb(30,144,255)', 'hsl(210,100%,56%)', 'biru'.")],
      keywords="warna, color, colour, hex, rgb, hsl, hsv, kode warna, color code, palet, palette, shade", lead=True)
def color_convert(color: str) -> dict[str, Any]:
    rgb = parse_color(color)
    h, l_, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    hv, sv, vv = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))

    def shade(f: float) -> str:
        r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l_ + f)), s)
        return _hex((round(r * 255), round(g * 255), round(b * 255)))

    comp = colorsys.hls_to_rgb((h + 0.5) % 1, l_, s)
    return {"hex": _hex(rgb), "rgb": f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})",
            "hsl": f"hsl({round(h * 360)}, {round(s * 100)}%, {round(l_ * 100)}%)",
            "hsv": f"hsv({round(hv * 360)}, {round(sv * 100)}%, {round(vv * 100)}%)",
            "shades": {"lighter_20": shade(0.2), "lighter_10": shade(0.1), "darker_10": shade(-0.1), "darker_20": shade(-0.2)},
            "complement": _hex(tuple(round(c * 255) for c in comp)),  # type: ignore[arg-type]
            "best_text_on_it": "#000000" if _luminance(rgb) > 0.179 else "#ffffff"}


@spec("color_contrast", "WCAG 2 contrast ratio between a text colour and a background, with AA/AAA pass or fail for normal "
      "and large text, and the nearest passing text colour if it fails.",
      "dev", [param("foreground"), param("background")],
      keywords="kontras, contrast, wcag, aksesibilitas, accessibility, keterbacaan, readable, a11y", lead=True)
def color_contrast(foreground: str, background: str) -> dict[str, Any]:
    fg, bg = parse_color(foreground), parse_color(background)
    l1, l2 = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    ratio = (l1 + 0.05) / (l2 + 0.05)
    out: dict[str, Any] = {"ratio": round(ratio, 2), "AA_normal": ratio >= 4.5, "AA_large": ratio >= 3,
                           "AAA_normal": ratio >= 7, "AAA_large": ratio >= 4.5}
    if ratio < 4.5:
        h, l_, s = colorsys.rgb_to_hls(*(c / 255 for c in fg))
        darker = _luminance(bg) > 0.179
        for step in range(1, 101):
            nl = max(0.0, l_ - step / 100) if darker else min(1.0, l_ + step / 100)
            r, g, b = colorsys.hls_to_rgb(h, nl, s)
            cand = (round(r * 255), round(g * 255), round(b * 255))
            a, c = sorted((_luminance(cand), _luminance(bg)), reverse=True)
            if (a + 0.05) / (c + 0.05) >= 4.5:
                out["suggested_foreground"] = _hex(cand)
                break
    return out


@spec("css_units", "Convert CSS lengths between px, rem, em, pt, and vw/vh (given base font size and viewport).",
      "dev", [param("value", "num"), param("from_unit", enum=["px", "rem", "em", "pt", "vw", "vh", "pc", "in", "cm", "mm"]),
              param("base_font", "num", "Root font size in px.", required=False, default=16),
              param("viewport_width", "num", required=False, default=1440), param("viewport_height", "num", required=False, default=900)],
      keywords="rem, px, em, css unit, ukuran font, font size, pt to px, px to rem")
def css_units(value: float, from_unit: str, base_font: float = 16, viewport_width: float = 1440,
              viewport_height: float = 900) -> dict[str, Any]:
    to_px = {"px": 1, "rem": base_font, "em": base_font, "pt": 96 / 72, "pc": 16, "in": 96, "cm": 96 / 2.54,
             "mm": 96 / 25.4, "vw": viewport_width / 100, "vh": viewport_height / 100}
    px = float(value) * to_px[from_unit]
    return {unit: round(px / factor, 4) for unit, factor in to_px.items()}


# ── Code checks ───────────────────────────────────────────────────────────────
@spec("syntax_check", "Check code for syntax errors without running it: Python (exact line/column), JSON, YAML, TOML, and "
      "JavaScript/TypeScript via node --check when node is installed. Give text or a workspace file.",
      "dev", [param("language", enum=["python", "json", "yaml", "toml", "javascript", "auto"], required=False, default="auto"),
              param("code", required=False), param("path", required=False)],
      keywords="syntax, sintaks, syntax error, cek kode, check code, kode error, lint, parse error, indentation", lead=True,
      workspace=True)
def syntax_check(language: str = "auto", code: str | None = None, path: str | None = None,
                 workspace_path: str | None = None) -> dict[str, Any]:
    file = _files.source(path, workspace_path) if path else None
    text = code if code is not None else (file.read_text(encoding="utf-8", errors="replace") if file else None)
    if text is None:
        raise ValueError("give code or path")
    lang = language
    if lang == "auto":
        ext = file.suffix.lower() if file else ""
        lang = {".py": "python", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".js": "javascript",
                ".mjs": "javascript", ".cjs": "javascript"}.get(ext, "python" if re.search(r"^\s*(def|import|class) ", text, re.M) else "json")
    try:
        if lang == "python":
            tree = ast.parse(text)
            return {"valid": True, "language": lang, "functions": sum(isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) for n in ast.walk(tree)),
                    "classes": sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree))}
        if lang == "json":
            json.loads(text)
        elif lang == "yaml":
            import yaml

            list(yaml.safe_load_all(text))
        elif lang == "toml":
            tomllib.loads(text)
        elif lang == "javascript":
            return _node_check(text)
        return {"valid": True, "language": lang}
    except SyntaxError as exc:
        line = (exc.text or "").rstrip()
        return {"ok": True, "valid": False, "language": lang, "error": exc.msg, "line": exc.lineno, "column": exc.offset,
                "code": line, "pointer": " " * max((exc.offset or 1) - 1, 0) + "^"}
    except Exception as exc:  # noqa: BLE001 - json/yaml/toml errors all describe the position
        return {"ok": True, "valid": False, "language": lang, "error": str(exc)}


def _node_check(text: str) -> dict[str, Any]:
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        return {"ok": False, "error": "node is not installed, so JavaScript cannot be checked here"}
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        name = handle.name
    try:
        run = subprocess.run([node, "--check", name], capture_output=True, text=True, timeout=15)
    finally:
        Path(name).unlink(missing_ok=True)
    if run.returncode == 0:
        return {"valid": True, "language": "javascript"}
    return {"ok": True, "valid": False, "language": "javascript", "error": run.stderr.replace(name, "<code>")[-1500:]}


@spec("python_inspect", "List a Python file's imports, functions (with arguments and line numbers), classes and methods, "
      "and flag common mistakes: bare except, mutable default arguments, unused imports, print calls.",
      "dev", [param("code", required=False), param("path", required=False)],
      keywords="python, analisa kode python, inspect python, fungsi apa saja, list functions, unused import", workspace=True)
def python_inspect(code: str | None = None, path: str | None = None, workspace_path: str | None = None) -> dict[str, Any]:
    text = code if code is not None else _files.source(path or "", workspace_path).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text)
    imports, functions, classes, issues = [], [], [], []
    imported_names: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                imported_names[name] = node.lineno
                imports.append(f"{getattr(node, 'module', None) or ''}{'.' if isinstance(node, ast.ImportFrom) else ''}{alias.name}".lstrip("."))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append({"name": node.name, "line": node.lineno, "args": [a.arg for a in node.args.args]})
            for default in node.args.defaults:
                if isinstance(default, ast.List | ast.Dict | ast.Set):
                    issues.append({"line": node.lineno, "issue": f"mutable default argument in {node.name}"})
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno,
                            "methods": [n.name for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]})
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({"line": node.lineno, "issue": "bare except catches everything, including KeyboardInterrupt"})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            issues.append({"line": node.lineno, "issue": "print call (debug output?)"})
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    exported = re.search(r"__all__", text) is not None
    for name, line in imported_names.items():
        if name not in used and name != "annotations" and not exported:
            issues.append({"line": line, "issue": f"import '{name}' looks unused"})
    return {"imports": imports, "functions": functions, "classes": classes, "issues": issues[:100],
            "lines": text.count("\n") + 1}


@spec("dependencies_list", "List a project's declared dependencies from requirements*.txt, pyproject.toml, package.json, "
      "go.mod, Cargo.toml, or composer.json in the workspace, with version constraints.",
      "dev", [param("path", description="Folder to look in (default: workspace root).", required=False, default=".")],
      keywords="dependensi, dependency, dependencies, requirements, package.json, pyproject, library apa, paket, packages",
      workspace=True)
def dependencies_list(path: str = ".", workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    folder = (root / path).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir():
        raise ValueError("path must be a folder inside the workspace")
    found: dict[str, Any] = {}
    for req in sorted(folder.glob("requirements*.txt")):
        found[req.name] = [line.split("#")[0].strip() for line in req.read_text(encoding="utf-8").splitlines()
                           if line.split("#")[0].strip() and not line.startswith("-")]
    if (folder / "pyproject.toml").exists():
        data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
        project = data.get("project", {})
        found["pyproject.toml"] = {"dependencies": project.get("dependencies", []),
                                   "optional": project.get("optional-dependencies", {}),
                                   "poetry": data.get("tool", {}).get("poetry", {}).get("dependencies", {})}
    if (folder / "package.json").exists():
        pkg = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        found["package.json"] = {k: pkg.get(k, {}) for k in ("dependencies", "devDependencies", "peerDependencies") if pkg.get(k)}
        found["package.json"]["scripts"] = pkg.get("scripts", {})
    if (folder / "go.mod").exists():
        found["go.mod"] = re.findall(r"^\s*([\w./-]+)\s+(v[\w.+-]+)", (folder / "go.mod").read_text(encoding="utf-8"), re.M)
    if (folder / "Cargo.toml").exists():
        found["Cargo.toml"] = tomllib.loads((folder / "Cargo.toml").read_text(encoding="utf-8")).get("dependencies", {})
    if (folder / "composer.json").exists():
        found["composer.json"] = json.loads((folder / "composer.json").read_text(encoding="utf-8")).get("require", {})
    return {"files": found, "none_found": not found}


@spec("html_to_text", "Turn HTML (pasted, or a workspace .html file) into clean Markdown or plain text, and list its links "
      "and headings.",
      "dev", [param("html", required=False), param("path", required=False),
              param("output", enum=["markdown", "text"], required=False, default="markdown")],
      keywords="html ke teks, html to text, html to markdown, strip html, ambil teks dari html, scrape html", workspace=True)
def html_to_text(html: str | None = None, path: str | None = None, output: str = "markdown",
                 workspace_path: str | None = None) -> dict[str, Any]:
    from bs4 import BeautifulSoup

    markup = html if html is not None else _files.source(path or "", workspace_path).read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    links = [{"text": a.get_text(" ", strip=True)[:80], "href": a["href"]} for a in soup.find_all("a", href=True)][:200]
    headings = [f"{h.name}: {h.get_text(' ', strip=True)[:100]}" for h in soup.find_all(re.compile(r"^h[1-6]$"))][:100]
    if output == "markdown":
        from markdownify import markdownify

        body = re.sub(r"\n{3,}", "\n\n", markdownify(str(soup), heading_style="ATX")).strip()
    else:
        body = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return {"result": body[:200_000], "title": soup.title.get_text(strip=True) if soup.title else "", "links": links,
            "headings": headings}


@spec("markdown_render", "Render Markdown to HTML (CommonMark plus tables), e.g. to preview or to save as a web page.",
      "dev", [param("markdown"), param("full_page", "bool", "Wrap in a minimal HTML page.", required=False, default=False)],
      keywords="markdown ke html, markdown to html, render markdown, preview markdown")
def markdown_render(markdown: str, full_page: bool = False) -> dict[str, Any]:
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough").render(markdown)
    if full_page:
        body = ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
                "content=\"width=device-width,initial-scale=1\"></head><body>" + body + "</body></html>")
    return {"html": body}


@spec("gitignore_template", "A sensible .gitignore for a stack (python, node, java, go, rust, flutter, laravel, android, "
      "unity, macos, windows, env), combined when several are given.",
      "dev", [param("stacks", "list", items="str")], keywords="gitignore, .gitignore, abaikan file git, ignore files")
def gitignore_template(stacks: list[str]) -> dict[str, Any]:
    t = {
        "python": "__pycache__/\n*.py[cod]\n.venv/\nvenv/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\ndist/\nbuild/\n*.egg-info/\n.coverage\n",
        "node": "node_modules/\nnpm-debug.log*\nyarn-error.log\n.pnpm-store/\ndist/\n.next/\n.nuxt/\ncoverage/\n.turbo/\n",
        "java": "*.class\ntarget/\nbuild/\n.gradle/\n*.jar\n.idea/\n*.iml\n",
        "go": "bin/\n*.exe\n*.test\n*.out\nvendor/\n",
        "rust": "target/\nCargo.lock\n",
        "flutter": ".dart_tool/\n.packages\nbuild/\n.flutter-plugins*\n",
        "laravel": "/vendor/\n/node_modules/\n/public/storage\n/storage/*.key\n.env\n.phpunit.result.cache\n",
        "android": "*.apk\n*.aab\n.gradle/\nlocal.properties\n/build/\ncaptures/\n",
        "unity": "[Ll]ibrary/\n[Tt]emp/\n[Oo]bj/\n[Bb]uild/\n[Ll]ogs/\n*.csproj\n*.sln\n",
        "macos": ".DS_Store\n.AppleDouble\n._*\n",
        "windows": "Thumbs.db\ndesktop.ini\n$RECYCLE.BIN/\n",
        "env": ".env\n.env.*\n!.env.example\n*.pem\n*.key\n",
    }
    unknown = [s for s in stacks if s.lower() not in t]
    parts = [f"# {s}\n{t[s.lower()]}" for s in stacks if s.lower() in t]
    if "env" not in [s.lower() for s in stacks]:
        parts.append(f"# secrets\n{t['env']}")
    return {"gitignore": "\n".join(parts), "unknown": unknown, "available": sorted(t)}


@spec("code_stats", "Count files and lines of code per language in the workspace (or a folder), skipping dependencies, "
      "build output and hidden folders. Shows the biggest files.",
      "dev", [param("path", required=False, default=".")],
      keywords="jumlah baris kode, lines of code, loc, code stats, statistik kode, bahasa pemrograman apa, berapa file", workspace=True)
def code_stats(path: str = ".", workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    folder = (root / path).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir():
        raise ValueError("path must be a folder inside the workspace")
    langs = {".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
             ".jsx": "JavaScript", ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".go": "Go", ".rs": "Rust",
             ".java": "Java", ".kt": "Kotlin", ".php": "PHP", ".rb": "Ruby", ".c": "C", ".h": "C", ".cpp": "C++",
             ".cs": "C#", ".swift": "Swift", ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte", ".sql": "SQL",
             ".sh": "Shell", ".mq4": "MQL4", ".mq5": "MQL5", ".md": "Markdown", ".yaml": "YAML", ".yml": "YAML", ".json": "JSON"}
    skip = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", ".agent_memory",
            ".vault", ".skills", "vendor"}
    totals: dict[str, dict[str, int]] = {}
    biggest: list[tuple[int, str]] = []
    scanned = 0
    for file in folder.rglob("*"):
        rel = file.relative_to(root)
        if any(p in skip or p.startswith(".") for p in rel.parts[:-1]) or not file.is_file():
            continue
        lang = langs.get(file.suffix.lower())
        if not lang or file.stat().st_size > 5_000_000:
            continue
        scanned += 1
        if scanned > 20_000:
            break
        lines = file.read_bytes().count(b"\n") + 1
        entry = totals.setdefault(lang, {"files": 0, "lines": 0})
        entry["files"] += 1
        entry["lines"] += lines
        biggest.append((lines, str(rel)))
    biggest.sort(reverse=True)
    return {"languages": dict(sorted(totals.items(), key=lambda kv: -kv[1]["lines"])),
            "total_lines": sum(v["lines"] for v in totals.values()), "files": scanned,
            "biggest_files": [{"path": p, "lines": n} for n, p in biggest[:10]]}
