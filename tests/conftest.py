"""共享 fixtures（Spec §14）：tree 目录树、fake_trash、fake_ui。"""

import os
from pathlib import Path

import pytest


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """标准目录树：{~$a.docx, keep.txt, x.tmp} + b/{y.TMP, ~$b.docx} + b/c/{z.tmp}。"""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "~$a.docx").write_text("a")
    (root / "keep.txt").write_text("keep")
    (root / "x.tmp").write_text("xxx")
    b = root / "b"
    b.mkdir()
    (b / "y.TMP").write_text("yy")
    (b / "~$b.docx").write_text("b")
    c = b / "c"
    c.mkdir()
    (c / "z.tmp").write_text("z")
    return root


class FakeTrash:
    """记录调用路径的可编程 trash；fail_names 命中即抛。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_names: set[str] = set()

    def __call__(self, path: str) -> None:
        if Path(path).name in self.fail_names:
            raise OSError(f"trash failed: {path}")
        self.calls.append(path)


class FakeUI:
    """可编程 confirm 序列；序列耗尽后 confirm/confirm_next_batch 返回 False（安全默认）。

    high_risk_path 设置后按 normcase 精确匹配放行，否则高危恒拒。
    """

    def __init__(self, confirms: list[bool] | None = None,
                 next_batches: list[bool] | None = None,
                 high_risk_path: Path | None = None) -> None:
        self.confirms = list(confirms or [])
        self.next_batches = list(next_batches or [])
        self.high_risk_path = high_risk_path
        self.confirm_messages: list[str] = []
        self.batch_calls: list[tuple[int, int]] = []
        self.high_risk_calls: list[tuple[Path, str]] = []
        self.match_results: list[object] = []
        self.summaries: list[object] = []
        self.errors: list[str] = []

    def confirm(self, message: str) -> bool:
        self.confirm_messages.append(message)
        return self.confirms.pop(0) if self.confirms else False

    def confirm_high_risk(self, path: Path, reason: str) -> bool:
        self.high_risk_calls.append((path, reason))
        if self.high_risk_path is None:
            return False
        return os.path.normcase(str(path)) == os.path.normcase(str(self.high_risk_path))

    def confirm_next_batch(self, done: int, total: int) -> bool:
        self.batch_calls.append((done, total))
        return self.next_batches.pop(0) if self.next_batches else False

    def show_matches(self, result) -> None:
        self.match_results.append(result)

    def show_summary(self, outcome) -> None:
        self.summaries.append(outcome)

    def error(self, message: str) -> None:
        self.errors.append(message)


@pytest.fixture
def fake_trash() -> FakeTrash:
    return FakeTrash()


@pytest.fixture
def fake_ui() -> FakeUI:
    return FakeUI()

