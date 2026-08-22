"""Refuse to let a credential enter a commit.

Written as a property of the repository rather than as advice: "no tracked file
in this repo may contain a credential". It runs from .githooks/pre-commit against
the STAGED content, so it judges what is actually about to be committed, not what
happens to be sitting in the working tree.

    python tools/check_secrets.py            # scan the staged diff (hook mode)
    python tools/check_secrets.py --tracked  # scan every tracked file (audit)

Exit 0 = clean, 1 = something looks like a secret, 2 = the checker itself failed.
A checker that cannot run must not report "clean" — that is why 2 exists and is
treated as a failure by the hook.
"""

from __future__ import annotations

import re
import subprocess
import sys

# Each rule: (name, compiled pattern). Patterns match an ASSIGNMENT of a value,
# not the mere mention of a word, so prose like "put your consumer key in .env"
# does not trip them.
RULES = [
    (
        "EPO OPS consumer key/secret assigned a literal",
        re.compile(
            r"""(?ix)
            \b (?: ops[_-]?(?:consumer[_-]?)?(?:key|secret)
                 | consumer[_-]?(?:key|secret)
                 | client[_-]?secret )
            \b \s* [:=] \s* ["']? [A-Za-z0-9_\-]{16,} ["']?
            """
        ),
    ),
    (
        "Authorization: Basic/Bearer header with a literal token",
        re.compile(r"""(?i)\bauthorization\b\s*[:=]\s*["']?\s*(?:basic|bearer)\s+[A-Za-z0-9+/=._\-]{20,}"""),
    ),
    (
        "generic API key assigned a literal",
        re.compile(r"""(?ix)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key)\b
                       \s*[:=]\s*["']?[A-Za-z0-9_\-+/=]{20,}["']?"""),
    ),
    ("PEM private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

# Lines that are obviously teaching material, not a credential.
ALLOW = re.compile(
    r"""(?ix)
      (?: <your- | your_ | xxxx | \.\.\. | example | placeholder | changeme
        | REPLACE_ME | \bENV\b | os\.environ | getenv | \.env\.example )
    """
)

# Files whose whole purpose is to describe the shape of a credential.
SKIP_PATHS = (".env.example", "tools/check_secrets.py", ".gitignore")


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    )
    return [p for p in out.stdout.splitlines() if p.strip()]


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p.strip()]


def content_of(path: str, staged: bool) -> str:
    if staged:
        r = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def scan(paths: list[str], staged: bool) -> list[tuple[str, int, str, str]]:
    hits = []
    for path in paths:
        if any(path.replace("\\", "/").endswith(s) for s in SKIP_PATHS):
            continue
        text = content_of(path, staged)
        if not text or "\0" in text[:1024]:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW.search(line):
                continue
            for name, rule in RULES:
                if rule.search(line):
                    hits.append((path, lineno, name, line.strip()[:110]))
                    break
    return hits


def main() -> int:
    audit = "--tracked" in sys.argv
    try:
        paths = tracked_files() if audit else staged_files()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"check_secrets: could not ask git for the file list: {exc}", file=sys.stderr)
        return 2  # never report clean when the instrument did not run

    hits = scan(paths, staged=not audit)
    scope = "tracked" if audit else "staged"
    if not hits:
        print(f"check_secrets: {len(paths)} {scope} file(s) scanned, no credential found.")
        return 0

    print(f"\ncheck_secrets: REFUSING — {len(hits)} possible credential(s) in {scope} content:\n",
          file=sys.stderr)
    for path, lineno, name, line in hits:
        print(f"  {path}:{lineno}\n    rule : {name}\n    line : {line}\n", file=sys.stderr)
    print("Put the value in .env (git-ignored) and read it via config.py instead.\n"
          "If this is genuinely not a secret, commit with --no-verify and say why.\n",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
