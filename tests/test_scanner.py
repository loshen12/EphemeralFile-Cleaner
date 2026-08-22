"""efc.scanner 测试：命中/不命中/多模式 OR/ignore_case/文件名匹配/递归/exclude/排序
/非法正则/目录不存在/首模式归属（Spec §14）。"""

import re

import pytest

from efc.exceptions import PatternError, ScanError
from efc.scanner import compile_patterns, scan


def names(result) -> list[str]:
    return [m.path.name for m in result.matches]


# ---------- compile_patterns ----------


def test_compile_patterns_basic():
    pats = compile_patterns([r"\.tmp$", r"^~\$"], ignore_case=False)
    assert len(pats) == 2
    assert pats[0].search("x.tmp") and not pats[0].search("X.TMP")


def test_compile_patterns_ignore_case():
    pats = compile_patterns([r"\.tmp$"], ignore_case=True)
    assert pats[0].search("Y.TMP")


def test_compile_patterns_invalid_original_in_message():
    with pytest.raises(PatternError, match=re.escape("(")):
        compile_patterns(["("], ignore_case=False)


# ---------- 命中与不命中 ----------


def test_scan_top_level_hit_and_miss(tree):
    result = scan(tree, compile_patterns([r"\.tmp$"], False), recursive=False)
    assert names(result) == ["x.tmp"]  # keep.txt、~$a.docx 不命中
    assert result.scanned_dirs == 1
    assert result.root == tree and result.recursive is False


def test_scan_no_match_returns_empty(tree):
    result = scan(tree, compile_patterns([r"\.xyz$"], True), recursive=True)
    assert result.matches == [] and result.scanned_dirs == 3


# ---------- 递归开关 ----------


def test_scan_recursive_toggle(tree):
    flat = scan(tree, compile_patterns([r"\.tmp$"], True), recursive=False)
    deep = scan(tree, compile_patterns([r"\.tmp$"], True), recursive=True)
    assert set(names(flat)) == {"x.tmp"}
    assert set(names(deep)) == {"x.tmp", "y.TMP", "z.tmp"}
    assert deep.scanned_dirs == 3  # tree、b、b/c


# ---------- 多模式 OR 与首命中归属 ----------


def test_scan_multi_pattern_or(tree):
    pats = compile_patterns([r"\.tmp$", r"^~\$"], ignore_case=True)
    result = scan(tree, pats, recursive=True)
    assert set(names(result)) == {"x.tmp", "y.TMP", "z.tmp", "~$a.docx", "~$b.docx"}


def test_scan_first_pattern_attribution(tree):
    pats = compile_patterns([r"\.tmp$", r"^x"], ignore_case=False)
    result = scan(tree, pats, recursive=False)
    xtmp = next(m for m in result.matches if m.path.name == "x.tmp")
    assert xtmp.pattern == r"\.tmp$"  # 同时命中两个模式，归属第一个


# ---------- ignore_case ----------


def test_scan_ignore_case_difference(tree):
    sensitive = scan(tree, compile_patterns([r"\.tmp$"], False), recursive=True)
    insensitive = scan(tree, compile_patterns([r"\.tmp$"], True), recursive=True)
    assert "y.TMP" not in names(sensitive)
    assert "y.TMP" in names(insensitive)


# ---------- 匹配文件名而非路径 ----------


def test_scan_matches_filename_not_path(tree):
    (tree / "sub.tmpdir").mkdir()
    (tree / "sub.tmpdir" / "n.txt").write_text("n")
    (tree / "dir.tmp").mkdir()  # 目录名命中也不算文件
    result = scan(tree, compile_patterns([r"\.tmp$"], True), recursive=True)
    found = names(result)
    assert "n.txt" not in found  # 不因所在目录名含 .tmpdir 而命中
    assert "dir.tmp" not in found  # 目录不参与匹配
    assert set(found) == {"x.tmp", "y.TMP", "z.tmp"}


# ---------- exclude ----------


def test_scan_exclude_dir_prunes_subtree(tree):
    result = scan(tree, compile_patterns([r"\.tmp$", r"^~\$"], True),
                  recursive=True, exclude=[tree / "b"])
    assert set(names(result)) == {"x.tmp", "~$a.docx"}
    assert result.scanned_dirs == 1  # b 整棵剪枝


def test_scan_exclude_exact_file(tree):
    result = scan(tree, compile_patterns([r"\.tmp$", r"^~\$"], True),
                  recursive=False, exclude=[tree / "x.tmp"])
    assert names(result) == ["~$a.docx"]


# ---------- 排序与元数据 ----------


def test_scan_sorted_by_str_path(tree):
    result = scan(tree, compile_patterns([r"\.tmp$", r"^~\$"], True), recursive=True)
    keys = [str(m.path) for m in result.matches]
    assert keys == sorted(keys)
    relatives = [m.relative for m in result.matches]
    assert relatives == sorted(relatives)
    assert all("\\" not in r for r in relatives)  # relative 用 / 分隔


def test_filematch_metadata(tree):
    result = scan(tree, compile_patterns([r"\.tmp$"], False), recursive=False)
    m = result.matches[0]
    assert m.path == tree / "x.tmp"
    assert m.relative == "x.tmp"
    assert m.size == 3  # "xxx"
    assert m.mtime == pytest.approx((tree / "x.tmp").stat().st_mtime)


def test_scan_relative_posix_nested(tree):
    result = scan(tree, compile_patterns([r"\.tmp$"], True), recursive=True)
    z = next(m for m in result.matches if m.path.name == "z.tmp")
    assert z.relative == "b/c/z.tmp"


# ---------- 错误路径 ----------


def test_scan_missing_dir_raises(tree):
    with pytest.raises(ScanError, match="不存在"):
        scan(tree / "nope", compile_patterns([r"\.tmp$"], False), False)


def test_scan_file_as_root_raises(tree):
    with pytest.raises(ScanError, match="不是目录"):
        scan(tree / "x.tmp", compile_patterns([r"\.tmp$"], False), False)
