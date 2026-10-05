"""Offline privacy gate for tracked source and release archives.

Only explicitly selected/tracked public files are opened. Optional exact private
matches come from --denylist; reports never include matched values or excerpts.
This is a leak detector, not proof that arbitrary prose contains no personal data.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
_HOME = re.compile(r"(?:[A-Za-z]:[/\\]+Users|/Users|/home)[/\\]+([^/\\\s\"']+)", re.I)
_ACCOUNT = re.compile(r"\b(?:DU|U|F)\d{6,12}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?:phone|mobile|telephone|手机号|联系电话)[\"']?\s*[=:：]\s*[\"']?(\+?\d[\d ()-]{7,}\d)", re.I)
_ADDRESS = re.compile(r"(?:home_address|residential_address|住址|家庭地址)[\"']?\s*[=:：]\s*[\"']?([^\n\"']{6,})", re.I)
_INTERVIEW_CHOICE = re.compile(r"\bQ[0-9]{1,3}\s*=\s*[A-Z]\b", re.I)
_CREDENTIALS = {
    "private_key_header": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "github_access_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google_api_key": re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
}
_HOME_PLACEHOLDERS = frozenset(("example", "example-user", "fixture", "test-user", "username", "user",
                              "<user>", "<username>", "${username}", "%username%"))
# These exact pre-existing broker test values are synthetic, and only allowed in
# _test_*.py. There is no blanket exemption for tests or documentation.
_ACCOUNT_FIXTURES = frozenset(("U" + "1234567", "U" + "7654321"))
_PRIVATE_PREFIXES = ("data/state/", "data/runs/", "data/audit/", "data/decisions/",
                     "evals/results/", "evals/cache/")
_PRIVATE_NAMES = frozenset((".env", ".credentials.json", "settings.local.json", "positions.md",
                            "trading_memory.md", "watchlist.local.md", "privacy-denylist.json"))
_PRIVATE_EXACT = frozenset(("config/user.toml", "docs/strategy.md", "docs/strategy-checklist.md"))
_WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def _private_path(path: str) -> bool:
    low = path.lower()
    candidates = [low, *(low[index + 1:] for index, value in enumerate(low) if value == "/")]
    return (any(value.startswith(_PRIVATE_PREFIXES) or value in _PRIVATE_EXACT for value in candidates)
            or PurePosixPath(low).name in _PRIVATE_NAMES
            or (PurePosixPath(low).name.startswith(".env.") and PurePosixPath(low).name != ".env.example")
            or low.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".env")))


def _normal_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    # Reject aliases rather than silently normalizing them: Windows extraction
    # trims trailing dots/spaces and treats device names/ADS specially. Inspect
    # raw components before PurePosixPath can collapse a single-dot component.
    raw_parts = normalized.split("/")
    if any(component.endswith((".", " ")) or _WINDOWS_DEVICE.match(component)
           or re.search(r'[<>"|?*\x00-\x1f]', component) for component in raw_parts):
        return None
    parts = PurePosixPath(normalized).parts
    if not parts or normalized.startswith("/") or ":" in normalized or ".." in parts:
        return None
    return "/".join(parts)


def _safe_path(path: str, literals: tuple[str, ...]) -> str:
    # casefold may expand characters (e.g. one character into two), so a
    # regex span replacement with re.I is not equivalent to the match gate.
    # Hide the entire location whenever the same gate matches a private value.
    if any(literal.casefold() in path.casefold() for literal in literals):
        return "[private-path]"
    safe = _HOME.sub("[user-home]", path)
    safe = _ACCOUNT.sub("[account-id]", safe)
    safe = _EMAIL.sub("[email]", safe)
    safe = _PHONE.sub("[phone]", safe)
    safe = _ADDRESS.sub("[residential-address]", safe)
    safe = _INTERVIEW_CHOICE.sub("[interview-answer]", safe)
    for pattern in _CREDENTIALS.values():
        safe = pattern.sub("[credential]", safe)
    for literal in literals:
        safe = re.sub(re.escape(literal), "[private-literal]", safe, flags=re.I)
    return safe


def _load_denylist(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        values = data["literals"]
        if (data.get("schema_version") != 1 or not isinstance(values, list) or len(values) > 4096
                or any(not isinstance(value, str) or not 4 <= len(value) <= 512 or not value.strip()
                       for value in values)):
            raise ValueError
        return tuple(dict.fromkeys(values))
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("privacy denylist is missing or invalid") from None


def _reserved_email(value: str) -> bool:
    domain = value.rsplit("@", 1)[1].lower()
    return (domain in ("example.com", "example.org", "example.net")
            or domain.endswith((".example", ".invalid", ".test"))
            or any(domain.endswith("." + reserved) for reserved in ("example.com", "example.org", "example.net")))


def _text_findings(path: str, text: str, literals: tuple[str, ...]) -> list[dict]:
    findings = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for literal in literals:
        if "\n" in literal or "\r" in literal:
            needle = literal.replace("\r\n", "\n").replace("\r", "\n")
            for match in re.finditer(re.escape(needle), normalized, re.I):
                findings.append(dict(path=_safe_path(path, literals),
                                     line=normalized.count("\n", 0, match.start()) + 1,
                                     category="private_denylist_match"))
    test = PurePosixPath(path).name.startswith("_test_") and path.endswith(".py")
    for number, line in enumerate(text.splitlines(), 1):
        variants = [line]
        # JSON escaping is not a privacy boundary: scan string values too while
        # preserving the source line and without emitting the decoded content.
        for quoted in re.findall(r'"(?:[^"\\]|\\.)*"', line):
            try:
                variants.append(json.loads(quoted))
            except ValueError:
                pass
        categories = set()
        if any(match.group(1).lower() not in _HOME_PLACEHOLDERS
               for variant in variants for match in _HOME.finditer(variant)):
            categories.add("user_home_path")
        if any(not (test and match.group() in _ACCOUNT_FIXTURES)
               for variant in variants for match in _ACCOUNT.finditer(variant)):
            categories.add("broker_account_identifier")
        if any(not _reserved_email(match.group()) for variant in variants for match in _EMAIL.finditer(variant)):
            categories.add("contact_email")
        if any(_PHONE.search(variant) for variant in variants):
            categories.add("contact_phone")
        if any(_ADDRESS.search(variant) for variant in variants):
            categories.add("residential_address")
        if any(_INTERVIEW_CHOICE.search(variant) for variant in variants):
            categories.add("personal_interview_choice")
        categories.update(category for category, pattern in _CREDENTIALS.items()
                          if any(pattern.search(variant) for variant in variants))
        if any(literal.casefold() in variant.casefold() for literal in literals for variant in variants):
            categories.add("private_denylist_match")
        findings.extend(dict(path=_safe_path(path, literals), line=number, category=category)
                        for category in sorted(categories))
    return findings


def _report() -> dict:
    return dict(status="pass", findings=[], text_files_scanned=0,
                scope="selected_public_files_only", private_denylist_used=False)


def _add(report: dict, path: str, category: str, literals: tuple[str, ...] = ()) -> None:
    report["findings"].append(dict(path=_safe_path(path, literals), line=None, category=category))


def sanitize_message(message: str, *, denylist_path: Path | None = None) -> str:
    """Redact location/legacy diagnostic strings using the same exact matches."""
    try:
        return _safe_path(message, _load_denylist(denylist_path))
    except ValueError:
        return "[invalid private denylist]"


def _location_findings(report: dict, path: str, literals: tuple[str, ...]) -> None:
    for finding in _text_findings(path, path, literals):
        finding["line"] = None
        report["findings"].append(finding)


def _scan_bytes(report: dict, path: str, content: bytes, literals: tuple[str, ...]) -> None:
    try:
        encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = content.decode(encoding)
        if "\x00" in text:
            raise UnicodeError
    except UnicodeError:
        _add(report, path, "unscannable_content", literals)
        return
    report["text_files_scanned"] += 1
    report["findings"].extend(_text_findings(path, text, literals))


def _finish(report: dict) -> dict:
    report["findings"] = [dict(path=path, line=line, category=category)
                          for path, line, category in dict.fromkeys(
                              (finding["path"], finding["line"], finding["category"])
                              for finding in report["findings"])]
    report["status"] = "fail" if report["findings"] else "pass"
    return report


def scan_tree(root: Path, *, paths=None, denylist_path: Path | None = None) -> dict:
    """Scan git-tracked worktree bytes, or an explicit relative public file list."""
    report = _report()
    try:
        literals = _load_denylist(denylist_path)
    except ValueError:
        _add(report, "[denylist]", "invalid_private_denylist")
        return _finish(report)
    report["private_denylist_used"] = denylist_path is not None
    root = Path(root).resolve()
    if paths is None:
        try:
            result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                                    check=True, timeout=30)
            paths = result.stdout.decode("utf-8").split("\0")
        except (OSError, UnicodeError, subprocess.SubprocessError):
            _add(report, "[tracked-tree]", "tracked_file_inventory_unavailable")
            return _finish(report)
    total = 0
    for name in dict.fromkeys(paths):
        if not name:
            continue
        path = _normal_path(name)
        if path is None:
            _add(report, "[unsafe-path]", "unsafe_path")
            continue
        _location_findings(report, path, literals)
        if _private_path(path):
            _add(report, path, "private_file", literals)
            continue  # Never open private state, even if someone tracked it.
        source = root / path
        try:
            if source.is_symlink() or not source.resolve().is_relative_to(root):
                _add(report, path, "symlink_or_external_path", literals)
                continue
            size = source.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                _add(report, path, "scan_size_limit", literals)
                continue
            _scan_bytes(report, path, source.read_bytes(), literals)
        except OSError:
            _add(report, path, "unreadable_public_file", literals)
    return _finish(report)


def scan_archive(archive: Path, *, denylist_path: Path | None = None) -> dict:
    """Scan without extraction; private members are rejected without reading."""
    report = _report()
    try:
        literals = _load_denylist(denylist_path)
    except ValueError:
        _add(report, "[denylist]", "invalid_private_denylist")
        return _finish(report)
    report["private_denylist_used"] = denylist_path is not None
    try:
        with zipfile.ZipFile(archive) as bundle:
            total = 0
            seen = set()
            if bundle.comment:
                _add(report, "[archive-comment]", "archive_metadata_comment")
            for member in bundle.infolist():
                path = _normal_path(member.filename)
                if path is None:
                    _add(report, "[unsafe-path]", "unsafe_archive_path")
                    continue
                _location_findings(report, path, literals)
                if member.comment:
                    _add(report, path, "archive_metadata_comment", literals)
                # Releases have one trading-copilot[-version] root; support bare
                # files as well so dropping the root cannot bypass path guards.
                parts = path.split("/", 1)
                relative = parts[1] if len(parts) == 2 and parts[0].lower().startswith("trading-copilot") else path
                if relative.casefold() in seen:
                    _add(report, path, "duplicate_archive_path", literals)
                    continue
                seen.add(relative.casefold())
                if _private_path(relative + "/" if member.is_dir() else relative):
                    _add(report, path, "private_file", literals)
                    continue
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    _add(report, path, "symlink_or_external_path", literals)
                    continue
                if member.is_dir():
                    continue
                total += member.file_size
                if member.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    _add(report, path, "scan_size_limit", literals)
                    continue
                _scan_bytes(report, relative, bundle.read(member), literals)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
        _add(report, "[archive]", "unreadable_archive")
    return _finish(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--denylist", type=Path, help="explicit local private exact-match JSON; never printed")
    args = parser.parse_args()
    report = (scan_archive(args.archive, denylist_path=args.denylist) if args.archive
              else scan_tree(args.root, denylist_path=args.denylist))
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
