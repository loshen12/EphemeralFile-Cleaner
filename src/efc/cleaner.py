"""清理流水线（Spec §7）：安全门 → 扫描 → 确认 → 备份 → 分批入回收站。

单任务执行：只消费 AppConfig 三个运行时字段（target_dir/filename_patterns/
recursive）与 confirm/backup/max_batch 等开关；任务清单解析在 cli/repl 层。
唯一删除入口 send2trash 经构造注入。dry_run 只走扫描与确认流程，
零副作用（results 为空、不建备份、不写 manifest）；命中明细由
ui.show_matches 展示、总量经 total_matched 传递。
"""

import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

from efc.backup import BackupRun, new_run
from efc.config import AppConfig
from efc.exceptions import (
    AbortError,
    ConfigError,
    PlatformError,
    ScanError,
)
from efc.models import (
    STATUS_BACKUP_FAILED,
    STATUS_TRASH_FAILED,
    STATUS_TRASHED,
    CleanOutcome,
    FileMatch,
    FileOutcome,
)
from efc.safety import assess_risk, ensure_supported_platform, is_unc
from efc.scanner import compile_patterns, scan
from efc.ui import UI


def _existing_ancestor(p: Path) -> Path:
    """自 p 向上找到第一个存在的祖先（disk_usage 需要真实路径）。"""
    q = p.expanduser()
    while not q.exists():
        if q.parent == q:
            return Path(".")
        q = q.parent
    return q


class Cleaner:
    """一个已解析任务的清理执行器；config 运行时字段须已就绪。"""

    def __init__(
        self,
        config: AppConfig,
        ui: UI,
        trash: Callable[[str], None] = send2trash,
        *,
        dry_run: bool = False,
        task_name: str | None = None,
    ) -> None:
        self._cfg = config
        self._ui = ui
        self._trash = trash
        self._dry_run = dry_run
        self._task_name = task_name

    def run(self) -> CleanOutcome:
        cfg = self._cfg
        start = time.perf_counter()
        # 步骤 2：平台守卫 + UNC 拦截
        ensure_supported_platform()
        if cfg.target_dir is None:
            raise ConfigError("目标目录未设置（任务解析应已填充 target_dir）")
        target = cfg.target_dir.expanduser()
        if is_unc(target):
            raise PlatformError(f"不支持 Windows UNC 网络路径: {target}")
        # 步骤 3：任务目录存在性
        if not target.exists():
            raise ScanError(f"目标目录不存在: {target}")
        if not target.is_dir():
            raise ScanError(f"目标不是目录: {target}")
        # 步骤 4：高危评估与二次确认
        risk = assess_risk(target, cfg.recursive, cfg.high_risk_dirs)
        if risk.high_risk:
            reason = risk.reason or "高危目标"
            if not self._ui.confirm_high_risk(target.resolve(), reason):
                raise AbortError(f"高危目标未获确认，已中止: {target}")
        # 步骤 5：编译与扫描（排除备份目录与日志文件自身）
        compiled = compile_patterns(cfg.filename_patterns, cfg.ignore_case)
        result = scan(target, compiled, cfg.recursive,
                      exclude=[cfg.backup_dir, cfg.log_file])
        resolved_target = target.resolve()
        # 步骤 6：零命中
        if not result.matches:
            self._ui.show_matches(result)
            return CleanOutcome(total_matched=0, results=[], batches=0, backup_dir=None,
                                task_name=self._task_name, target_dir=resolved_target,
                                duration_seconds=time.perf_counter() - start)
        # 步骤 7：展示与普通确认
        self._ui.show_matches(result)
        if cfg.confirm and not self._ui.confirm(
            f"确认将 {len(result.matches)} 个文件移入回收站?"
        ):
            return CleanOutcome(total_matched=len(result.matches), results=[], batches=0,
                                backup_dir=None, aborted=True, task_name=self._task_name,
                                target_dir=resolved_target,
                                duration_seconds=time.perf_counter() - start)
        if self._dry_run:
            return CleanOutcome(total_matched=len(result.matches), results=[], batches=0,
                                backup_dir=None, task_name=self._task_name,
                                target_dir=resolved_target,
                                duration_seconds=time.perf_counter() - start)
        # 步骤 8：备份空间预检（可用 < 总字节×1.05 → AbortError，零 trash）+ 建备份目录
        backup_run: BackupRun | None = None
        if cfg.backup_enabled:
            self._precheck_space(cfg.backup_dir,
                                 sum(m.size for m in result.matches))
            backup_run = new_run(cfg.backup_dir.expanduser())
        # 步骤 9：分批处理（每批 ≤ max_batch；批间拒绝则停止，已删不回滚）
        outcomes: list[FileOutcome] = []
        aborted = False
        batches = 0
        total = len(result.matches)
        for i in range(0, total, cfg.max_batch):
            if batches > 0 and not self._ui.confirm_next_batch(len(outcomes), total):
                aborted = True
                break
            batches += 1
            for m in result.matches[i:i + cfg.max_batch]:
                outcomes.append(self._process_one(m, backup_run))
        # 步骤 10：manifest 与结局汇总
        if backup_run is not None:
            backup_run.write_manifest(outcomes, {
                "run_at": datetime.now().isoformat(timespec="seconds"),
                "target_dir": str(resolved_target),
                "patterns": list(cfg.filename_patterns),
                "recursive": cfg.recursive,
            })
        outcome = CleanOutcome(
            total_matched=total,
            results=outcomes,
            batches=batches,
            backup_dir=backup_run.root if backup_run is not None else None,
            aborted=aborted,
            task_name=self._task_name,
            target_dir=resolved_target,
            duration_seconds=time.perf_counter() - start,
            total_bytes=sum(o.size for o in outcomes if o.status == STATUS_TRASHED),
        )
        self._ui.show_summary(outcome)
        return outcome

    def _process_one(self, m: FileMatch, backup_run: BackupRun | None) -> FileOutcome:
        """批内单文件：备份失败跳过 trash；trash 失败继续批次。"""
        backup_path: Path | None = None
        if backup_run is not None:
            try:
                backup_path = backup_run.backup_file(m.path, m.relative)
            except OSError as e:
                return FileOutcome(path=m.path, status=STATUS_BACKUP_FAILED,
                                   error=f"备份失败: {e}", size=m.size, pattern=m.pattern)
        try:
            self._trash(str(m.path))
        except Exception as e:  # send2trash 各平台异常类型不一，一律计 trash_failed
            return FileOutcome(path=m.path, status=STATUS_TRASH_FAILED,
                               backup_path=backup_path, error=f"回收站失败: {e}",
                               size=m.size, pattern=m.pattern)
        return FileOutcome(path=m.path, status=STATUS_TRASHED, backup_path=backup_path,
                           size=m.size, pattern=m.pattern)

    def _precheck_space(self, backup_dir: Path, needed: int) -> None:
        try:
            usage = shutil.disk_usage(_existing_ancestor(backup_dir))
        except OSError:
            return  # 无法预检时交由后续 copy 失败兜底（计 backup_failed）
        if usage.free < int(needed * 1.05):
            raise AbortError(
                f"备份空间不足：约需 {int(needed * 1.05)} 字节，"
                f"{backup_dir} 所在卷可用 {usage.free} 字节"
            )
