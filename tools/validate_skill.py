#!/usr/bin/env python3
"""Validate this skill package against the open Agent Skill conventions.

Checks performed:
  1. SKILL.md has a well-formed YAML frontmatter block.
  2. Frontmatter only uses recognised keys (name / description + a small
     set of commonly accepted optional keys). Unknown keys are rejected
     because strict hosts refuse to load a skill that carries them.
  3. `name` is lowercase-hyphen, <= 64 chars, and matches the folder name.
  4. `description` is non-empty and <= 1024 chars.
  5. Every `scripts/...`, `references/...`, `assets/...` path referenced
     from SKILL.md actually exists on disk.
  6. No host-specific coupling: the skill must not hardcode a particular
     agent product name or its internal tool names, otherwise it silently
     misleads users on every other host.
  7. Shell scripts pass `bash -n` (syntax only, nothing is executed).

Exit code is 0 when everything passes, 1 otherwise.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STANDARD_KEYS = {"name", "description"}
OPTIONAL_KEYS = {"license", "allowed-tools", "allowed_tools", "metadata", "version"}

# Product / tool names that would tie this skill to one specific host.
COUPLING_TERMS = [
    "Claude",
    "ChatGPT",
    "Cursor",
    "Codex",
    "Copilot",
    "view 工具",
    "Read 工具",
]

MAX_NAME = 64
MAX_DESC = 1024

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REF_RE = re.compile(r"`((?:scripts|references|assets)/[^`\s]+)`")

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def ok(msg: str) -> None:
    notes.append(msg)


def read_skill_md() -> str:
    path = os.path.join(ROOT, "SKILL.md")
    if not os.path.exists(path):
        fail("SKILL.md not found at repository root")
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_frontmatter(text: str):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        fail("SKILL.md has no well-formed `---` frontmatter block")
        return None
    return m.group(1)


def frontmatter_keys(block: str) -> list[str]:
    keys = []
    for line in block.split("\n"):
        km = re.match(r"^([A-Za-z0-9_-]+):", line)
        if km:
            keys.append(km.group(1))
    return keys


def field(block: str, key: str) -> str:
    m = re.search(
        r"^%s:(.*?)(?=^[A-Za-z0-9_-]+:|\Z)" % re.escape(key), block, re.M | re.S
    )
    return m.group(1).strip() if m else ""


def check_frontmatter(text: str) -> None:
    block = parse_frontmatter(text)
    if block is None:
        return

    keys = frontmatter_keys(block)
    unknown = [k for k in keys if k not in STANDARD_KEYS | OPTIONAL_KEYS]
    if unknown:
        fail(
            "frontmatter carries non-standard key(s): %s "
            "(strict hosts may refuse to load the skill; move this into the body)"
            % ", ".join(unknown)
        )
    else:
        ok("frontmatter keys are all recognised: %s" % ", ".join(keys))

    for required in sorted(STANDARD_KEYS):
        if required not in keys:
            fail("frontmatter is missing required key `%s`" % required)

    name = field(block, "name")
    if name:
        if not NAME_RE.fullmatch(name):
            fail("`name` = %r is not lowercase-hyphen (a-z, 0-9, '-')" % name)
        elif len(name) > MAX_NAME:
            fail("`name` is %d chars, limit is %d" % (len(name), MAX_NAME))
        else:
            ok("name = %s (%d chars)" % (name, len(name)))
        folder = os.path.basename(ROOT)
        if folder != name:
            notes.append(
                "note: folder name %r differs from skill name %r "
                "(fine for a git repo, but the installed folder should match)"
                % (folder, name)
            )

    desc = field(block, "description")
    if not desc:
        fail("`description` is empty")
    elif len(desc) > MAX_DESC:
        fail("`description` is %d chars, limit is %d" % (len(desc), MAX_DESC))
    else:
        ok("description = %d chars (limit %d)" % (len(desc), MAX_DESC))


def check_references(text: str) -> None:
    refs = sorted(set(REF_RE.findall(text)))
    if not refs:
        notes.append("note: SKILL.md references no bundled files")
        return
    for ref in refs:
        path = os.path.join(ROOT, ref)
        if os.path.exists(path):
            ok("referenced file exists: %s" % ref)
        else:
            fail("SKILL.md references %s but the file does not exist" % ref)


def iter_text_files():
    skip_dirs = {".git", ".github", "__pycache__", "tools"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn.endswith((".md", ".sh", ".py", ".txt")):
                yield os.path.join(dirpath, fn)


def check_coupling() -> None:
    hits = []
    for path in iter_text_files():
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if rel == "README.md":
            # The README is allowed to name hosts: it documents where to install.
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                for term in COUPLING_TERMS:
                    if term in line:
                        hits.append("%s:%d uses host-specific term %r" % (rel, lineno, term))
    if hits:
        for h in hits:
            fail(h)
    else:
        ok("no host-specific coupling terms in skill body or scripts")


def check_shell_syntax() -> None:
    scripts_dir = os.path.join(ROOT, "scripts")
    if not os.path.isdir(scripts_dir):
        return
    for fn in sorted(os.listdir(scripts_dir)):
        if not fn.endswith(".sh"):
            continue
        path = os.path.join(scripts_dir, fn)
        try:
            proc = subprocess.run(
                ["bash", "-n", path], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError) as exc:
            notes.append("note: could not run `bash -n` on %s (%s)" % (fn, exc))
            continue
        if proc.returncode == 0:
            ok("bash -n passes: scripts/%s" % fn)
        else:
            fail("bash -n failed on scripts/%s:\n%s" % (fn, proc.stderr.strip()))


def check_deprecated_ffmpeg() -> None:
    """`-vsync` was superseded by `-fps_mode` in ffmpeg 5.0."""
    scripts_dir = os.path.join(ROOT, "scripts")
    if not os.path.isdir(scripts_dir):
        return
    for fn in sorted(os.listdir(scripts_dir)):
        if not fn.endswith(".sh"):
            continue
        with open(os.path.join(scripts_dir, fn), encoding="utf-8") as fh:
            text = fh.read()
        # Bare `-vsync` without a runtime capability probe is the problem.
        if "-vsync" in text and "-fps_mode" not in text:
            fail(
                "scripts/%s uses deprecated `-vsync` with no `-fps_mode` fallback "
                "(ffmpeg >= 5.0 warns on it)" % fn
            )
        else:
            ok("scripts/%s handles ffmpeg fps flag compatibly" % fn)


def main() -> int:
    text = read_skill_md()
    if text:
        check_frontmatter(text)
        check_references(text)
    check_coupling()
    check_shell_syntax()
    check_deprecated_ffmpeg()

    print("=" * 62)
    print("skill validation: %s" % os.path.basename(ROOT))
    print("=" * 62)
    for n in notes:
        print("  PASS  %s" % n)
    if failures:
        print()
        for f in failures:
            print("  FAIL  %s" % f)
        print()
        print("RESULT: FAIL (%d issue%s)" % (len(failures), "" if len(failures) == 1 else "s"))
        return 1
    print()
    print("RESULT: PASS (%d checks)" % len(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
