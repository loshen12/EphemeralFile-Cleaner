"""备份层（Spec §5/§7）：shutil.copy2 保留相对结构 + manifest.json 审计记录。

备份先于删除：cleaner 在分批入回收站前逐文件 backup_file；备份异常向上抛
（由 cleaner 捕获计为 backup_failed）。时间戳目录含毫秒，防同秒冲突。
"""

import json
import shutil
import time
from pathlib import Path
from typing import Any

from efc.models import FileOutcome


class BackupRun:
    """一次清理对应的备份目录（base_dir/<YYYYmmdd-HHMMSS.fff>/）。经 new_run() 构造。"""

    def __init__(self, base_dir: Path) -> None:
        now = time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + \
            f".{int((now % 1) * 1000):03d}"
        root = base_dir / stamp
        suffix = 1
        while root.exists():
            root = base_dir / f"{stamp}-{suffix}"
            suffix += 1
        root.mkdir(parents=True)
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def backup_file(self, src: Path, relative: str) -> Path:
        """copy2 备份到 root/relative（保留结构与 mtime）；异常上抛。"""
        dest = self._root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    def write_manifest(self, outcomes: list[FileOutcome], meta: dict[str, Any]) -> Path:
        """写 manifest.json（UTF-8/indent=2）；返回路径。"""
        data: dict[str, Any] = {
            "run_at": meta.get("run_at"),
            "target_dir": meta.get("target_dir"),
            "patterns": meta.get("patterns"),
            "recursive": meta.get("recursive"),
            "results": [
                {
                    "original": str(o.path),
                    "backup": str(o.backup_path) if o.backup_path is not None else None,
                    "status": o.status,
                    "size": o.size,
                    "error": o.error,
                }
                for o in outcomes
            ],
        }
        path = self._root / "manifest.json"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return path


def new_run(base_dir: Path) -> BackupRun:
    """工厂函数：不直接暴露 BackupRun.__init__。"""
    return BackupRun(base_dir)
