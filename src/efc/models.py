"""数据模型（Spec §3）。

FileMatch / ScanResult 由 scanner 产出；RiskDecision 由 safety.assess_risk 产出；
FileOutcome / CleanOutcome 由 cleaner 流水线产出，供 summary / journal / cli 消费。
"""

from dataclasses import dataclass
from pathlib import Path

# FileOutcome.status 取值
STATUS_TRASHED = "trashed"
STATUS_BACKUP_FAILED = "backup_failed"
STATUS_TRASH_FAILED = "trash_failed"


@dataclass
class FileMatch:
    """扫描命中的单个文件。"""

    path: Path
    relative: str
    size: int
    mtime: float
    pattern: str  # 命中的第一个模式（按模式汇总唯一归属）


@dataclass
class ScanResult:
    """一次扫描的结果；matches 按 str(path) 排序（确定性）。"""

    root: Path
    recursive: bool
    matches: list[FileMatch]
    scanned_dirs: int


@dataclass
class RiskDecision:
    """高危评估结论；high_risk 时 reason 给人话原因。"""

    high_risk: bool
    reason: str | None = None


@dataclass
class FileOutcome:
    """单文件清理结局。"""

    path: Path
    status: str  # STATUS_TRASHED | STATUS_BACKUP_FAILED | STATUS_TRASH_FAILED
    backup_path: Path | None = None
    error: str | None = None
    size: int = 0
    pattern: str | None = None


@dataclass
class CleanOutcome:
    """单任务清理结局；一次性任务 task_name 为 None。"""

    total_matched: int
    results: list[FileOutcome]
    batches: int
    backup_dir: Path | None
    aborted: bool = False
    task_name: str | None = None
    target_dir: Path | None = None
    duration_seconds: float = 0.0
    total_bytes: int = 0  # 仅 trashed 合计

    @property
    def trashed(self) -> list[FileOutcome]:
        """成功移入回收站的文件。"""
        return [r for r in self.results if r.status == STATUS_TRASHED]

    @property
    def failed(self) -> list[FileOutcome]:
        """备份失败与回收站失败的文件。"""
        return [
            r for r in self.results if r.status in (STATUS_BACKUP_FAILED, STATUS_TRASH_FAILED)
        ]
