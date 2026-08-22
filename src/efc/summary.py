"""清理总结（Spec §3/§9）：纯函数聚合与渲染，不依赖 rich。

build_summary 只统计 trashed；同一目标目录（normcase）合并、name 取首个；
pattern=None 归入 "(无模式)"；by_pattern 保持命中顺序。失败文件数单列，
由 render_summary 末行提示。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from efc.models import CleanOutcome

NO_PATTERN = "(无模式)"
_NUMERALS = "一二三四五六七八九十"


@dataclass
class PatternStats:
    pattern: str
    files: int
    bytes: int


@dataclass
class TaskStats:
    name: str | None  # None=一次性任务
    dir: Path
    files: int
    bytes: int
    by_pattern: list[PatternStats] = field(default_factory=list)  # 保持命中顺序


@dataclass
class RunSummary:
    tasks: list[TaskStats]
    total_files: int
    total_bytes: int
    duration_seconds: float
    failed_files: int


def build_summary(outcomes: list[CleanOutcome]) -> RunSummary:
    """聚合多任务 CleanOutcome；目录键为 normcase(str(target_dir))。"""
    order: list[str] = []
    stats: dict[str, TaskStats] = {}
    failed = 0
    for outcome in outcomes:
        failed += len(outcome.failed)
        if outcome.target_dir is None:
            continue
        key = os.path.normcase(str(outcome.target_dir))
        if key not in stats:
            order.append(key)
            stats[key] = TaskStats(name=outcome.task_name, dir=outcome.target_dir,
                                   files=0, bytes=0)
        task = stats[key]
        for fo in outcome.trashed:
            task.files += 1
            task.bytes += fo.size
            pattern = fo.pattern if fo.pattern is not None else NO_PATTERN
            ps = next((p for p in task.by_pattern if p.pattern == pattern), None)
            if ps is None:
                ps = PatternStats(pattern=pattern, files=0, bytes=0)
                task.by_pattern.append(ps)
            ps.files += 1
            ps.bytes += fo.size
    tasks = [stats[k] for k in order]
    return RunSummary(
        tasks=tasks,
        total_files=sum(t.files for t in tasks),
        total_bytes=sum(t.bytes for t in tasks),
        duration_seconds=sum(o.duration_seconds for o in outcomes),
        failed_files=failed,
    )


def format_bytes(n: int) -> str:
    """B/KB/MB/GB 自适应，2 位小数（B 取整）。"""
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.2f} {unit}"
    return f"{value:.2f} GB"


def format_duration(sec: float) -> str:
    """<60s → 'N 秒'（取整），否则 'N.N min'。"""
    if sec < 60:
        return f"{int(sec)} 秒"
    return f"{sec / 60:.1f} min"


def render_summary(s: RunSummary) -> str:
    """§9 中文总结；tasks 为空返回空串，全部未清理仅一行提示。"""
    if not s.tasks:
        return ""
    if s.total_files == 0:
        return "本次未清理任何文件。"
    dirs_display = "、".join(str(t.dir) for t in s.tasks)
    scope = f"对 {dirs_display}"
    if len(s.tasks) > 1:
        scope += f" 等 {len(s.tasks)} 条路径"
    lines = [
        f"本次{scope}完成文件清理，合计清理 {s.total_files} 个文件，"
        f"合计大小 {format_bytes(s.total_bytes)}，"
        f"合计用时 {format_duration(s.duration_seconds)}，具体为"
    ]
    for i, t in enumerate(s.tasks, 1):
        label = _NUMERALS[i - 1] if i <= len(_NUMERALS) else str(i)
        name_part = f"（{t.name}）" if t.name else ""
        lines.append(
            f"{label}、本次 {t.dir}{name_part}完成清理 {t.files} 个文件，"
            f"合计大小 {format_bytes(t.bytes)}，具体为："
        )
        for j, p in enumerate(t.by_pattern, 1):
            lines.append(
                f"{j}. \"{p.pattern}\"模式：清理 {p.files} 个文件，"
                f"合计大小 {format_bytes(p.bytes)}；"
            )
    if s.failed_files:
        lines.append(f"另有 {s.failed_files} 个文件清理失败（详见执行日志）")
    return "\n".join(lines)
