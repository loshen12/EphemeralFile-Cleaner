"""efc.cleaner 测试（Spec §14）：fake trash 收绝对路径串、备份失败未送 trash、
trash 失败继续、confirm 拒绝 aborted、高危+AutoUI AbortError、13 文件 3 批、
批间拒绝、0 命中、UNC·不支持平台、size/pattern/task_name 携带、空间不足、
排除 backup_dir/log_file、dry_run 零 trash。"""

import os
import sys
from pathlib import Path

import pytest

import efc.backup
import efc.cleaner as cleaner_mod
from efc.cleaner import Cleaner
from efc.config import AppConfig
from efc.exceptions import AbortError, PlatformError
from efc.models import STATUS_BACKUP_FAILED, STATUS_TRASH_FAILED
from efc.ui import AutoUI


def make_tree(tmp_path: Path, n: int = 13) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    for i in range(n):
        (root / f"f{i}.tmp").write_text("x" * (i + 1))
    return root


def cfg_for(target: Path, patterns: list[str], **kw) -> AppConfig:
    return AppConfig(target_dir=target, filename_patterns=patterns, **kw)


def test_happy_path_trashes_absolute_paths(tree, fake_ui, fake_trash):
    fake_ui.confirms = [True]
    cfg = cfg_for(tree, [r"\.tmp$", r"^~\$"], recursive=True,
                  backup_dir=tree.parent / "bk")
    outcome = Cleaner(cfg, fake_ui, fake_trash, task_name="demo").run()
    assert outcome.total_matched == 5  # x.tmp/y.TMP/z.tmp/~$a/~$b
    assert len(fake_trash.calls) == len(outcome.trashed) == 5
    assert all(Path(c).is_absolute() for c in fake_trash.calls)
    assert outcome.batches == 1 and outcome.aborted is False
    assert outcome.task_name == "demo" and outcome.target_dir == tree.resolve()
    assert all(o.pattern in (r"\.tmp$", r"^~\$") for o in outcome.results)
    assert all(o.size >= 1 for o in outcome.results)
    assert outcome.total_bytes == sum(o.size for o in outcome.trashed)
    assert outcome.backup_dir is not None
    assert (outcome.backup_dir / "manifest.json").is_file()
    assert fake_ui.match_results and fake_ui.summaries


def test_thirteen_files_three_batches(tmp_path, fake_ui, fake_trash):
    fake_ui.confirms = [True]
    fake_ui.next_batches = [True, True]
    root = make_tree(tmp_path, 13)
    cfg = cfg_for(root, [r"\.tmp$"], max_batch=5)
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()
    assert outcome.batches == 3
    assert len(fake_trash.calls) == 13
    assert [c for c, _ in fake_ui.batch_calls] == [5, 10]  # 批间确认在 5、10 后


def test_backup_failure_skips_trash(tree, fake_ui, fake_trash, monkeypatch):
    fake_ui.confirms = [True]
    real = efc.backup.BackupRun.backup_file

    def failing_backup(self, src, relative):
        if src.name == "x.tmp":
            raise OSError("disk error")
        return real(self, src, relative)

    monkeypatch.setattr(efc.backup.BackupRun, "backup_file", failing_backup)
    cfg = cfg_for(tree, [r"\.tmp$"], backup_dir=tree.parent / "bk")
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()
    failed = outcome.failed
    assert [f.path.name for f in failed] == ["x.tmp"]
    assert failed[0].status == STATUS_BACKUP_FAILED and failed[0].error
    assert "x.tmp" not in [Path(c).name for c in fake_trash.calls]  # 未送 trash
    assert len(fake_trash.calls) == len(outcome.trashed)


def test_trash_failure_continues(tree, fake_ui, fake_trash):
    fake_ui.confirms = [True]
    fake_trash.fail_names = {"y.TMP", "z.tmp"}
    cfg = cfg_for(tree, [r"\.tmp$"], recursive=True, ignore_case=True)
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()
    assert [f.status for f in outcome.failed] == [STATUS_TRASH_FAILED] * 2
    assert len(fake_trash.calls) == 1  # x.tmp 成功
    assert len(outcome.trashed) == 1
    assert outcome.failed[0].backup_path is not None  # 备份仍保留


def test_confirm_refusal_aborts_zero_trash(tree, fake_ui, fake_trash):
    cfg = cfg_for(tree, [r"\.tmp$"])
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()  # fake_ui 默认拒绝
    assert outcome.aborted is True and outcome.batches == 0
    assert fake_trash.calls == []
    assert outcome.total_matched == 1


def test_high_risk_with_auto_ui_raises_abort(tree, fake_trash):
    cfg = cfg_for(tree, [r"\.tmp$"], high_risk_dirs=[tree])
    with pytest.raises(AbortError):
        Cleaner(cfg, AutoUI(), fake_trash).run()
    assert fake_trash.calls == []  # 零删除


def test_high_risk_confirmed_path_proceeds(tree, fake_ui, fake_trash):
    fake_ui.high_risk_path = tree  # 可编程期望路径：normcase 精确匹配放行
    fake_ui.confirms = [True]
    cfg = cfg_for(tree, [r"\.tmp$"], high_risk_dirs=[tree])
    Cleaner(cfg, fake_ui, fake_trash).run()
    assert len(fake_trash.calls) == 1
    assert fake_ui.high_risk_calls and "保护目录" in fake_ui.high_risk_calls[0][1]


def test_batch_refusal_stops_after_first_batch(tmp_path, fake_ui, fake_trash):
    root = make_tree(tmp_path, 13)
    fake_ui.confirms = [True]
    fake_ui.next_batches = [False]
    cfg = cfg_for(root, [r"\.tmp$"], max_batch=5)
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()
    assert outcome.aborted is True and outcome.batches == 1
    assert len(fake_trash.calls) == 5  # 已删不回滚，后续不再处理


def test_zero_matches_exits_cleanly(tree, fake_ui, fake_trash):
    outcome = Cleaner(cfg_for(tree, [r"\.xyz$"]), fake_ui, fake_trash).run()
    assert outcome.total_matched == 0 and outcome.results == []
    assert fake_trash.calls == [] and fake_ui.confirm_messages == []


def test_unsupported_platform_rejected(tree, fake_trash, monkeypatch):
    monkeypatch.setattr(sys, "platform", "aix")
    with pytest.raises(PlatformError):
        Cleaner(cfg_for(tree, [r"\.tmp$"]), AutoUI(), fake_trash).run()


def test_unc_path_rejected(fake_trash):
    with pytest.raises(PlatformError, match="UNC"):
        Cleaner(cfg_for(Path("\\\\server\\share"), [r"\.tmp$"]), AutoUI(),
                fake_trash).run()
    assert fake_trash.calls == []


def test_missing_dir_raises_scan_error(tmp_path, fake_ui, fake_trash):
    from efc.exceptions import ScanError

    with pytest.raises(ScanError, match="不存在"):
        Cleaner(cfg_for(tmp_path / "nope", [r"\.tmp$"]), fake_ui, fake_trash).run()


def test_insufficient_space_aborts_zero_trash(tmp_path, fake_ui, fake_trash, monkeypatch):
    fake_ui.confirms = [True]
    root = make_tree(tmp_path, 3)
    monkeypatch.setattr(cleaner_mod.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 0})())
    cfg = cfg_for(root, [r"\.tmp$"], backup_dir=tmp_path / "bk")
    with pytest.raises(AbortError, match="空间不足"):
        Cleaner(cfg, fake_ui, fake_trash).run()
    assert fake_trash.calls == []


def test_excludes_backup_dir_and_log_file(tmp_path, fake_ui, fake_trash):
    fake_ui.confirms = [True]
    root = tmp_path / "t2"
    root.mkdir()
    (root / "a.tmp").write_text("x")
    (root / "run.tmp").write_text("log")  # 日志文件自身命中模式也不可清
    (root / "bk").mkdir()
    (root / "bk" / "b.tmp").write_text("x")
    cfg = AppConfig(target_dir=root, filename_patterns=[r"\.tmp$"],
                    log_file=root / "run.tmp", backup_dir=root / "bk")
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()
    assert outcome.total_matched == 1
    assert [Path(c).name for c in fake_trash.calls] == ["a.tmp"]


def test_dry_run_zero_side_effects(tree, fake_ui, fake_trash):
    fake_ui.confirms = [True]
    cfg = cfg_for(tree, [r"\.tmp$"], backup_dir=tree.parent / "bk")
    outcome = Cleaner(cfg, fake_ui, fake_trash, dry_run=True).run()
    assert fake_trash.calls == []
    assert outcome.total_matched == 1 and outcome.results == []
    assert outcome.backup_dir is None
    assert not (tree.parent / "bk").exists()  # 未创建备份目录


def test_no_backup_disables_backup(tmp_path, fake_ui, fake_trash):
    fake_ui.confirms = [True]
    root = make_tree(tmp_path, 2)
    cfg = cfg_for(root, [r"\.tmp$"], backup_enabled=False,
                  backup_dir=tmp_path / "bk")
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()
    assert outcome.backup_dir is None
    assert all(o.backup_path is None for o in outcome.trashed)
    assert not (tmp_path / "bk").exists()
    assert len(fake_trash.calls) == 2


def test_backup_disabled_via_config_confirm_off(tmp_path, fake_ui, fake_trash):
    """confirm=False 跳过普通确认（配置开关）。"""
    root = make_tree(tmp_path, 1)
    cfg = cfg_for(root, [r"\.tmp$"], confirm=False)
    outcome = Cleaner(cfg, fake_ui, fake_trash).run()  # 未编排 confirm 序列
    assert fake_ui.confirm_messages == [] and len(fake_trash.calls) == 1
    assert outcome.aborted is False


@pytest.mark.skipif(os.environ.get("EFC_REAL_TRASH") != "1",
                    reason="真实回收站用例仅本地手动执行（EFC_REAL_TRASH=1），不上 CI")
def test_real_trash_sends_to_system_trash(tmp_path):
    from send2trash import send2trash

    root = tmp_path / "real"
    root.mkdir()
    (root / "x.tmp").write_text("x")
    outcome = Cleaner(cfg_for(root, [r"\.tmp$"]), AutoUI(), send2trash,
                      task_name="real").run()
    assert len(outcome.trashed) == 1 and not (root / "x.tmp").exists()
