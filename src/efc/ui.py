"""UI 协议（Spec §5）：ConsoleUI（typer/rich 交互）/ AutoUI（自动确认，高危拒）。

业务层（cleaner）只依赖 UI 协议；渲染细节收敛在此层。
高危确认要求逐字符输入 normcase 归一后的完整路径，一次不匹配即 False；
--yes/--non-interactive 场景注入 AutoUI 或 ConsoleUI(interactive=False)：
普通确认自动通过、高危恒拒（不能绕过高危红线）。
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import typer
from rich.console import Console
from rich.table import Table

from efc.models import CleanOutcome, ScanResult


class UI(Protocol):
    """清理流水线依赖的 UI 协议。"""

    def confirm(self, message: str) -> bool: ...

    def confirm_high_risk(self, path: Path, reason: str) -> bool: ...

    def confirm_next_batch(self, done: int, total: int) -> bool: ...

    def show_matches(self, result: ScanResult) -> None: ...

    def show_summary(self, outcome: CleanOutcome) -> None: ...

    def error(self, message: str) -> None: ...


class AutoUI:
    """--yes/测试用：confirm/confirm_next_batch 恒 True；confirm_high_risk 恒 False。"""

    def confirm(self, message: str) -> bool:
        return True

    def confirm_high_risk(self, path: Path, reason: str) -> bool:
        return False

    def confirm_next_batch(self, done: int, total: int) -> bool:
        return True

    def show_matches(self, result: ScanResult) -> None:
        pass  # 无人类阅读者，静默

    def show_summary(self, outcome: CleanOutcome) -> None:
        pass

    def error(self, message: str) -> None:
        pass


class ConsoleUI:
    """typer.confirm/rich 渲染；开关 interactive/no_color/progress。

    input_fn 与 console 为依赖注入点（测试用），生产默认 input 与本机 Console。
    """

    def __init__(
        self,
        *,
        interactive: bool = True,
        no_color: bool = False,
        progress: bool = True,
        input_fn: Callable[[str], str] | None = None,
        console: Console | None = None,
    ) -> None:
        self.interactive = interactive
        self.no_color = no_color
        self.progress = progress
        self._input: Callable[[str], str] = input_fn if input_fn is not None else input
        self._console = console if console is not None else Console(no_color=no_color)

    def confirm(self, message: str) -> bool:
        if not self.interactive:
            return True
        return typer.confirm(message, default=False)

    def confirm_high_risk(self, path: Path, reason: str) -> bool:
        if not self.interactive:
            return False
        answer = self._input(
            f"高危目标: {path}\n原因: {reason}\n请输入完整路径 {path} 以确认继续: "
        )
        return os.path.normcase(answer.strip()) == os.path.normcase(os.fspath(path))

    def confirm_next_batch(self, done: int, total: int) -> bool:
        if not self.interactive:
            return True
        return typer.confirm(f"已完成 {done}/{total} 个文件，继续下一批?", default=True)

    def show_matches(self, result: ScanResult) -> None:
        self._console.print(
            f"扫描 {result.root}（recursive={result.recursive}）："
            f"命中 {len(result.matches)} 个文件，共遍历 {result.scanned_dirs} 个目录"
        )
        if not result.matches:
            return
        table = Table(show_header=True)
        table.add_column("文件")
        table.add_column("大小", justify="right")
        table.add_column("命中模式")
        for m in result.matches:
            table.add_row(str(m.path), str(m.size), m.pattern)
        self._console.print(table)

    def show_summary(self, outcome: CleanOutcome) -> None:
        task = f"任务 {outcome.task_name}：" if outcome.task_name else "一次性任务："
        lines = [
            f"{task}移入回收站 {len(outcome.trashed)} 个文件"
            f"（匹配 {len(outcome.results)} 个，分 {outcome.batches} 批执行）"
        ]
        if outcome.failed:
            lines.append(
                f"另有 {len(outcome.failed)} 个文件清理失败（详见 manifest 与执行日志）"
            )
        if outcome.backup_dir is not None:
            lines.append(f"备份目录: {outcome.backup_dir}")
        if outcome.aborted:
            lines.append("本次执行被中止（已处理文件不回滚）")
        self._console.print("\n".join(lines))

    def error(self, message: str) -> None:
        print(f"错误: {message}", file=sys.stderr)
