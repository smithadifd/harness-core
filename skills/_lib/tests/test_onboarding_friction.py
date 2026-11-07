"""The friction log stays inside an explicit root and fits a terminal screen."""

import fcntl
import importlib.util
import hashlib
import json
import os
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "onboarding_friction.py"
spec = importlib.util.spec_from_file_location("onboarding_friction_under_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
friction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(friction)


def _terminal_width(line):
    return sum(
        0 if unicodedata.combining(char)
        else 2 if unicodedata.east_asian_width(char) in "WF"
        else 1
        for char in line
    )


def _exclusive_lock_held(path):
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        held = True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        held = False
    finally:
        os.close(fd)
    return held


def test_append_preserves_order_and_one_line_per_obstacle(tmp_path):
    friction.append(tmp_path, "First stumble", "Fix first", False)
    friction.append(tmp_path, "Second stumble", "Fix second", True)

    lines = (tmp_path / "topics" / "onboarding-friction.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["obstacle"] for line in lines] == [
        "First stumble", "Second stumble"
    ]
    assert [json.loads(line)["needs_owner"] for line in lines] == [False, True]


def test_append_after_unterminated_malformed_line(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    log = topics / "onboarding-friction.jsonl"
    log.write_text("broken fragment", encoding="utf-8")

    friction.append(tmp_path, "New stumble", "New fix", True)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "broken fragment"
    assert len(lines) == 2
    assert json.loads(lines[1])["obstacle"] == "New stumble"
    result = friction.digest(tmp_path)
    assert "New stumble" in result
    assert "Skipped malformed line(s): 1." in result


def test_digest_skips_and_names_malformed_line(tmp_path):
    friction.append(tmp_path, "Valid stumble", "Valid fix", True)
    log = tmp_path / "topics" / "onboarding-friction.jsonl"
    with log.open("a") as output:
        output.write("bad json\n")
        output.write('{"obstacle": "missing fields"}\n')

    result = friction.digest(tmp_path)
    assert "Valid stumble" in result
    assert "Skipped malformed line(s): 2, 3." in result
    assert "missing fields" not in result


def test_digest_names_changes_calls_and_caps_lines(tmp_path):
    for obstacle, change, needs_call in [
        ("First stumble", "First change", False),
        ("Second stumble", "Second change", True),
        ("Third stumble", "Third change", True),
    ]:
        friction.append(tmp_path, obstacle, change, needs_call)

    result = friction.digest(tmp_path)
    assert "Onboarding friction: 3 logged" in result
    assert "First stumble | Proposed: First change | Needs owner: no" in result
    assert "Second stumble | Proposed: Second change | Needs owner: yes" in result
    assert "Third stumble | Proposed: Third change | Needs owner: yes" in result
    assert len(result.splitlines()) <= 24


def test_digest_caps_obstacle_and_proposed_change_width(tmp_path):
    obstacle = "A" * (5 * 1024 * 1024)
    friction.append(tmp_path, obstacle, "B" * 1000, True)
    friction.append(tmp_path, "界" * 50, "界" * 1000, False)

    result = friction.digest(tmp_path)
    assert all(_terminal_width(line) <= 100 for line in result.splitlines())
    assert "A" * 10 in result
    change_hash = hashlib.sha256(("B" * 1000).encode()).hexdigest()[:8]
    assert f"... #{change_hash} | Needs owner: yes" in result
    assert "..." in result
    assert "A" * 27 not in result
    assert len(result.splitlines()) <= 24


def test_digest_preserves_distinct_long_proposed_changes(tmp_path):
    changes = [
        "Add setup steps for X " + "A" * 1000,
        "Remove obsolete setup steps for X " + "A" * 1000,
    ]
    for number, change in enumerate(changes):
        friction.append(tmp_path, f"Stumble {number}", change, False)

    result = friction.digest(tmp_path)
    assert all(_terminal_width(line) <= 100 for line in result.splitlines())
    assert "Proposed: Add setup steps for X" in result
    assert "Proposed: Remove obsolete setup steps for X" in result
    for change in changes:
        change_hash = hashlib.sha256(change.encode()).hexdigest()[:8]
        assert f"... #{change_hash} | Needs owner: no" in result
    assert len(set(result.splitlines()[1:])) == 2
    assert len(result.splitlines()) <= 24


def test_digest_summarizes_overflow_within_line_cap(tmp_path):
    for number in range(30):
        friction.append(tmp_path, f"Stumble {number}", f"Change {number}", number % 2 == 0)

    result = friction.digest(tmp_path)
    assert len(result.splitlines()) <= 24
    assert "Stumble 0 | Proposed: Change 0 | Needs owner: yes" in result
    assert "Stumble 1 | Proposed: Change 1 | Needs owner: no" in result
    assert "8 more logged (4 need the owner)." in result


def test_zero_friction_and_missing_root(tmp_path):
    assert friction.digest(tmp_path) == "No onboarding friction logged."
    assert not (tmp_path / "topics").exists()
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics" / "onboarding-friction.jsonl").touch()
    assert friction.digest(tmp_path) == "No onboarding friction logged."
    with pytest.raises(FileNotFoundError):
        friction.append(tmp_path / "missing", "stumble", "change", False)
    with pytest.raises(FileNotFoundError):
        friction.digest(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_append_refuses_symlink_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "topics").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        friction.append(root, "stumble", "change", False)
    assert list(outside.iterdir()) == []


def test_append_refuses_symlink_log_outside_root(tmp_path):
    root = tmp_path / "root"
    (root / "topics").mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("untouched")
    (root / "topics" / "onboarding-friction.jsonl").symlink_to(outside)

    with pytest.raises(OSError):
        friction.append(root, "stumble", "change", False)
    assert outside.read_text() == "untouched"


def test_append_refuses_hard_link_outside_root(tmp_path):
    root = tmp_path / "root"
    (root / "topics").mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("untouched")
    os.link(outside, root / "topics" / "onboarding-friction.jsonl")

    with pytest.raises(OSError, match="one link"):
        friction.append(root, "stumble", "change", False)
    assert outside.read_text() == "untouched"


def test_digest_skips_and_names_invalid_utf8_line(tmp_path):
    friction.append(tmp_path, "Valid stumble", "Valid fix", False)
    log = tmp_path / "topics" / "onboarding-friction.jsonl"
    with log.open("ab") as output:
        output.write(b"\xff invalid utf-8\n")
    assert "Skipped malformed line(s): 2." in friction.digest(tmp_path)
    assert "Valid stumble" in friction.digest(tmp_path)


def test_first_append_retries_consecutive_missing_file_errors(tmp_path, monkeypatch):
    original_open = os.open
    failures_left = 3
    attempts = 0

    def transient_open(path, flags, *args, **kwargs):
        nonlocal failures_left, attempts
        if path == "onboarding-friction.jsonl" and flags & os.O_CREAT:
            attempts += 1
            if failures_left:
                failures_left -= 1
                raise FileNotFoundError(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(friction.os, "open", transient_open)
    friction.append(tmp_path, "Stumble", "Fix", False)
    assert attempts == 4
    lines = (tmp_path / "topics" / "onboarding-friction.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["obstacle"] == "Stumble"


def test_first_append_raises_after_bounded_missing_file_errors(tmp_path, monkeypatch):
    original_open = os.open
    attempts = 0

    def missing_open(path, flags, *args, **kwargs):
        nonlocal attempts
        if path == "onboarding-friction.jsonl" and flags & os.O_CREAT:
            attempts += 1
            raise FileNotFoundError(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(friction.os, "open", missing_open)
    with pytest.raises(FileNotFoundError):
        friction.append(tmp_path, "Stumble", "Fix", False)
    assert attempts == 5
    assert not (tmp_path / "topics" / "onboarding-friction.jsonl").exists()


def test_first_append_with_concurrent_creation(tmp_path):
    start = threading.Barrier(8)

    def write(number):
        for entry in range(50):
            if entry == 0:
                start.wait(timeout=10)
            friction.append(tmp_path, f"Stumble {number}-{entry}", "Fix", False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    lines = (tmp_path / "topics" / "onboarding-friction.jsonl").read_text().splitlines()
    assert len(lines) == 400
    assert {json.loads(line)["obstacle"] for line in lines} == {
        f"Stumble {number}-{entry}" for number in range(8) for entry in range(50)
    }


def test_concurrent_appends_produce_one_valid_line_each_when_last_byte_check_races(
    tmp_path, monkeypatch
):
    n = 8
    friction.append(tmp_path, "seed", "fix", False)
    log = tmp_path / "topics" / "onboarding-friction.jsonl"
    original_pread = friction.os.pread

    def pread_as_inflight_writer_would(fd, nbytes, offset):
        if _exclusive_lock_held(log):
            return original_pread(fd, nbytes, offset)
        return b"\0"

    original_write = friction.os.write
    unlocked_writes = []

    def write_checking_lock(fd, data):
        if not _exclusive_lock_held(log):
            unlocked_writes.append(bytes(data))
        return original_write(fd, data)

    monkeypatch.setattr(friction.os, "pread", pread_as_inflight_writer_would)
    monkeypatch.setattr(friction.os, "write", write_checking_lock)
    start = threading.Barrier(n)

    def write(number):
        start.wait(timeout=10)
        friction.append(tmp_path, f"Stumble {number}", "Fix", False)

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(write, range(n)))
    assert unlocked_writes == []
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n + 1
    assert all(lines)
    records = [json.loads(line) for line in lines]
    assert records[0]["obstacle"] == "seed"
    assert {record["obstacle"] for record in records[1:]} == {
        f"Stumble {number}" for number in range(n)
    }


def test_digest_removes_terminal_controls(tmp_path):
    friction.append(tmp_path, "ESC\x1b[31m NUL\x00 C1\x9b", "Fix\x1b[0m", True)
    result = friction.digest(tmp_path)
    assert "ESC [31m NUL C1" in result
    assert "Proposed: Fix [0m" in result
    assert not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in result.replace("\n", ""))


def test_nonregular_log_is_refused_without_blocking(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    os.mkfifo(topics / "onboarding-friction.jsonl")
    with pytest.raises(OSError):
        friction.append(tmp_path, "stumble", "fix", False)
    with pytest.raises(OSError, match="regular file"):
        friction.digest(tmp_path)


def test_write_failure_closes_log_descriptor(tmp_path, monkeypatch):
    descriptors = []

    def fail_write(fd, data):
        descriptors.append(fd)
        raise OSError("write failed")

    monkeypatch.setattr(friction.os, "write", fail_write)
    with pytest.raises(OSError, match="write failed"):
        friction.append(tmp_path, "stumble", "fix", False)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_digest_fdopen_failure_closes_log_descriptor(tmp_path, monkeypatch):
    friction.append(tmp_path, "stumble", "fix", False)
    descriptors = []

    def fail_fdopen(fd, *args, **kwargs):
        descriptors.append(fd)
        raise RuntimeError("fdopen failed")

    monkeypatch.setattr(friction.os, "fdopen", fail_fdopen)
    with pytest.raises(RuntimeError, match="fdopen failed"):
        friction.digest(tmp_path)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
