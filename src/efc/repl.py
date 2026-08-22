"""REPL 交互会话（Spec §10）。

task/dir/pattern/recursive 只改会话内状态，不写回 config（持久化用
efc task add）；clean 与 CLI 走同一条 Cleaner 流水线（禁止复制第二套
逻辑），结束后同样写日志、输出总结。默认任务清单恰一个时自动加载。
"""

import re
import shlex
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from send2trash import send2trash

from efc import __version__
from efc.cleaner import Cleaner
from efc.config import AppConfig, Task, default_tasks, list_tasks, resolve_task
from efc.journal import ExecutionLog, build_record
from efc.models import ScanResult
from efc.safety import assess_risk, ensure_supported_platform, is_unc
from efc.scanner import compile_patterns
from efc.scanner import scan as scan_dir
from efc.summary import build_summary, render_summary
from efc.ui import UI

PROMPT = "efc> "

HELP_TEXT = """命令表：
  task [NAME]        列出任务清单 / 加载命名任务
  dir [PATH]         显示当前目录与高危评估 / 设置目标目录
  pattern [REGEX|clear|list]  追加规则（即时校验）/ 清空 / 列出
  recursive [on|off] 查看 / 切换递归
  list               用当前状态扫描预览（只读）
  clean              按当前状态执行清理（同 CLI 流水线）
  status             查看会话状态汇总
  help / exit / quit 帮助 / 退出（EOF 同）"""


class ReplSession:
    """交互会话：会话内状态与 CLI 共用同一套业务模块。"""

    def __init__(self, config: AppConfig, ui: UI,
                 trash: Callable[[str], None] = send2trash) -> None:
        self._cfg = config
        self._ui = ui
        self._trash = trash
        self._task_name: str | None = None
        self._dir: Path | None = None
        self._patterns: list[str] = []
        self._recursive = False
        defaults = default_tasks(config)
        if len(defaults) == 1:  # 恰一个默认任务时自动加载
            self._load_task(defaults[0])

    @property
    def config(self) -> AppConfig:
        return self._cfg

    # ---------- 命令分发 ----------

    def handle(self, line: str) -> bool:
        """处理一行命令；返回 False 表示退出会话。"""
        try:
            parts = shlex.split(line.strip())
        except ValueError as e:
            print(f"输入无法解析: {e}")
            return True
        if not parts:
            return True
        command, args = parts[0].lower(), parts[1:]
        if command in ("exit", "quit"):
            return False
        if command in ("help", "?"):
            print(HELP_TEXT)
        elif command == "task":
            self._cmd_task(args)
        elif command == "dir":
            self._cmd_dir(args)
        elif command == "pattern":
            self._cmd_pattern(args)
        elif command == "recursive":
            self._cmd_recursive(args)
        elif command == "list":
            self._cmd_list()
        elif command == "clean":
            self._cmd_clean()
        elif command == "status":
            self._cmd_status()
        else:
            print(f"未知命令: {command}（输入 help 查看命令表）")
        return True

    def run(self) -> None:
        """主循环：input 提示符；EOF 退出，Ctrl+C 清行、连按退出。"""
        ensure_supported_platform()
        print(f"efc {__version__} — 输入 help 查看命令")
        consecutive_interrupts = 0
        while True:
            try:
                line = input(PROMPT)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                consecutive_interrupts += 1
                if consecutive_interrupts >= 2:
                    print()
                    break
                print("^C（再按一次 Ctrl+C 退出）")
                continue
            consecutive_interrupts = 0
            try:
                if not self.handle(line):
                    break
            except Exception as e:  # 单条命令出错不崩会话
                print(f"错误: {e}")
        print("再见")

    # ---------- 各命令 ----------

    def _load_task(self, task: Task) -> None:
        self._task_name = task.name
        self._dir = task.dir.expanduser() if task.dir is not None else None
        self._patterns = list(task.patterns)
        self._recursive = task.recursive

    def _cmd_task(self, args: list[str]) -> None:
        if not args:
            tasks = list_tasks(self._cfg)
            if not tasks:
                print("（任务清单为空：用 efc task add 添加，或 dir/pattern 手动配置）")
                return
            for t in tasks:
                mark = " [默认]" if t.default else ""
                rec = "递归" if t.recursive else "顶层"
                print(f"{t.name}{mark}: {t.dir}（{rec}，{len(t.patterns)} 条规则）")
            return
        try:
            task = resolve_task(self._cfg, args[0])
        except Exception:
            print(f"任务不存在: {args[0]}（会话未变）")
            return
        self._load_task(task)
        print(f"已加载任务 {task.name}: {task.dir}（{len(self._patterns)} 条规则，"
              f"recursive={self._recursive}）")

    def _cmd_dir(self, args: list[str]) -> None:
        if not args:
            if self._dir is None:
                print("当前目录: （未设置）")
                return
            decision = assess_risk(self._dir, self._recursive,
                                   self._cfg.high_risk_dirs)
            risk = f"高危（{decision.reason}）" if decision.high_risk else "普通"
            print(f"当前目录: {self._dir}（{risk}）")
            return
        candidate = Path(args[0]).expanduser()
        if is_unc(candidate):
            print(f"拒绝设置: {candidate} 是 Windows UNC 网络路径")
            return
        if not candidate.exists():
            print(f"拒绝设置: {candidate} 不存在")
            return
        self._dir = candidate.resolve()
        decision = assess_risk(self._dir, self._recursive,
                               self._cfg.high_risk_dirs)
        if decision.high_risk:
            print(f"警告: {self._dir} 是高危目录（{decision.reason}），"
                  f"clean 前需输入完整路径确认")
        else:
            print(f"目录已设置: {self._dir}")

    def _cmd_pattern(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            if not self._patterns:
                print("（无规则）")
                return
            for i, p in enumerate(self._patterns, 1):
                print(f"  {i}. {p}")
            return
        if args[0] == "clear":
            self._patterns = []
            print("规则已清空")
            return
        candidate = args[0]
        try:
            compile_patterns([candidate], self._cfg.ignore_case)
        except Exception as e:
            print(f"非法正则，未追加: {e}")
            return
        self._patterns.append(candidate)
        print(f"已追加: {candidate}（共 {len(self._patterns)} 条）")

    def _cmd_recursive(self, args: list[str]) -> None:
        if not args:
            print(f"recursive={'on' if self._recursive else 'off'}")
            return
        value = args[0].lower()
        if value == "on":
            self._recursive = True
        elif value == "off":
            self._recursive = False
        else:
            print("用法: recursive [on|off]")
            return
        print(f"recursive={value}")

    def _require_ready(self, action: str) -> bool:
        missing = []
        if self._dir is None:
            missing.append("dir")
        if not self._patterns:
            missing.append("pattern")
        if missing:
            print(f"无法{action}: 缺少 {' 与 '.join(missing)}（用 dir/pattern 设置）")
            return False
        return True

    def _cmd_list(self) -> None:
        if not self._require_ready("预览"):
            return
        compiled = compile_patterns(self._patterns, self._cfg.ignore_case)
        result = self._scan(compiled)
        self._ui.show_matches(result)

    def _scan(self, compiled: list[re.Pattern[str]]) -> ScanResult:
        assert self._dir is not None  # 由 _require_ready 保证
        return scan_dir(self._dir, compiled, self._recursive,
                        exclude=[self._cfg.backup_dir, self._cfg.log_file])

    def _cmd_clean(self) -> None:
        if not self._require_ready("清理"):
            return
        session_cfg = replace(self._cfg, target_dir=self._dir,
                              filename_patterns=list(self._patterns),
                              recursive=self._recursive)
        outcome = Cleaner(session_cfg, self._ui, self._trash,
                          task_name=self._task_name).run()
        if self._cfg.log_enabled:
            ExecutionLog(self._cfg.log_file).record(
                build_record("repl", [outcome], False))
        rendered = render_summary(build_summary([outcome]))
        if rendered:
            print(rendered)

    def _cmd_status(self) -> None:
        task = self._task_name if self._task_name else "（一次性/未加载）"
        patterns = "、".join(self._patterns) if self._patterns else "（无）"
        print(f"任务: {task}")
        print(f"目录: {self._dir or '（未设置）'}")
        print(f"规则: {patterns}")
        print(f"递归: {'on' if self._recursive else 'off'}")
        print(f"confirm={self._cfg.confirm} max_batch={self._cfg.max_batch} "
              f"backup={self._cfg.backup_enabled}（{self._cfg.backup_dir}）")
