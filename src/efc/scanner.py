"""扫描器（Spec §5）：正则编译 + 目录遍历 + 文件名模式匹配。

仅对文件名（而非整条路径）做 re.search；递归走 os.walk(followlinks=False)，
不跟随符号链接；exclude 目录（resolve+normcase）整棵跳过，exclude 文件
精确跳过（用于 backup_dir/log_file 自我保护）；无权限子目录静默跳过；
输出按 str(path) 排序保证确定性。
"""

import os
import re
from pathlib import Path

from efc.exceptions import PatternError, ScanError
from efc.models import FileMatch, ScanResult


def _norm(p: Path) -> str:
    return os.path.normcase(os.fspath(p.expanduser().resolve()))


def compile_patterns(patterns: list[str], ignore_case: bool) -> list[re.Pattern[str]]:
    """编译文件名正则；非法正则抛 PatternError（消息含模式原文与 re 错误）。"""
    flags = re.IGNORECASE if ignore_case else 0
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, flags))
        except re.error as e:
            raise PatternError(f"非法正则 {pattern!r}: {e}") from e
    return compiled


def _match_file(
    path: Path, root: Path, patterns: list[re.Pattern[str]]
) -> FileMatch | None:
    hit = next((c for c in patterns if c.search(path.name)), None)
    if hit is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return FileMatch(
        path=path,
        relative=path.relative_to(root).as_posix(),
        size=stat.st_size,
        mtime=stat.st_mtime,
        pattern=hit.pattern,
    )


def scan(
    root: Path,
    patterns: list[re.Pattern[str]],
    recursive: bool,
    exclude: list[Path] | None = None,
) -> ScanResult:
    """扫描 root 下文件名命中任一模式的文件（多模式 OR，记录首个命中模式）。

    目录不存在/不是目录 → ScanError；recursive=False 只看顶层。
    """
    root = root.expanduser()
    if not root.exists():
        raise ScanError(f"目标目录不存在: {root}")
    if not root.is_dir():
        raise ScanError(f"目标不是目录: {root}")
    excluded = {_norm(p) for p in (exclude or [])}
    matches: list[FileMatch] = []
    scanned_dirs = 0

    def take(path: Path) -> FileMatch | None:
        if excluded and _norm(path) in excluded:
            return None
        return _match_file(path, root, patterns)

    if recursive:
        for dirpath, dirnames, filenames in os.walk(
            root, followlinks=False, onerror=lambda e: None
        ):
            current = Path(dirpath)
            dirnames[:] = [
                d for d in dirnames if _norm(current / d) not in excluded
            ]
            scanned_dirs += 1
            for filename in filenames:
                match = take(current / filename)
                if match is not None:
                    matches.append(match)
    else:
        scanned_dirs = 1
        try:
            entries = list(root.iterdir())
        except PermissionError:
            entries = []
        for entry in entries:
            if not entry.is_file():
                continue
            match = take(entry)
            if match is not None:
                matches.append(match)

    matches.sort(key=lambda m: str(m.path))
    return ScanResult(root=root, recursive=recursive, matches=matches,
                      scanned_dirs=scanned_dirs)
