"""Judge a shell command by what it does, not by how it is spelled.

The old guards matched regexes, so ``rm -r -f /`` (two flags) or ``rm -rf ~`` slipped through. Here the command is
split into its parts with ``shlex`` (``;``, ``&&``, ``||``, ``|``, ``&``, new lines), wrappers such as ``sudo``,
``env``, ``nohup`` and ``timeout`` are peeled off, ``~`` and ``$HOME`` are expanded, and each part is judged:

* **Recursive deletes** (``rm -r``, ``find -delete``, ``chmod/chown -R``) are fine inside the folder being worked in,
  inside the chat workspace and in ``/tmp``; aimed at the home folder, a parent of it, or a system folder they are
  refused.
* **Running fetched or decoded text** (``curl … | sh``, ``base64 -d | bash``, ``bash <(curl …)``,
  ``sh -c "$(curl …)"``) is refused unless it comes straight from a known installer (nvm, composer, bun, rustup ...).
  The agent can always download the script to a file, read it, and run the file.
* **Key material** (``~/.ssh`` private keys, ``authorized_keys``, ``~/.aws``, ``~/.gnupg``, browser profiles,
  agent login files, SquidWings' own credential stores) is never read, copied or sent by a command.
* **Inline code** (``python -c``, ``node -e`` ...) that deletes trees while naming the home folder or ``/`` is refused.
* **Root shells** (``sudo -i``, ``sudo su``, ``sudo bash``) are refused; ``sudo <one command>`` is judged as that
  command, so framework work (apt, nginx, MySQL) still runs.

The result is a ``Risk``. It never raises, and an unparseable command is judged on its raw text.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Installers whose ``curl … | sh`` is ordinary setup work (the owner's rule: nvm, composer, bun are normal).
TRUSTED_INSTALL_HOSTS = frozenset({
    "raw.githubusercontent.com/nvm-sh", "getcomposer.org", "bun.sh", "sh.rustup.rs", "astral.sh",
    "install.python-poetry.org", "deb.nodesource.com", "rpm.nodesource.com", "get.docker.com", "get.pnpm.io",
    "deno.land", "fnm.vercel.app", "cli.github.com", "pkgs.tailscale.com", "ollama.com", "claude.ai/install.sh",
    "brew.sh", "raw.githubusercontent.com/Homebrew",
})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "perl", "ruby", "node", "php"})
INLINE_FLAGS = {"python": "-c", "python3": "-c", "node": "-e", "perl": "-e", "ruby": "-e", "php": "-r",
                "bash": "-c", "sh": "-c", "zsh": "-c"}
WRAPPERS = frozenset({"sudo", "env", "nohup", "nice", "ionice", "time", "command", "exec", "stdbuf", "xargs", "doas"})
SYSTEM_DIRS = ("/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/opt", "/proc", "/root",
               "/sbin", "/srv", "/sys", "/usr", "/var", "/snap", "/mnt", "/media")
OPERATORS = frozenset({";", "&&", "||", "|", "&", "|&", "\n", "(", ")"})
_SAFE_SSH = frozenset({"known_hosts", "known_hosts.old", "config"})
_DELETE_IN_CODE = re.compile(r"rmtree|remove_tree|rimraf|unlink|os\.remove|fs\.rm|rm\s+-[a-z]*r", re.IGNORECASE)


@dataclass(frozen=True)
class Risk:
    blocked: bool = False
    reason: str = ""
    root: bool = False          # the command asks for sudo (judged by the rest of it)
    fetches: bool = False       # it runs something fetched from the network (an installer)

    @property
    def ok(self) -> bool:
        return not self.blocked


def _home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def secret_paths(home: Path | None = None) -> list[Path]:
    """Files and folders a command must never read or send."""
    h = home or _home()
    from brain.config import paths

    found = [h / ".gnupg", h / ".aws", h / ".netrc", h / ".git-credentials", h / ".docker" / "config.json",
             h / ".kube", h / ".config" / "gh" / "hosts.yml", h / ".codex" / "auth.json",
             h / ".claude" / ".credentials.json", h / ".mozilla", h / ".config" / "google-chrome",
             h / ".config" / "chromium", h / ".password-store", h / ".pgpass", Path("/etc/shadow"),
             *paths.secret_stores()]
    # The stores moved out of the repo are reached through links: guard the real place as well.
    return list(dict.fromkeys([*found, *(p.resolve() for p in found)]))


def _expand(token: str, home: Path) -> str:
    # A function, not a string: a Windows home (C:\\Users\\...) would be read as regex escapes.
    token = re.sub(r"\$\{HOME\}|\$HOME", lambda _match: str(home), token)
    if token == "~" or token.startswith("~/"):
        token = str(home) + token[1:]
    return token


def _split(command: str) -> list[tuple[str, list[str]]]:
    """[(operator before the part, its words)]: the parts of a command line."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    parts: list[tuple[str, list[str]]] = []
    current: list[str] = []
    op = ""
    for token in lexer:
        if token in OPERATORS or set(token) <= set(";&|()\n"):
            if current:
                parts.append((op, current))
            current, op = [], token
            continue
        if "\n" in token:
            for i, piece in enumerate(token.split("\n")):
                if i and current:
                    parts.append((op, current))
                    current, op = [], "\n"
                if piece:
                    current.append(piece)
            continue
        current.append(token)
    if current:
        parts.append((op, current))
    return parts


def _peel(words: list[str]) -> tuple[list[str], bool, list[str]]:
    """The real command inside wrappers; whether sudo was one of them; sudo's own flags."""
    root = False
    sudo_flags: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            i += 1
            continue
        name = os.path.basename(word)
        if name in {"sudo", "doas"}:
            root = True
            i += 1
            while i < len(words) and words[i].startswith("-"):
                flag = words[i]
                sudo_flags.append(flag)
                i += 1
                if flag in {"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t", "-D"} and i < len(words):
                    i += 1
            continue
        if name == "timeout":
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", words[i])):
                i += 1
            continue
        if name in WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
            continue
        break
    return words[i:], root, sudo_flags


def _under(path: Path, folder: Path) -> bool:
    try:
        return path == folder or path.is_relative_to(folder)
    except ValueError:
        return False


def _too_wide(folder: Path, home: Path) -> bool:
    """A folder too broad to count as "the folder being worked in": the home folder, a parent of it, a system one."""
    return folder == home or _under(home, folder) or str(folder) in SYSTEM_DIRS


def _protected(path: Path, home: Path, allowed: list[Path]) -> str:
    """Why a recursive change to ``path`` is refused, or "" when it is fine."""
    if any(_under(path, folder) and path != folder for folder in allowed) or any(
            _under(path, folder) for folder in allowed if folder == Path("/tmp")):
        return ""
    if path == home:
        return f"{path} is your home folder"
    if _under(home, path):
        return f"{path} contains your home folder"
    if str(path) in SYSTEM_DIRS or any(_under(path, Path(d)) for d in SYSTEM_DIRS[1:] if d not in {"/home", "/tmp"}):
        return f"{path} is a system folder"
    if not any(_under(path, folder) for folder in allowed):
        return f"{path} is outside the folder being worked in"
    return ""


def _resolve(raw: str, cwd: Path, home: Path) -> Path:
    raw = _expand(raw, home)
    if any(ch in raw for ch in "*?[") and raw.rstrip("/*").strip() in {"", str(home)}:
        raw = raw.rstrip("/*") or "/"
    candidate = Path(raw) if os.path.isabs(raw) else cwd / raw
    return Path(os.path.normpath(candidate))


def _host_of(text: str) -> str:
    for match in re.finditer(r"https?://\S+", text):
        url = match.group(0).strip("'\")")
        parts = urlsplit(url)
        first = parts.path.strip("/").split("/", 1)[0]
        return f"{parts.hostname or ''}/{first}".rstrip("/")
    return ""


def _trusted_fetch(text: str) -> bool:
    host = _host_of(text)
    return bool(host) and any(host == t or host.startswith(t.rstrip("/") + "/") or host.split("/")[0] == t
                              for t in TRUSTED_INSTALL_HOSTS)


def _touches_secret(text: str, home: Path) -> str:
    expanded = _expand(text, home)
    for token in re.findall(r"[^\s'\"<>|;&()=]+", expanded):
        token = _expand(token, home)
        # Any absolute path of this system: on Windows "~/.ssh" becomes C:\\Users\\x/.ssh, which has no leading "/".
        if not (token.startswith("/") or os.path.isabs(token)):
            continue
        path = Path(os.path.normpath(token))
        ssh = home / ".ssh"
        if _under(path, ssh) and path != ssh:
            name = path.relative_to(ssh).parts[0]
            if name not in _SAFE_SSH and not name.endswith(".pub"):
                return f"{path} is SSH key material"
        for secret in secret_paths(home):
            if _under(path, secret):
                return f"{path} holds credentials"
    return ""


def assess(command: str, cwd: str | Path | None = None, workspace: str | Path | None = None) -> Risk:
    """How dangerous ``command`` is when run from ``cwd`` in the chat's ``workspace``."""
    text = (command or "").strip()
    if not text:
        return Risk()
    home = _home()
    here = Path(os.path.normpath(_expand(str(cwd), home))) if cwd and str(cwd) not in {".", ""} else None
    base = Path(workspace).resolve() if workspace else None
    work = here or base or Path.cwd()
    allowed = [p for p in (here, base) if p is not None and not _too_wide(p, home)] + [Path("/tmp")]

    secret = _touches_secret(text, home)
    if secret:
        return Risk(True, f"reads or sends secrets: {secret}")

    # Process substitution and command substitution are split apart by the parser, so judge them on the raw text.
    for match in re.finditer(r"(<\(|\$\()\s*(?:sudo\s+)?(curl|wget)\b[^)]*\)", text):
        consumer = text[: match.start()].rstrip().rsplit(None, 2)[-2:]
        runs = any(os.path.basename(w).strip('"\'') in SHELLS | {"eval", "source", "."} for w in consumer)
        if (runs or match.group(1) == "$(" and re.search(r"\b(sh|bash|zsh|eval)\b[^|;&]*-c\s*[\"']?\s*$",
                                                          text[: match.start()])) and not _trusted_fetch(match.group(0)):
            return Risk(True, "running text fetched from an unknown site; download it, read it, then run the file")

    try:
        parts = _split(text)
    except ValueError:
        parts = [("", text.split())]

    root = fetches = False
    previous: list[str] = []
    for op, words in parts:
        real, sudo, sudo_flags = _peel(words)
        root = root or sudo
        if sudo and ({"-i", "-s", "--login", "--shell"} & set(sudo_flags) or not real or
                     os.path.basename(real[0]) in {"su", "bash", "sh", "zsh", "fish", "dash"} and
                     not any(a == "-c" for a in real[1:]) and len(real) <= 2):
            return Risk(True, "an interactive root shell is not allowed; run the one command that needs sudo", root=True)
        if not real:
            previous = real
            continue
        name = os.path.basename(real[0])
        args = real[1:]
        if name in {"cd", "pushd"}:
            target = next((a for a in args if not a.startswith("-")), str(home))
            work = _resolve(target, work, home)
            previous = real
            continue

        if name in {"rm", "rmdir", "shred"}:
            flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
            recursive = "r" in flags.lower() or "--recursive" in args or name == "shred"
            if recursive:
                for target in (a for a in args if not a.startswith("-")):
                    why = _protected(_resolve(target, work, home), home, allowed)
                    if why:
                        return Risk(True, f"recursive delete refused: {why}", root=root)

        if name in {"chmod", "chown", "chgrp"} and any(a in {"-R", "--recursive"} or
                                                        (a.startswith("-") and "R" in a) for a in args):
            for target in (a for a in args[1:] if not a.startswith("-")):
                why = _protected(_resolve(target, work, home), home, allowed)
                if why:
                    return Risk(True, f"recursive permission change refused: {why}", root=root)

        if name == "find" and ("-delete" in args or ("-exec" in args and "rm" in args)):
            start = next((a for a in args if not a.startswith("-")), ".")
            why = _protected(_resolve(start, work, home), home, allowed)
            if why:
                return Risk(True, f"find with delete refused: {why}", root=root)

        if name in {"mkfs", "wipefs", "fdisk", "parted", "sfdisk"} or name.startswith("mkfs."):
            return Risk(True, "formatting or repartitioning a disk is left to the user", root=root)
        if name == "dd" and any(re.match(r"of=/dev/(sd|nvme|hd|vd|mmcblk|disk)", a) for a in args):
            return Risk(True, "writing straight onto a disk is left to the user", root=root)
        if name in {"shutdown", "poweroff", "halt", "reboot"}:
            return Risk(True, "shutting down or rebooting is left to the user", root=root)

        flag = INLINE_FLAGS.get(name)
        if flag and flag in args:
            code = " ".join(args[args.index(flag) + 1:])
            if name in {"bash", "sh", "zsh"}:
                inner = assess(code, cwd=work, workspace=base)
                if inner.blocked:
                    return inner
                if re.search(r"\$\((?:[^)]*)(curl|wget)", code) and not _trusted_fetch(code):
                    return Risk(True, "running text fetched from an unknown site; download it, read it, then run the file")
            elif _DELETE_IN_CODE.search(code) and re.search(r"expanduser|Path\.home|os\.environ\[.HOME|"
                                                          r"""['"]/['"]|['"]~|""" + re.escape(str(home)), code):
                return Risk(True, "inline code deletes files in the home folder or the root")

        if op in {"|", "|&"} and name in SHELLS and not any(not a.startswith("-") for a in args):
            producer = " ".join(previous)
            fetch = os.path.basename(previous[0]) in {"curl", "wget"} if previous else False
            if fetch and _trusted_fetch(producer):
                fetches = True
            else:
                source = "fetched from an unknown site" if fetch else "produced by another command"
                return Risk(True, f"piping text {source} into {name}; save it to a file, read it, then run the file")

        if name in {"eval", "source", "."} or (name in SHELLS and any(a.startswith("<(") for a in args)):
            joined = " ".join(args)
            if re.search(r"curl|wget", joined) and not _trusted_fetch(joined):
                return Risk(True, "running text fetched from an unknown site; download it, read it, then run the file")

        previous = real
    return Risk(False, "", root=root, fetches=fetches)
