"""efc.backup 测试：保留相对结构/copy2 保留 mtime/manifest 字段/多批次
/异常上抛/同戳冲突（Spec §14，T010 验收）。"""

import json
import os
import time
from pathlib import Path

import pytest

from efc.backup import new_run
from efc.models import FileOutcome


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src"
    (src / "b" / "c").mkdir(parents=True)
    (src / "x.tmp").write_text("xxx")
    (src / "b" / "c" / "z.tmp").write_text("z")
    return tmp_path / "bk", src


def test_new_run_creates_timestamp_dir(workspace):
    base, _ = workspace
    run = new_run(base)
    assert run.root.is_dir() and run.root.parent == base
    assert len(run.root.name.split(".")[-1]) == 3  # <YYYYmmdd-HHMMSS>.<fff>
    assert 0 <= int(run.root.name.split(".")[-1]) < 1000


def test_backup_preserves_relative_structure(workspace):
    base, src = workspace
    run = new_run(base)
    dest1 = run.backup_file(src / "x.tmp", "x.tmp")
    dest2 = run.backup_file(src / "b" / "c" / "z.tmp", "b/c/z.tmp")
    assert dest1 == run.root / "x.tmp"
    assert dest2 == run.root / "b" / "c" / "z.tmp"
    assert dest1.read_text() == "xxx" and dest2.read_text() == "z"


def test_copy2_preserves_mtime(workspace):
    base, src = workspace
    src_file = src / "x.tmp"
    os.utime(src_file, (1000000000, 1000000000))
    run = new_run(base)
    dest = run.backup_file(src_file, "x.tmp")
    assert dest.stat().st_mtime == 1000000000


def test_manifest_fields_complete(workspace):
    base, src = workspace
    run = new_run(base)
    src_file = src / "x.tmp"
    dest = run.backup_file(src_file, "x.tmp")
    outcomes = [
        FileOutcome(path=src_file, status="trashed", backup_path=dest, size=3,
                    pattern=r"\.tmp$"),
        FileOutcome(path=src / "b" / "c" / "z.tmp", status="trash_failed",
                    error="no gvfs", size=1, pattern=r"\.tmp$"),
    ]
    meta = {"run_at": "2026-08-23T10:00:00", "target_dir": str(src),
            "patterns": [r"\.tmp$"], "recursive": True}
    path = run.write_manifest(outcomes, meta)
    assert path == run.root / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"run_at", "target_dir", "patterns", "recursive", "results"}
    assert data["run_at"] == "2026-08-23T10:00:00"
    ok, failed = data["results"]
    assert ok == {"original": str(src_file), "backup": str(dest),
                  "status": "trashed", "size": 3, "error": None}
    assert failed["backup"] is None and failed["error"] == "no gvfs"
    assert failed["status"] == "trash_failed"


def test_multiple_batches_accumulate(workspace):
    base, src = workspace
    run = new_run(base)
    run.backup_file(src / "x.tmp", "x.tmp")
    run.backup_file(src / "b" / "c" / "z.tmp", "b/c/z.tmp")
    extra = src / "y.log"
    extra.write_text("yy")
    run.backup_file(extra, "y.log")  # 第二批继续写入同一 run
    entries = sorted(str(p.relative_to(run.root)) for p in run.root.rglob("*"))
    assert entries == ["b", "b/c", "b/c/z.tmp", "x.tmp", "y.log"]


def test_backup_missing_src_propagates(workspace):
    base, src = workspace
    run = new_run(base)
    with pytest.raises(OSError):
        run.backup_file(src / "nope", "nope")


def test_same_stamp_collision_gets_suffix(workspace, monkeypatch):
    base, _ = workspace
    real_localtime = time.localtime
    monkeypatch.setattr(time, "time", lambda: 1234567890.5)
    monkeypatch.setattr(time, "localtime", lambda t: real_localtime(1234567890))
    run_a = new_run(base)
    run_b = new_run(base)
    assert run_a.root != run_b.root
    assert run_b.root.name == run_a.root.name + "-1"
