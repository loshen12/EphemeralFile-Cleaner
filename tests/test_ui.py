"""efc.ui 测试：AutoUI 语义、ConsoleUI 高危确认/无彩色/非交互（T012 验收）。"""

import io
import os
from pathlib import Path

from rich.console import Console

from efc.models import CleanOutcome, FileOutcome, ScanResult
from efc.ui import AutoUI, ConsoleUI


def scan_result(root: Path) -> ScanResult:
    return ScanResult(
        root=root,
        recursive=False,
        matches=[],
        scanned_dirs=1,
    )


def clean_outcome() -> CleanOutcome:
    return CleanOutcome(total_matched=0, results=[], batches=0, backup_dir=None)


# ---------- AutoUI ----------


def test_auto_ui_confirms_but_rejects_high_risk():
    ui = AutoUI()
    assert ui.confirm("继续?") is True
    assert ui.confirm_next_batch(5, 13) is True
    assert ui.confirm_high_risk(Path("/"), "卷根") is False


def test_auto_ui_render_methods_are_noops(capsys):
    ui = AutoUI()
    ui.show_matches(scan_result(Path("/tmp")))
    ui.show_summary(clean_outcome())
    ui.error("boom")
    assert capsys.readouterr().out == ""  # Agent 模式静默，人读输出由 cli 层路由


# ---------- ConsoleUI：高危确认 ----------


def test_high_risk_confirm_correct_path(tmp_path):
    ui = ConsoleUI(input_fn=lambda prompt: str(tmp_path))
    assert ui.confirm_high_risk(tmp_path, "测试") is True


def test_high_risk_confirm_wrong_path(tmp_path):
    ui = ConsoleUI(input_fn=lambda prompt: str(tmp_path / "oops"))
    assert ui.confirm_high_risk(tmp_path, "测试") is False


def test_high_risk_confirm_strips_whitespace(tmp_path):
    ui = ConsoleUI(input_fn=lambda prompt: f"  {tmp_path} \n")
    assert ui.confirm_high_risk(tmp_path, "测试") is True


def test_high_risk_confirm_normcase_folded(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())  # 模拟 Windows 折叠
    target = tmp_path / "Data"
    ui = ConsoleUI(input_fn=lambda prompt: str(target).lower())
    assert ui.confirm_high_risk(target, "测试") is True


# ---------- ConsoleUI：非交互 ----------


def test_non_interactive_auto_confirms_but_rejects_high_risk():
    called = []
    ui = ConsoleUI(interactive=False, input_fn=lambda p: called.append(p) or "x")
    assert ui.confirm("继续?") is True
    assert ui.confirm_next_batch(5, 13) is True
    assert ui.confirm_high_risk(Path("/"), "卷根") is False
    assert called == []  # 全程无 input() 调用


# ---------- ConsoleUI：渲染 ----------


def test_no_color_renders_without_ansi(tmp_path):
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, width=200)
    ui = ConsoleUI(no_color=True, progress=False, console=console)
    result = ScanResult(
        root=tmp_path,
        recursive=True,
        matches=[],
        scanned_dirs=2,
    )
    ui.show_matches(result)
    ui.show_summary(
        CleanOutcome(total_matched=0, results=[], batches=0, backup_dir=None)
    )
    out = buf.getvalue()
    assert "\x1b[" not in out  # 不触发彩色控制序列
    assert "命中 0 个文件" in out


def test_show_matches_lists_files(tmp_path):
    from efc.models import FileMatch

    buf = io.StringIO()
    console = Console(file=buf, no_color=True, width=200)
    ui = ConsoleUI(console=console)
    result = ScanResult(
        root=tmp_path,
        recursive=False,
        matches=[
            FileMatch(path=tmp_path / "x.tmp", relative="x.tmp", size=3,
                      mtime=1.0, pattern=r"\.tmp$"),
        ],
        scanned_dirs=1,
    )
    ui.show_matches(result)
    out = buf.getvalue()
    assert "x.tmp" in out and r"\.tmp$" in out


def test_show_summary_reports_failure_and_abort(tmp_path):
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, width=200)
    ui = ConsoleUI(console=console)
    outcomes = [
        FileOutcome(path=tmp_path / "a.tmp", status="trashed", size=1),
        FileOutcome(path=tmp_path / "b.tmp", status="trash_failed", error="x", size=1),
    ]
    ui.show_summary(
        CleanOutcome(
            total_matched=2, results=outcomes, batches=1,
            backup_dir=tmp_path / "bk", aborted=True, task_name="downloads",
        )
    )
    out = buf.getvalue()
    assert "任务 downloads" in out
    assert "移入回收站 1 个文件" in out
    assert "1 个文件清理失败" in out
    assert "被中止" in out
    assert str(tmp_path / "bk") in out


def test_error_writes_stderr_line(capsys):
    ui = ConsoleUI()
    ui.error("目标目录不存在")
    captured = capsys.readouterr()
    assert captured.err.strip() == "错误: 目标目录不存在"
    assert captured.out == ""
