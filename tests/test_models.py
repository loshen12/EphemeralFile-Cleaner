"""efc.models 测试：字段与 Spec §3 完全一致、trashed/failed 语义（T002 验收）。"""

import dataclasses
from pathlib import Path

from efc.models import (
    CleanOutcome,
    FileMatch,
    FileOutcome,
    RiskDecision,
    ScanResult,
)


def spec_fields(cls: type) -> list[tuple[str, object]]:
    return [(f.name, f.type) for f in dataclasses.fields(cls)]


def test_file_match_fields() -> None:
    assert spec_fields(FileMatch) == [
        ("path", Path),
        ("relative", str),
        ("size", int),
        ("mtime", float),
        ("pattern", str),
    ]


def test_scan_result_fields() -> None:
    assert spec_fields(ScanResult) == [
        ("root", Path),
        ("recursive", bool),
        ("matches", list[FileMatch]),
        ("scanned_dirs", int),
    ]


def test_risk_decision_fields() -> None:
    assert spec_fields(RiskDecision) == [("high_risk", bool), ("reason", str | None)]
    assert RiskDecision(high_risk=False).reason is None


def test_file_outcome_fields_and_defaults() -> None:
    assert spec_fields(FileOutcome) == [
        ("path", Path),
        ("status", str),
        ("backup_path", Path | None),
        ("error", str | None),
        ("size", int),
        ("pattern", str | None),
    ]
    o = FileOutcome(path=Path("f.tmp"), status="trashed")
    assert (o.backup_path, o.error, o.size, o.pattern) == (None, None, 0, None)


def test_clean_outcome_fields_and_defaults() -> None:
    assert spec_fields(CleanOutcome) == [
        ("total_matched", int),
        ("results", list[FileOutcome]),
        ("batches", int),
        ("backup_dir", Path | None),
        ("aborted", bool),
        ("task_name", str | None),
        ("target_dir", Path | None),
        ("duration_seconds", float),
        ("total_bytes", int),
    ]
    o = CleanOutcome(total_matched=0, results=[], batches=0, backup_dir=None)
    assert o.aborted is False
    assert o.task_name is None  # 一次性任务为 None
    assert o.target_dir is None
    assert o.duration_seconds == 0.0
    assert o.total_bytes == 0


def test_clean_outcome_task_name_accepts_str() -> None:
    o = CleanOutcome(
        total_matched=0, results=[], batches=0, backup_dir=None, task_name="downloads"
    )
    assert o.task_name == "downloads"


def _fo(status: str) -> FileOutcome:
    return FileOutcome(path=Path("f.tmp"), status=status, size=10)


def test_trashed_and_failed_semantics() -> None:
    o = CleanOutcome(
        total_matched=3,
        results=[_fo("trashed"), _fo("backup_failed"), _fo("trash_failed")],
        batches=1,
        backup_dir=None,
    )
    assert [f.status for f in o.trashed] == ["trashed"]
    assert [f.status for f in o.failed] == ["backup_failed", "trash_failed"]
    assert len(o.trashed) + len(o.failed) == len(o.results)


def test_trashed_and_failed_empty() -> None:
    o = CleanOutcome(total_matched=0, results=[], batches=0, backup_dir=None)
    assert o.trashed == []
    assert o.failed == []
