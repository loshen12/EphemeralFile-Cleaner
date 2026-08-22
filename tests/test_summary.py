"""efc.summary 测试（Spec §14）：聚合（同 dir 合并/None 归"(无模式)"/只计 trashed）、
format_bytes/format_duration、render 分节与空输入。"""

from pathlib import Path

from efc.models import CleanOutcome, FileOutcome
from efc.summary import (
    NO_PATTERN,
    build_summary,
    format_bytes,
    format_duration,
    render_summary,
)


def fo(status: str, size: int = 1, pattern: str | None = r"\.tmp$") -> FileOutcome:
    return FileOutcome(path=Path("/t/a.tmp"), status=status, size=size, pattern=pattern)


def outcome(dir_: str, results: list[FileOutcome], *, name: str | None = None,
            aborted: bool = False, duration: float = 1.0) -> CleanOutcome:
    return CleanOutcome(total_matched=len(results), results=results, batches=1,
                        backup_dir=None, aborted=aborted, task_name=name,
                        target_dir=Path(dir_), duration_seconds=duration)


# ---------- 聚合 ----------


def test_merge_same_dir_and_first_name():
    s = build_summary([
        outcome("/t", [fo("trashed", 3)], name="a"),
        outcome("/t", [fo("trashed", 4)], name="b"),  # 同目录合并，name 取首个
    ])
    assert len(s.tasks) == 1
    assert s.tasks[0].name == "a" and s.tasks[0].files == 2
    assert s.tasks[0].bytes == 7


def test_only_trashed_counted():
    s = build_summary([
        outcome("/t", [fo("trashed", 3), fo("backup_failed", 100), fo("trash_failed", 200)]),
    ])
    assert s.total_files == 1 and s.total_bytes == 3
    assert s.failed_files == 2


def test_none_pattern_grouped():
    s = build_summary([outcome("/t", [fo("trashed", 1, None)])])
    assert s.tasks[0].by_pattern[0].pattern == NO_PATTERN


def test_by_pattern_keeps_first_seen_order():
    s = build_summary([
        outcome("/t", [fo("trashed", 1, r"\.a$"), fo("trashed", 1, r"\.b$"),
                       fo("trashed", 1, r"\.a$")]),
    ])
    assert [p.pattern for p in s.tasks[0].by_pattern] == [r"\.a$", r"\.b$"]
    assert s.tasks[0].by_pattern[0].files == 2


def test_totals_and_duration():
    s = build_summary([
        outcome("/t", [fo("trashed", 3)], duration=90),
        outcome("/u", [fo("trashed", 4)], duration=30),
    ])
    assert s.total_files == 2 and s.total_bytes == 7
    assert s.duration_seconds == 120


# ---------- format_bytes / format_duration ----------


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1536) == "1.50 KB"
    assert format_bytes(5 * 1024 * 1024) == "5.00 MB"
    assert format_bytes(3 * 1024 ** 3) == "3.00 GB"


def test_format_duration():
    assert format_duration(0) == "0 秒"
    assert format_duration(59.9) == "59 秒"
    assert format_duration(60) == "1.0 min"
    assert format_duration(90) == "1.5 min"


# ---------- render ----------


def test_render_multi_task_sections():
    s = build_summary([
        outcome("/t", [fo("trashed", 3, r"\.tmp$")], name="downloads"),
        outcome("/u", [fo("trashed", 4, r"\.tmp$")]),
    ])
    text = render_summary(s)
    assert "本次对 /t、/u 等 2 条路径完成文件清理，合计清理 2 个文件" in text
    assert "一、本次 /t（downloads）完成清理 1 个文件" in text
    assert "二、本次 /u 完成清理 1 个文件" in text  # 一次性任务无括号后缀
    assert '"\\.tmp$"模式：清理 1 个文件' in text


def test_render_single_task_no_path_count():
    s = build_summary([outcome("/t", [fo("trashed", 3)], name="a")])
    text = render_summary(s)
    assert "等 1 条路径" not in text
    assert "（a）" in text


def test_render_failure_tail_line():
    s = build_summary([outcome("/t", [fo("trashed", 3), fo("trash_failed", 9)])])
    assert render_summary(s).endswith("另有 1 个文件清理失败（详见执行日志）")


def test_render_all_zero_single_line():
    s = build_summary([outcome("/t", [fo("backup_failed", 9)])])
    assert render_summary(s) == "本次未清理任何文件。"


def test_render_empty_input():
    assert render_summary(build_summary([])) == ""
