"""共享 fixtures（Spec §14）。"""


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
