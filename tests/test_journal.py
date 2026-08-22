"""efc.journal 测试（Spec §14）：追加单行 JSONL/字段完整/result 判定/写失败不抛。"""

import json
from pathlib import Path

from efc.journal import ExecutionLog, build_record, judge_result
from efc.models import CleanOutcome, FileOutcome


def fo(status: str, size: int = 1, pattern: str | None = r"\.tmp$") -> FileOutcome:
    return FileOutcome(path=Path("/t/a.tmp"), status=status, size=size, pattern=pattern)


def outcome(results: list[FileOutcome], *, name: str | None = None,
            aborted: bool = False, duration: float = 1.0) -> CleanOutcome:
    return CleanOutcome(total_matched=len(results), results=results, batches=1,
                        backup_dir=None, aborted=aborted, task_name=name,
                        target_dir=Path("/t"), duration_seconds=duration)


# ---------- result 判定优先级 ----------


def test_judge_aborted_wins():
    oks = outcome([fo("trashed")])
    aborted = outcome([], aborted=True)
    assert judge_result([aborted, oks], False) == "aborted"
    assert judge_result([aborted], True) == "aborted"  # aborted 优先于 dry_run


def test_judge_partial_over_dry_run():
    assert judge_result([outcome([fo("trash_failed")])], True) == "partial"


def test_judge_dry_run_and_completed():
    assert judge_result([outcome([fo("trashed")])], True) == "dry_run"
    assert judge_result([outcome([fo("trashed")])], False) == "completed"


# ---------- record 与 JSONL ----------


def test_record_appends_single_line_json(tmp_path: Path):
    log = ExecutionLog(tmp_path / ".efc.log")
    rec = build_record("clean", [
        outcome([fo("trashed", 3), fo("trash_failed", 9, r"\.bak$")], name="downloads"),
    ], False)
    log.record(rec)
    log.record(build_record("scan", [], True))
    lines = (tmp_path / ".efc.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    data = json.loads(lines[0])
    assert set(data) == {"ts", "command", "dry_run", "result",
                         "duration_seconds", "tasks"}
    assert data["command"] == "clean" and data["result"] == "partial"
    assert data["tasks"][0]["name"] == "downloads"
    assert data["tasks"][0]["dir"] == "/t"
    files = data["tasks"][0]["files"]
    assert files[0] == {"path": "/t/a.tmp", "size": 3, "pattern": r"\.tmp$",
                        "status": "trashed"}
    assert files[1]["status"] == "trash_failed"
    assert json.loads(lines[1])["result"] == "dry_run"


def test_record_one_off_task_name_null(tmp_path: Path):
    log = ExecutionLog(tmp_path / ".efc.log")
    log.record(build_record("clean", [outcome([fo("trashed")])], False))
    data = json.loads((tmp_path / ".efc.log").read_text(encoding="utf-8"))
    assert data["tasks"][0]["name"] is None


def test_write_failure_warns_not_raises(tmp_path: Path, capsys):
    log = ExecutionLog(tmp_path)  # 目录不可作为日志文件 → OSError
    log.record(build_record("clean", [], False))
    captured = capsys.readouterr()
    assert "警告" in captured.err and "执行日志" in captured.err
