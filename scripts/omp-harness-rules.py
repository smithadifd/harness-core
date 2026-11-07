#!/usr/bin/env python3
"""Copy the shared Claude Code rules and one overlay into OMP's rules folder.

OMP never reads the Claude rules folder (`.claude/rules/` under the home
folder), and it skips a rules file that has no
`alwaysApply: true` frontmatter. This script copies harness.md plus the one
`harness-*.md` overlay present into OMP's rules folder with that frontmatter
added, so OMP follows the same rules.

Exit codes: 0 done or up to date, 1 refused or out of date (nothing changed),
2 could not run (bad source, OMP missing, probe could not finish).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SHARED = "harness.md"
OVERLAY_GLOB = "harness-*.md"
OWNER_MARKER = "by claude-toolkit scripts/omp-harness-rules.py"
#: The Claude rules folder as messages name it, joined from two parts so this
#: source holds no home-folder path literal.
RULES_HOME = "~" + "/.claude/rules"
PROBE_TIMEOUT = 180


class Refusal(Exception):
    """A state the script will not change on its own (exit 1)."""


class Unusable(Exception):
    """Input or environment the script cannot work with (exit 2)."""


def first_heading(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line
    raise Unusable(f"{name} has no '# ' heading line")


def render(name: str, text: str) -> str:
    if text.startswith("---"):
        raise Unusable(f"{name} already starts with frontmatter; copy it by hand")
    heading = first_heading(text, name)[2:].strip()
    description = (
        f"{heading} (copied from {RULES_HOME}/{name} {OWNER_MARKER}; "
        "re-run it after that file changes)"
    )
    return f"---\ndescription: {json.dumps(description, ensure_ascii=False)}\nalwaysApply: true\n---\n{text}"


def is_ours(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    return end != -1 and OWNER_MARKER in text[4:end]


def read_sources(source: Path) -> tuple[str, dict[str, str]]:
    shared = source / SHARED
    if not shared.is_file():
        raise Unusable(f"{shared} not found; copy the rules into {RULES_HOME}/ first")
    present = sorted(
        path.name for path in source.glob(OVERLAY_GLOB) if path.is_file()
    )
    if len(present) != 1:
        found = ", ".join(present) if present else "none"
        raise Unusable(
            f"expected exactly one {OVERLAY_GLOB} overlay in {source}, found {found}"
        )
    overlay = present[0]
    files = {}
    for name in (SHARED, overlay):
        try:
            files[name] = render(name, (source / name).read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise Unusable(f"{source / name} is not UTF-8: {exc}") from exc
    return overlay, files


def plan(target: Path, overlay: str, files: dict[str, str]) -> list[tuple[str, str]]:
    """Return (name, action) pairs; action is 'unchanged', 'create' or 'update'."""
    others = sorted(
        path for path in target.glob(OVERLAY_GLOB)
        if path.name != overlay and (path.exists() or path.is_symlink())
    )
    if others:
        found = ", ".join(str(path) for path in others)
        raise Refusal(
            f"{found} is present; expected only overlay {overlay}. "
            "Remove the other overlay by hand, then re-run"
        )
    actions = []
    for name, wanted in files.items():
        path = target / name
        if path.is_symlink():
            raise Refusal(f"{path} is a symlink; OMP rules here must be plain files. Remove it, then re-run")
        if not path.exists():
            actions.append((name, "create"))
        elif not path.is_file():
            raise Refusal(f"{path} is not a regular file")
        elif not is_ours(path):
            raise Refusal(f"{path} exists and was not written by this script; move it aside, then re-run")
        elif path.read_text(encoding="utf-8") == wanted:
            actions.append((name, "unchanged"))
        else:
            actions.append((name, "update"))
    return actions


def write_all(target: Path, pending: dict[str, tuple[str, str]]) -> None:
    """Write every pending file, or none of them.

    `pending` maps a file name to (action, text). Files are staged first, so a
    staging failure leaves the target untouched. A new file is linked into
    place only if nothing has appeared at its name since planning; an existing
    file is replaced only if it is still this script's own. If any step fails,
    the files already placed are rolled back, but only while each still is the
    exact file this run placed.

    This guards against mistakes, not against another process racing writes
    into the same folder: between the ownership re-check and the rename of an
    update there is a window that plain path operations cannot close.
    """
    staged: list[tuple[str, Path]] = []
    placed: list[tuple[str, tuple[int, int], bytes | None]] = []
    try:
        for name, (_, text) in pending.items():
            fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=target)
            staged.append((name, Path(tmp)))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        for name, tmp in staged:
            action, _ = pending[name]
            dest = target / name
            info = os.lstat(tmp)
            identity = (info.st_dev, info.st_ino)
            if action == "create":
                try:
                    os.link(tmp, dest)
                except FileExistsError:
                    raise Refusal(f"{dest} appeared while this script ran; nothing was written") from None
                placed.append((name, identity, None))
            else:
                if dest.is_symlink() or not is_ours(dest):
                    raise Refusal(f"{dest} changed while this script ran; nothing was written")
                previous = dest.read_bytes()
                os.replace(tmp, dest)
                placed.append((name, identity, previous))
    except BaseException:
        for name, identity, previous in reversed(placed):
            dest = target / name
            try:
                info = os.lstat(dest)
                if (info.st_dev, info.st_ino) != identity:
                    print(f"error: {dest} changed after this run placed it; left alone", file=sys.stderr)
                elif previous is None:
                    dest.unlink()
                else:
                    dest.write_bytes(previous)
            except OSError as exc:
                print(f"error: could not roll back {dest}: {exc}", file=sys.stderr)
        raise
    finally:
        for _, tmp in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as exc:
                print(f"warning: could not remove the staging file {tmp}: {exc}", file=sys.stderr)


def omp_rules_dir() -> Path:
    omp = shutil.which("omp")
    if omp is None:
        raise Unusable("omp is not on PATH; install OMP first, or pass --target")
    try:
        result = subprocess.run([omp, "config", "path"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unusable(f"'omp config path' did not finish: {exc}") from exc
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines:
        raise Unusable(f"'omp config path' failed: {result.stderr.strip() or result.stdout.strip()}")
    agent = Path(lines[-1].strip())
    if not agent.is_absolute():
        raise Unusable(f"'omp config path' printed a relative path: {agent}")
    return agent / "rules"


def probe(expected: list[str], model: str | None) -> int:
    omp = shutil.which("omp")
    if omp is None:
        print("probe: could not verify: omp is not on PATH", file=sys.stderr)
        return 2
    prompt = (
        "Without using tools, quote verbatim every line of your instructions that begins with "
        "'# Harness agreements'. One per line, nothing else. If there are none, reply NONE."
    )
    cmd = [omp, "-p", "--no-tools", "--no-session"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    with tempfile.TemporaryDirectory(prefix="omp-rules-probe-") as cwd:
        try:
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"probe: could not verify: {exc}", file=sys.stderr)
            return 2
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-500:]
        print(f"probe: could not verify: omp exited {result.returncode}: {detail}", file=sys.stderr)
        return 2
    seen = {line.strip() for line in result.stdout.splitlines()}
    missing = [line for line in expected if line not in seen]
    if missing:
        for line in missing:
            print(f"probe: GAP: OMP did not quote {line!r}")
        return 1
    for line in expected:
        print(f"probe: PASS: OMP quoted {line!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=Path.home() / ".claude" / "rules",
                        help=f"folder holding the harness rules (default {RULES_HOME})")
    parser.add_argument("--target", type=Path,
                        help="OMP rules folder (default: <omp config path>/rules)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report whether OMP's copies are current; never writes the rules folder")
    mode.add_argument("--probe", action="store_true",
                      help="ask OMP to quote the rule headings; one model call, never writes the rules folder")
    parser.add_argument("--model", help="model for --probe (default: OMP's default model)")
    args = parser.parse_args(argv)

    try:
        overlay, files = read_sources(args.source.expanduser())
        if args.probe:
            expected = [first_heading(text, name) for name, text in files.items()]
            if len(set(expected)) != len(expected):
                raise Unusable("the rules files share a heading, so the probe cannot tell them apart")
            return probe(expected, args.model)
        target = args.target.expanduser() if args.target else omp_rules_dir()
        if target.exists() and not target.is_dir():
            raise Unusable(f"{target} exists and is not a folder")
        if not args.check:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise Unusable(f"cannot create {target}: {exc}") from exc
        actions = plan(target, overlay, files) if target.is_dir() else [(n, "create") for n in files]
    except Unusable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    if args.check:
        stale = [(name, action) for name, action in actions if action != "unchanged"]
        for name, action in actions:
            state = "up to date" if action == "unchanged" else ("missing" if action == "create" else "out of date")
            print(f"{target / name}: {state}")
        return 1 if stale else 0

    try:
        write_all(target, {name: (action, files[name]) for name, action in actions if action != "unchanged"})
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: writing into {target} failed: {exc}", file=sys.stderr)
        return 2
    for name, action in actions:
        print(f"{action}: {target / name}")
    print(f"overlay: {overlay}. Next: python3 {Path(__file__).resolve()} --probe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
