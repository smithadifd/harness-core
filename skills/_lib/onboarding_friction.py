"""Record onboarding obstacles and render a compact, read-only digest."""

import fcntl
import hashlib
import json
import os
import stat
import unicodedata
from contextlib import contextmanager
from pathlib import Path


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MAX_LINES = 24
_LOG_NAME = "onboarding-friction.jsonl"
_CREATE_ATTEMPTS = 5
_DISPLAY_FIELD_WIDTH = 26


@contextmanager
def _directory_fd(path: Path | str, *, dir_fd: int | None = None):
    """Open an existing directory without following a symlink."""
    fd = os.open(path, _DIRECTORY_FLAGS, dir_fd=dir_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def append(root: Path | str, obstacle: str, proposed_change: str, needs_owner: bool) -> None:
    """Append one obstacle under an existing root."""
    if not isinstance(obstacle, str) or not isinstance(proposed_change, str):
        raise TypeError("obstacle and proposed_change must be strings")
    if not isinstance(needs_owner, bool):
        raise TypeError("needs_owner must be a bool")
    record = {
        "obstacle": obstacle,
        "proposed_change": proposed_change,
        "needs_owner": needs_owner,
    }
    with _directory_fd(root) as root_fd:
        try:
            os.mkdir("topics", dir_fd=root_fd)
        except FileExistsError:
            pass
        for attempt in range(_CREATE_ATTEMPTS):
            with _directory_fd("topics", dir_fd=root_fd) as topics_fd:
                try:
                    fd = os.open(
                        _LOG_NAME,
                        os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                        0o600,
                        dir_fd=topics_fd,
                    )
                except FileNotFoundError:
                    if attempt + 1 < _CREATE_ATTEMPTS:
                        continue
                    raise
                try:
                    _check_log_fd(fd)
                    # Exclusive lock spans the last-byte check and the write so
                    # another writer cannot finish a record after we decide to
                    # insert a separator, which would leave a blank line.
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    size = os.fstat(fd).st_size
                    separator = b"\n" if size and os.pread(fd, 1, size - 1) != b"\n" else b""
                    payload = separator + (
                        json.dumps(record, ensure_ascii=False) + "\n"
                    ).encode("utf-8")
                    _write_all(fd, payload)
                except BaseException:
                    os.close(fd)
                    raise
                os.close(fd)
                return


def _check_log_fd(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError("friction log must be a regular file with one link")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(f"os.write returned {written} without raising")
        view = view[written:]


def _single_line(value: str) -> str:
    return " ".join(
        "".join(" " if ord(char) < 32 or 127 <= ord(char) <= 159 else char for char in value).split()
    )


def _terminal_width(value: str) -> int:
    return sum(
        0 if unicodedata.combining(char)
        else 2 if unicodedata.east_asian_width(char) in "WF"
        else 1
        for char in value
    )


def _display_field(
    value: str, *, width_limit: int = _DISPLAY_FIELD_WIDTH, identify_truncation: bool = False
) -> str:
    clean = _single_line(value)

    if _terminal_width(clean) <= width_limit:
        return clean
    suffix = "..."
    if identify_truncation:
        suffix += " #" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    budget = width_limit - len(suffix)
    used = 0
    end = 0
    for index, char in enumerate(clean):
        char_width = _terminal_width(char)
        if used + char_width > budget:
            break
        used += char_width
        end = index + 1
    return clean[:end].rstrip() + suffix


def digest(root: Path | str) -> str:
    """Summarize logged obstacles in at most one screen of terminal lines."""
    records: list[dict] = []
    malformed: list[int] = []
    with _directory_fd(root) as root_fd:
        try:
            with _directory_fd("topics", dir_fd=root_fd) as topics_fd:
                try:
                    fd = os.open(
                        _LOG_NAME,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=topics_fd,
                    )
                except FileNotFoundError:
                    return "No onboarding friction logged."
                try:
                    _check_log_fd(fd)
                    # Shared lock so a digest does not observe an in-flight
                    # append as a torn last line.
                    fcntl.flock(fd, fcntl.LOCK_SH)
                    log = os.fdopen(fd, "rb")
                except BaseException:
                    os.close(fd)
                    raise
                with log:
                    for line_number, line in enumerate(log, 1):
                        try:
                            record = json.loads(line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            malformed.append(line_number)
                            continue
                        if (
                            not isinstance(record, dict)
                            or not isinstance(record.get("obstacle"), str)
                            or not isinstance(record.get("proposed_change"), str)
                            or not isinstance(record.get("needs_owner"), bool)
                        ):
                            malformed.append(line_number)
                            continue
                        records.append(record)
        except FileNotFoundError:
            return "No onboarding friction logged."

    if not records and not malformed:
        return "No onboarding friction logged."
    lines = [f"Onboarding friction: {len(records)} logged"]
    # Reserve a line each for overflow and malformed input when present.
    slots = _MAX_LINES - 1 - bool(malformed)
    visible = min(len(records), slots if len(records) <= slots else slots - 1)
    for number, record in enumerate(records[:visible], 1):
        call = "yes" if record["needs_owner"] else "no"
        prefix = f"{number}. Hit: {_display_field(record['obstacle'])} | Proposed: "
        suffix = f" | Needs owner: {call}"
        proposed = _display_field(
            record["proposed_change"],
            width_limit=100 - _terminal_width(prefix) - _terminal_width(suffix),
            identify_truncation=True,
        )
        lines.append(prefix + proposed + suffix)
    if hidden := records[visible:]:
        calls = sum(record["needs_owner"] for record in hidden)
        lines.append(f"{len(hidden)} more logged ({calls} need the owner).")
    if malformed:
        numbers = ", ".join(str(number) for number in malformed[:8])
        extra = f", +{len(malformed) - 8} more" if len(malformed) > 8 else ""
        lines.append(f"Skipped malformed line(s): {numbers}{extra}.")
    return "\n".join(lines)
