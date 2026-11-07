"""Search past conversation transcripts: recalled words are evidence, never an origin.

``ledger recall WORDS...`` refreshes this system's recall index (only new or
changed transcript files are read) and prints the best matches. Every hit
prints its timestamp, project, speaker kind and the disclaimer. A hit is a
trigger to confirm with the person, never a standing decision.

The index lives at ``recall-<system>.db`` in the ``harness`` folder of the
user's cache folder (``.cache`` under the home folder), where
``<system>`` is the system the ledger database is claimed for; an unclaimed
ledger is refused. Room instances (``~/rooms/**``), any transcript folder
whose name contains ``room``, and other systems' workspaces are never
indexed: a folder refused by name is never listed, and a session whose
recorded working folder resolves into one is refused before any of it is
indexed. An ``--index`` or ``--project`` path under ``~/rooms`` or another
system's workspace is refused.
Without WORDS the verb only refreshes the index and prints its numbers.
The guard, the speaker classifier and the index are this package's other
modules.

This is a ledger plugin verb module: the ledger itself does not import
recall. The toolkit's plugin registry (``ledger_plugins.MODULES``) lists it,
and the toolkit's ``ledger`` command passes that list to the ledger CLI
(``cli.main(plugin_modules=...)``).
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from ledger.paths import resolve_db_path
from ledger.store import open_ledger_readonly
from ledger.verbs import (
    EXIT_NOT_FOUND,
    EXIT_REFUSED,
    CliError,
    Context,
    Result,
    UsageError,
    Verb,
)
from recall import DISCLAIMER
from recall.classify import SPEAKERS
from recall.extract import KINDS
from recall.guard import SYSTEM_NAME, Guard, RoomsPathError
from recall.index import (
    RecallIndexError,
    default_index_path,
    default_sources,
    fts_query,
    index_bytes,
    open_index,
    purge_refused,
    refresh,
    search,
)

DEFAULT_HITS = 10


def add_arguments(parser) -> None:
    parser.add_argument("words", nargs="*",
                        help="words that must all appear (none: refresh the index only)")
    parser.add_argument("--fts", action="store_true",
                        help="pass WORDS to SQLite FTS5 as one raw match expression")
    parser.add_argument("--limit", type=int, help=f"max hits (default {DEFAULT_HITS})")
    parser.add_argument("--speaker", choices=SPEAKERS)
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--project", help="only hits whose project path contains this")
    parser.add_argument("--recent", action="store_true", help="newest first instead of best match")
    parser.add_argument("--no-refresh", action="store_true",
                        help="search the index as it is, without reading transcripts")
    parser.add_argument("--index", help="index file (default ~" "/.cache/harness/recall-<system>.db)")


def _refuse(message: str) -> CliError:
    return CliError(message, code="refused", exit_code=EXIT_REFUSED)


def _system(ctx: Context) -> str:
    lg = open_ledger_readonly(ctx.db)
    try:
        system = lg.system()
    finally:
        lg.close()
    if system is None:
        raise _refuse(
            "the ledger is not claimed for a system; recall keeps one index per system, "
            "so claim the ledger first")
    if not SYSTEM_NAME.fullmatch(system):
        raise _refuse(f"the ledger's system name {system!r} is not a lowercase name")
    return system


def _count_reasons(refused: list[tuple[str, str, str]]) -> dict[str, int]:
    """Counts by reason; refused folders and files are never printed by name."""
    reasons: dict[str, int] = {}
    for _source, _name, reason in refused:
        reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


# Characters a transcript must never send to the terminal raw: C0 and C1
# controls (ESC starts every ANSI sequence), DEL, line and paragraph
# separators, and the bidirectional overrides that reorder what is shown.
_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
                  "\u2066\u2067\u2068\u2069\u2028\u2029")


def _visible(text: str, *, keep: str = "") -> str:
    """``text`` with every unsafe character written as a visible escape (``\\x1b``).

    Characters in ``keep`` (a newline or tab in an excerpt) pass through.
    """
    out = []
    for char in text:
        code = ord(char)
        if char in keep:
            out.append(char)
        elif code < 0x20 or 0x7F <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
        elif char in _BIDI:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    return "".join(out)


def _expand(value: str, what: str) -> Path:
    """``~`` expansion as a one-line usage error, never a traceback."""
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:  # ``~account`` for an account that does not exist
        raise UsageError(f"cannot expand {what} {value!r}: {exc}") from exc


def _display(project: str, home: Path) -> str:
    prefix = str(home).rstrip("/") + "/"
    return "~/" + project[len(prefix):] if project.startswith(prefix) else project


def run(args, ctx: Context) -> Result:
    limit = DEFAULT_HITS if args.limit is None else args.limit
    if type(limit) is not int or limit < 1:
        raise UsageError("--limit must be an integer >= 1")
    home = Path.home()
    system = _system(ctx)
    guard = Guard(home, system)
    index_path = (_expand(args.index, "--index") if args.index
                  else default_index_path(home, system))
    project = args.project
    if project and project.startswith("~"):
        project = str(_expand(project, "--project"))
    try:
        guard.refuse_path(index_path, "index path")
        if project and ("/" in project):
            guard.refuse_path(Path(project), "project path")
    except RoomsPathError as exc:
        raise _refuse(str(exc)) from exc
    index_path = resolve_db_path(index_path)
    try:
        conn = open_index(index_path, system)
    except RecallIndexError as exc:
        raise _refuse(str(exc)) from exc
    except OSError as exc:
        # An unwritable folder, a vanished staging file: one line, never a traceback.
        raise _refuse(f"cannot open the recall index at {index_path}: {exc}") from exc
    try:
        if args.no_refresh:
            removed = purge_refused(conn, guard)
            scan = {"refreshed": False, "files_removed": removed}
        else:
            stats = refresh(conn, guard, default_sources(home))
            scan = {
                "refreshed": True,
                "files_seen": stats.files_seen,
                "files_read": len(stats.files_read),
                "files_unchanged": stats.files_unchanged,
                "files_removed": stats.files_removed,
                "records_added": stats.records_added,
                "dirs_refused": _count_reasons(stats.refused),
                "files_refused": _count_reasons(stats.files_refused),
                "dirs_unlisted": stats.incomplete,
                "seconds": round(stats.seconds, 3),
            }
        hits, truncated = [], False
        if args.words:
            expression = " ".join(args.words) if args.fts else fts_query(" ".join(args.words))
            if not expression.strip():
                raise UsageError("no search words")
            try:
                hits, truncated = search(
                    conn, expression, limit=limit, speaker=args.speaker, kind=args.kind,
                    project=project,
                    recent=args.recent)
            except sqlite3.OperationalError as exc:
                raise UsageError(f"bad search expression: {exc}") from exc
    finally:
        conn.close()
    index = {"path": str(index_path), "bytes": index_bytes(index_path)}
    summary = (
        f"# recall index ({system}): " + ", ".join(f"{k}={v}" for k, v in scan.items())
        + f", index_bytes={index['bytes']}"
    )
    data = {
        "system": system,
        "disclaimer": DISCLAIMER,
        "hits": [{**asdict(hit), "disclaimer": DISCLAIMER} for hit in hits],
        "truncated": truncated,
        "scan": scan,
        "index": index,
    }
    if not args.words:
        return Result(data=data, text=summary)
    if not hits:
        raise CliError("no recall hits", code="empty", exit_code=EXIT_NOT_FOUND)
    lines = []
    for hit in hits:
        lines.append(
            f"{_visible(hit.ts or '-')}\t{_visible(_display(hit.project, home))}"
            f"\tspeaker={hit.speaker}\tkind={hit.kind}\t{hit.source}")
        # Verbatim: every line of the excerpt as it was, indented, never re-spaced;
        # only control characters are shown as escapes, so a transcript cannot
        # clear the screen or forge the disclaimer below.
        excerpt = _visible(hit.excerpt, keep="\n\t")
        lines.extend("    " + line for line in excerpt.split("\n"))
        lines.append(f"    ({DISCLAIMER})")
    if truncated:
        lines.append(f"# more hits than --limit {limit}")
    lines.append(summary)
    return Result(data=data, text="\n".join(lines))


VERB = Verb(
    name="recall",
    help="search past transcripts (hits are evidence, never an origin)",
    add_arguments=add_arguments,
    run=run,
)
