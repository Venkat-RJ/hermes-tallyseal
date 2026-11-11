"""Bounded, exact CSV reconciliation. The model does not supply the contract."""
import csv
import hashlib
import io
import os
import re
import stat
import tempfile
from pathlib import Path

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 50_000
MAX_DETAILS = 12
SUM = "total_minor"
COUNT = "row_count"
HEX = re.compile(r"[a-f0-9]{64}")
INTEGER = re.compile(r"-?[0-9]{1,29}")


class CheckError(Exception):
    """An operator-actionable error safe to show in chat."""


def _snapshot(root_fd, name):
    # O_NONBLOCK keeps a substituted FIFO from blocking before fstat can reject it.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with os.fdopen(os.open(name, flags, dir_fd=root_fd), "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise CheckError(f"{name} must be a regular file with one link")
            if before.st_size > MAX_BYTES:
                raise CheckError(f"{name} exceeds 4 MiB")
            data = handle.read(MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError:
        raise CheckError(f"{name} could not be opened safely") from None
    if len(data) > MAX_BYTES:
        raise CheckError(f"{name} exceeds 4 MiB")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise CheckError(f"{name} changed during the read; retry after the writer finishes")
    return data


def _rows(data, name, required, *, exact=False):
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, [])
        if not header or any(not col for col in header) or len(set(header)) != len(header):
            raise CheckError(f"{name} needs a unique, non-empty header")
        if not set(required).issubset(header) or (exact and set(header) != set(required)):
            raise CheckError(f"{name} columns do not match the configured contract")
        positions = [header.index(col) for col in required]
        for record, row in enumerate(reader, 2):
            if record > MAX_ROWS + 1:
                raise CheckError(f"{name} exceeds 50,000 data records")
            if len(row) != len(header) or any("\x00" in cell for cell in row):
                raise CheckError(f"{name} record {record} is malformed")
            yield record, tuple(row[index] for index in positions)
    except (csv.Error, UnicodeError):
        raise CheckError(f"{name} is not valid UTF-8 CSV within parser limits") from None


def _key(cells, name, record):
    if any(not cell.strip() or len(cell) > 256 for cell in cells):
        raise CheckError(f"{name} record {record} has a blank or oversized group key")
    return cells


def _int(cell, name, record, digits=24):
    if not INTEGER.fullmatch(cell) or len(cell.lstrip("-")) > digits:
        raise CheckError(f"{name} record {record} needs an integer of at most {digits} digits")
    return int(cell)


def check(root, source_sha, group_by, amount_column):
    """Return redacted structured evidence and the exact checked report bytes."""
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise CheckError("Configure root as an absolute path in TallySeal settings")
    if not isinstance(source_sha, str) or not HEX.fullmatch(source_sha):
        raise CheckError("Configure the trusted lowercase source_sha256 in TallySeal settings")
    if (
        not isinstance(group_by, list)
        or not 1 <= len(group_by) <= 4
        or any(not isinstance(col, str) or not col.strip() for col in group_by)
        or len(set(group_by)) != len(group_by)
        or any(col in (SUM, COUNT) for col in group_by)
        or not isinstance(amount_column, str)
        or not amount_column.strip()
        or amount_column in group_by
    ):
        raise CheckError("Configure one to four distinct group_by columns and an amount_column")
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, ValueError):
        raise CheckError("The configured job folder could not be opened safely") from None
    try:
        source = _snapshot(root_fd, "source.csv")
        if hashlib.sha256(source).hexdigest() != source_sha:
            raise CheckError("source.csv no longer matches the operator's pinned SHA-256")
        report = _snapshot(root_fd, "report.csv")
    finally:
        os.close(root_fd)

    expected = {}
    source_count = source_total = 0
    for record, (*group, amount) in _rows(source, "source.csv", [*group_by, amount_column]):
        key = _key(tuple(group), "source.csv", record)
        value = _int(amount, "source.csv", record)
        count, total, first = expected.get(key, (0, 0, record))
        expected[key] = (count + 1, total + value, first)
        source_count += 1
        source_total += value
    if not source_count:
        raise CheckError("source.csv has no data records")

    seen = set()
    report_count = report_total = issue_count = 0
    issues = []

    def issue(kind, **locations):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < MAX_DETAILS:
            issues.append({"kind": kind, **locations})

    for record, (*group, count, total) in _rows(report, "report.csv", [*group_by, COUNT, SUM], exact=True):
        key = _key(tuple(group), "report.csv", record)
        n = _int(count, "report.csv", record)
        value = _int(total, "report.csv", record, digits=29)
        if n <= 0:
            raise CheckError(f"report.csv record {record} needs a positive row_count")
        report_count += n
        report_total += value
        if key in seen:
            issue("duplicate_group", report_record=record)
            continue
        seen.add(key)
        if key not in expected:
            issue("unexpected_group", report_record=record)
            continue
        expected_n, expected_value, first = expected[key]
        if n != expected_n:
            issue("row_count_mismatch", report_record=record, first_source_record=first)
        if value != expected_value:
            issue("total_mismatch", report_record=record, first_source_record=first)
    for key, (_, _, first) in expected.items():
        if key not in seen:
            issue("missing_group", first_source_record=first)

    return {
        "status": "pass" if not issue_count else "fail",
        "grand_total_matches": report_total == source_total,
        "total_row_count_matches": report_count == source_count,
        "expected_groups": len(expected),
        "reported_groups": len(seen),
        "issue_count": issue_count,
        "issues": issues,
        "source_sha256": source_sha,
        "report_sha256": hashlib.sha256(report).hexdigest(),
    }, report


def seal(snapshot, out):
    """Publish only this in-memory snapshot locally, atomically and without clobbering."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=out.parent, prefix=".tallyseal-", delete=False) as f:
            tmp = Path(f.name)
            f.write(snapshot)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, out)
    except FileExistsError:
        raise CheckError("Seal destination already exists; choose a new path") from None
    except OSError:
        raise CheckError("Could not write the sealed copy in the destination folder") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
