"""执行日志（Spec §3）：JSONL 追加写入，失败仅警告不抛异常。"""

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from efc.models import CleanOutcome


@dataclass
class JFile:
    path: str
    size: int
    pattern: str | None
    status: str


@dataclass
class JTarget:
    name: str | None  # None=一次性任务
    dir: str
    files: list[JFile] = field(default_factory=list)


@dataclass
class JournalRecord:
    ts: str
    command: str
    dry_run: bool
    result: str  # completed | partial | aborted | dry_run
    duration_seconds: float
    tasks: list[JTarget] = field(default_factory=list)


def judge_result(outcomes: list[CleanOutcome], dry_run: bool) -> str:
    """跨任务汇总判定：任一 aborted → aborted；否则有失败文件 → partial；
    否则 dry_run → dry_run；否则 completed。"""
    if any(o.aborted for o in outcomes):
        return "aborted"
    if any(o.failed for o in outcomes):
        return "partial"
    if dry_run:
        return "dry_run"
    return "completed"


def build_record(command: str, outcomes: list[CleanOutcome],
                 dry_run: bool) -> JournalRecord:
    """由 CleanOutcome 列表组装一条执行记录（cli/repl 共用）。"""
    return JournalRecord(
        ts=datetime.now().isoformat(timespec="seconds"),
        command=command,
        dry_run=dry_run,
        result=judge_result(outcomes, dry_run),
        duration_seconds=sum(o.duration_seconds for o in outcomes),
        tasks=[
            JTarget(
                name=o.task_name,
                dir=str(o.target_dir),
                files=[
                    JFile(path=str(f.path), size=f.size, pattern=f.pattern,
                          status=f.status)
                    for f in o.results
                ],
            )
            for o in outcomes
        ],
    )


class ExecutionLog:
    """追加单行 JSON（UTF-8/ensure_ascii=False）；写失败仅 stderr 警告。"""

    def __init__(self, path: Path) -> None:
        self._path = path.expanduser()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, rec: JournalRecord) -> None:
        try:
            line = json.dumps(dataclasses.asdict(rec), ensure_ascii=False)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"警告: 执行日志写入失败（{e}）", file=sys.stderr)
