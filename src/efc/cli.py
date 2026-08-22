"""EphemeralFile Cleaner 命令行入口（Spec §6）。

传输级全局选项（--format/--non-interactive/--stdin）在子命令前；子命令
scan/clean/repl/task/patterns 由后续任务逐个接入。错误出口统一两层：
命令内 EfcError 经 _translate 报告（json 信封 / stderr 一行）并转
typer.Exit(exit_code)；main() 兜底 UsageError→2、未预期异常→1。
"""

import functools
import os
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from send2trash import send2trash
from typer._click.exceptions import (  # typer>=0.27 内置 click
    NoArgsIsHelpError,
    UsageError,
)

from efc import __version__
from efc.cleaner import Cleaner
from efc.config import (
    AppConfig,
    Task,
    add_task,
    default_tasks,
    list_tasks,
    load_config,
    merge_overrides,
    read_env_overrides,
    read_stdin_payload,
    remove_task,
    resolve_task,
    save_config,
)
from efc.exceptions import ConfigError, EfcError
from efc.journal import ExecutionLog, build_record, judge_result
from efc.models import CleanOutcome, ScanResult
from efc.output import emit_error, emit_success
from efc.repl import ReplSession
from efc.safety import ensure_supported_platform
from efc.scanner import compile_patterns
from efc.scanner import scan as scan_dir
from efc.summary import build_summary, render_summary
from efc.ui import UI, AutoUI, ConsoleUI

app = typer.Typer(
    help="EphemeralFile Cleaner — 临时文件清理（回收站安全删除）",
    no_args_is_help=True,
    invoke_without_command=True,  # 允许 efc --version 这类仅全局选项的调用
)
task_app = typer.Typer(help="任务清单管理（add/list/remove）", no_args_is_help=True)
app.add_typer(task_app, name="task")


@dataclass
class AgentState:
    """传输级状态，经 ctx.obj 传递；只来自 CLI 与环境变量，不来自 stdin 负载。"""

    format: str = "text"
    format_explicit: bool = False  # CLI 显式 --format（优先于子命令 --json 简写）
    non_interactive: bool = False
    stdin: bool = False


def _command_fmt(state: AgentState, json_flag: bool) -> str:
    """命令内格式裁定：显式 --format > 子命令 --json > EFC_FORMAT > text。

    与 _resolve_format 的 argv 版语义一致，但不依赖 sys.argv
    （CliRunner 直驱 app 时不改写 argv）。
    """
    if state.format_explicit:
        return state.format
    if json_flag:
        return "json"
    return state.format


def _report_error(fmt: str, code: int, msg: str) -> None:
    if fmt == "json":
        emit_error(code, msg)
    else:
        print(f"错误: {msg}", file=sys.stderr)


def _translate(func: Callable[..., Any]) -> Callable[..., Any]:
    """装饰器：EfcError → 错误报告（按当前 format）+ typer.Exit(exit_code)。"""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except EfcError as e:
            _report_error(_resolve_format(), e.exit_code, str(e))
            raise typer.Exit(code=e.exit_code) from e

    return wrapper


def _resolve_format() -> str:
    """在 app() 之前独立解析输出格式，不依赖 callback。

    优先级：sys.argv 的 --format/--format=X（同时出现以 --format 为准，先出现者生效）
    > --json > EFC_FORMAT > text。
    """
    argv = sys.argv[1:]
    fmt: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--format" and i + 1 < len(argv) and argv[i + 1] in ("text", "json"):
            fmt = argv[i + 1]
        elif arg.startswith("--format=") and arg.split("=", 1)[1] in ("text", "json"):
            fmt = arg.split("=", 1)[1]
        if fmt is not None:
            break
    if fmt is not None:
        return fmt
    if "--json" in argv:
        return "json"
    env = os.environ.get("EFC_FORMAT")
    return env if env in ("text", "json") else "text"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true")


# ---------- 输入合并与任务解析（scan/clean/repl 共用，Spec §4.3/§4.4）----------


@app.callback()
@_translate
def main_options(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="显示版本号并退出"),
    format_opt: str = typer.Option(
        None, "--format", help="输出格式：text（人类可读）| json（Agent 单行信封）"
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="无头模式：确认自动通过（高危仍拒绝），全程无 input()"
    ),
    stdin_opt: bool = typer.Option(
        False, "--stdin", help="从 stdin 读取 JSON 负载作为参数来源"
    ),
) -> None:
    """全局回调：解析传输级选项并存入 ctx.obj。"""
    state = AgentState()
    if format_opt is not None:
        if format_opt not in ("text", "json"):
            raise ConfigError(f"--format 的值无效: {format_opt}（期望 text/json）")
        state.format = format_opt
        state.format_explicit = True
    elif (env_fmt := os.environ.get("EFC_FORMAT")) in ("text", "json"):
        state.format = env_fmt
    state.non_interactive = non_interactive or _env_flag("EFC_NON_INTERACTIVE")
    state.stdin = stdin_opt
    ctx.obj = state
    if version:
        print(f"efc {__version__}")
        raise typer.Exit()


@app.command()
@_translate
def repl(
    ctx: typer.Context,
    config: str = typer.Option(None, "--config", help="配置文件路径"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="输出详细诊断信息"),
) -> None:
    """交互会话（text 模式专用；Agent 标志一律拒绝）。"""
    state: AgentState = ctx.obj or AgentState()
    if state.format == "json" or state.non_interactive or state.stdin:
        raise ConfigError(
            "repl 不支持 Agent 模式标志（--format json / --non-interactive / --stdin），"
            "自动化请使用 scan / clean"
        )
    cfg = load_config(Path(config).expanduser() if config else None)
    ReplSession(cfg, ConsoleUI()).run()


def _load_for_write(config: str | None) -> tuple[AppConfig, Path]:
    """写命令（task add/remove）的配置加载与落盘路径。

    显式 --config（或 EFC_CONFIG）必须存在；否则按查找链取已存在的
    配置文件；都没有则从默认配置开始、保存到 ./config.json。
    """
    explicit = config or os.environ.get("EFC_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        return load_config(path), path
    cwd_cfg = Path.cwd() / "config.json"
    if cwd_cfg.is_file():
        return load_config(cwd_cfg), cwd_cfg
    user_cfg = Path("~/.efc/config.json").expanduser()
    if user_cfg.is_file():
        return load_config(user_cfg), user_cfg
    return AppConfig(), cwd_cfg


def _task_view(t: Task) -> dict[str, Any]:
    return {"name": t.name, "dir": str(t.dir), "patterns": list(t.patterns),
            "recursive": t.recursive, "default": t.default}


@task_app.command("add")
@_translate
def task_add(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="任务名（全清单唯一）"),
    dir_opt: str = typer.Option(None, "--dir", help="目标目录（新增必填，须已存在）"),
    pattern: list[str] = typer.Option(None, "--pattern", help="文件名正则（可重复，追加去重）"),
    recursive: bool | None = typer.Option(
        None, "--recursive/--no-recursive", help="三态：未指定则保持原设置"
    ),
    default: bool | None = typer.Option(
        None, "--default/--no-default", help="标记/取消默认任务"
    ),
    replace_patterns: bool = typer.Option(
        False, "--replace-patterns", help="整体替换 patterns（默认追加去重）"
    ),
    config: str = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """新增/更新任务并写盘（同名更新仅覆盖显式字段）。"""
    state: AgentState = ctx.obj or AgentState()
    fmt = _command_fmt(state, False)
    cfg, path = _load_for_write(config)
    add_task(cfg, name=name, dir=dir_opt,
             patterns=list(pattern) if pattern else None,
             recursive=recursive, default=default,
             replace_patterns=replace_patterns)
    save_config(cfg, path)
    data = {"saved": True, "task": _task_view(resolve_task(cfg, name)),
            "config_file": str(path)}
    if fmt == "json":
        emit_success(data)
    else:
        typer.echo(f"已保存任务 {name}（{len(resolve_task(cfg, name).patterns)} 条规则）"
                   f" → {path}")


@task_app.command("list")
@_translate
def task_list(
    ctx: typer.Context,
    config: str = typer.Option(None, "--config", help="配置文件路径"),
    json_flag: bool = typer.Option(False, "--json", help="--format json 简写"),
) -> None:
    """列出任务清单与持久化配置。"""
    state: AgentState = ctx.obj or AgentState()
    fmt = _command_fmt(state, json_flag)
    cfg = load_config(Path(config).expanduser() if config else None)
    data: dict[str, Any] = {
        "tasks": [_task_view(t) for t in list_tasks(cfg)],
        "confirm": cfg.confirm, "max_batch": cfg.max_batch,
        "backup_enabled": cfg.backup_enabled, "backup_dir": str(cfg.backup_dir),
        "ignore_case": cfg.ignore_case, "log_enabled": cfg.log_enabled,
        "log_file": str(cfg.log_file),
        "high_risk_dirs": [str(p) for p in cfg.high_risk_dirs],
    }
    if fmt == "json":
        emit_success(data)
        return
    if not cfg.tasks:
        typer.echo("（任务清单为空：用 efc task add --name ... --dir ... --pattern ... 添加）")
        return
    for t in cfg.tasks:
        mark = " [默认]" if t.default else ""
        rec = "递归" if t.recursive else "顶层"
        typer.echo(f"{t.name}{mark}: {t.dir}（{rec}）")
        for p in t.patterns:
            typer.echo(f"  - {p}")


@task_app.command("remove")
@_translate
def task_remove(
    ctx: typer.Context,
    name: str = typer.Option(None, "--name", help="按任务名移除"),
    dir_opt: str = typer.Option(None, "--dir", help="按目标目录移除"),
    config: str = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """移除任务（--name 与 --dir 二选一）。"""
    state: AgentState = ctx.obj or AgentState()
    fmt = _command_fmt(state, False)
    cfg, path = _load_for_write(config)
    removed = remove_task(cfg, name=name, dir=dir_opt)
    if removed:
        save_config(cfg, path)
    if fmt == "json":
        emit_success({"removed": removed})
    else:
        typer.echo("已移除任务" if removed else "未找到匹配的任务")


@app.command()
@_translate
def patterns(
    ctx: typer.Context,
    task: str = typer.Option(None, "--task", help="只看指定任务"),
    config: str = typer.Option(None, "--config", help="配置文件路径"),
    json_flag: bool = typer.Option(False, "--json", help="--format json 简写"),
) -> None:
    """查看任务规则清单（全部或单个任务）。"""
    state: AgentState = ctx.obj or AgentState()
    fmt = _command_fmt(state, json_flag)
    cfg = load_config(Path(config).expanduser() if config else None)
    selected = [resolve_task(cfg, task)] if task else list_tasks(cfg)
    data = {"tasks": [
        {"task": t.name, "dir": str(t.dir), "default": t.default,
         "patterns": list(t.patterns)}
        for t in selected
    ]}
    if fmt == "json":
        emit_success(data)
        return
    if not selected:
        typer.echo("（任务清单为空）")
        return
    for t in selected:
        mark = " [默认]" if t.default else ""
        typer.echo(f"{t.name}{mark} → {', '.join(t.patterns) if t.patterns else '（无规则）'}")


@dataclass
class RuntimeTask:
    """已应用 CLI/stdin/env 覆盖的每任务运行时参数；一次性任务 name=None。"""

    name: str | None
    dir: Path
    patterns: list[str]
    recursive: bool


def _effective(*layers: dict[str, Any]) -> dict[str, Any]:
    """按层序合并非 None 键（后层优先）——CLI > stdin > env。"""
    eff: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if value is not None:
                eff[key] = value
    return eff


def _gather(state: AgentState,
             cli_layer: dict[str, Any]) -> tuple[AppConfig, dict[str, Any]]:
    """读 env/stdin/CLI 三层 → (合并后的 AppConfig, 有效参数层)。

    config 键同样按 CLI > stdin > env 决定配置文件路径。
    """
    env_layer = read_env_overrides()
    stdin_layer: dict[str, Any] = {}
    if state.stdin:
        payload = read_stdin_payload()
        payload.pop("command", None)  # command 缺省取 CLI 子命令，不参与合并
        stdin_layer = payload
    eff = _effective(env_layer, stdin_layer, cli_layer)
    config_path = eff.get("config")
    cfg = load_config(Path(str(config_path)) if config_path else None)
    cfg = merge_overrides(cfg, env_layer, stdin_layer, cli_layer)
    return cfg, eff


def _resolve_targets(cfg: AppConfig, eff: dict[str, Any]) -> list[RuntimeTask]:
    """任务解析规则：--task > --all-tasks > --dir（一次性）> 默认清单。

    patterns 覆盖对每任务整体替换；recursive 三态覆盖；--dir 与
    --task/--all-tasks 互斥。
    """
    if eff.get("dir") and (eff.get("task") or eff.get("all_tasks")):
        raise ConfigError("--dir 不能与 --task/--all-tasks 同时使用")
    patterns_override = eff.get("patterns")
    recursive_override = eff.get("recursive")

    def finalize(t: Task) -> RuntimeTask:
        if t.dir is None:
            raise ConfigError(f"任务 {t.name} 缺少 dir")
        return RuntimeTask(
            name=t.name,
            dir=t.dir,
            patterns=list(patterns_override) if patterns_override is not None
            else list(t.patterns),
            recursive=bool(recursive_override) if recursive_override is not None
            else t.recursive,
        )

    out: list[RuntimeTask] = []
    if names := eff.get("task"):
        out = [finalize(resolve_task(cfg, name)) for name in names]
    elif eff.get("all_tasks"):
        all_tasks = list_tasks(cfg)
        if not all_tasks:
            raise ConfigError("任务清单为空，--all-tasks 无可执行任务")
        out = [finalize(t) for t in all_tasks]
    elif eff.get("dir"):
        patterns = patterns_override if patterns_override is not None else []
        if not patterns:
            raise ConfigError("一次性任务需要同时提供 --pattern（可重复）")
        target = cfg.target_dir if cfg.target_dir is not None else Path(str(eff["dir"]))
        out = [RuntimeTask(name=None, dir=target, patterns=list(patterns),
                           recursive=bool(recursive_override))]
    else:
        defaults = default_tasks(cfg)
        if not defaults:
            raise ConfigError(
                "没有可执行的任务：请用 efc task add --default 建立默认任务，"
                "或指定 --task / --dir + --pattern"
            )
        out = [finalize(t) for t in defaults]
    return out


def _scan_target(cfg: AppConfig, rt: RuntimeTask) -> ScanResult:
    compiled = compile_patterns(rt.patterns, cfg.ignore_case)
    return scan_dir(rt.dir, compiled, rt.recursive)


def _scan_payload(result: ScanResult, rt: RuntimeTask) -> dict[str, Any]:
    return {
        "task": rt.name,
        "root": str(result.root),
        "recursive": result.recursive,
        "scanned_dirs": result.scanned_dirs,
        "count": len(result.matches),
        "matches": [
            {
                "path": str(m.path),
                "relative": m.relative,
                "size": m.size,
                "mtime": datetime.fromtimestamp(m.mtime).isoformat(),
            }
            for m in result.matches
        ],
    }


@app.command()
@_translate
def scan(
    ctx: typer.Context,
    task: list[str] = typer.Option(None, "--task", help="按任务名选取（可重复）"),
    dir_opt: str = typer.Option(None, "--dir", help="一次性目标目录（与 --task 互斥）"),
    pattern: list[str] = typer.Option(None, "--pattern", help="文件名正则（可重复）"),
    recursive: bool | None = typer.Option(
        None, "--recursive/--no-recursive", help="三态覆盖任务递归设置"
    ),
    config: str = typer.Option(None, "--config", help="配置文件路径"),
    json_flag: bool = typer.Option(False, "--json", help="--format json 简写"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="输出详细诊断信息"),
) -> None:
    """扫描预览：列出各任务命中文件（只读，不删除）。"""
    state: AgentState = ctx.obj or AgentState()
    fmt = _command_fmt(state, json_flag)
    cli_layer: dict[str, Any] = {}
    if task:
        cli_layer["task"] = list(task)
    if dir_opt:
        cli_layer["dir"] = dir_opt
    if pattern:
        cli_layer["patterns"] = list(pattern)
    if recursive is not None:
        cli_layer["recursive"] = recursive
    if config:
        cli_layer["config"] = config
    cfg, eff = _gather(state, cli_layer)
    targets = _resolve_targets(cfg, eff)
    payloads = []
    stderr_ui = ConsoleUI(console=Console(file=sys.stderr, no_color=False))
    for rt in targets:
        result = _scan_target(cfg, rt)
        payloads.append(_scan_payload(result, rt))
        if fmt == "text":
            stderr_ui.show_matches(result)  # text 表格走 stderr
    if fmt == "json":
        emit_success({"tasks": payloads})


def _select_ui(state: AgentState, fmt: str, auto_yes: bool) -> UI:
    """UI 选择：--yes → AutoUI；--non-interactive → 无交互 ConsoleUI（高危仍拒）。

    json 模式人读输出（表格/总结）一律走 stderr，stdout 只留信封。
    """
    stderr_console = Console(file=sys.stderr, no_color=True)
    if auto_yes:
        return AutoUI()
    if state.non_interactive:
        return ConsoleUI(interactive=False, no_color=True, progress=False)
    if fmt == "json":
        return ConsoleUI(console=stderr_console, no_color=True, progress=False)
    return ConsoleUI()


def _aggregate_exit_code(outcomes: list[CleanOutcome]) -> int:
    """多任务退出码：任一失败文件 → 4；否则任一中止 → 3；否则 0（PRD §5.3）。"""
    if any(o.failed for o in outcomes):
        return 4
    if any(o.aborted for o in outcomes):
        return 3
    return 0


@app.command()
@_translate
def clean(
    ctx: typer.Context,
    task: list[str] = typer.Option(None, "--task", help="按任务名选取（可重复）"),
    all_tasks: bool = typer.Option(False, "--all-tasks", help="执行任务清单全部任务"),
    dir_opt: str = typer.Option(None, "--dir", help="一次性目标目录（与 --task 互斥）"),
    pattern: list[str] = typer.Option(None, "--pattern", help="文件名正则（可重复）"),
    recursive: bool | None = typer.Option(
        None, "--recursive/--no-recursive", help="三态覆盖任务递归设置"
    ),
    config: str = typer.Option(None, "--config", help="配置文件路径"),
    yes: bool = typer.Option(False, "--yes", help="跳过普通确认（高危仍需确认）"),
    no_backup: bool = typer.Option(False, "--no-backup", help="本次不备份（放弃恢复手段）"),
    max_batch: int = typer.Option(None, "--max-batch", help="单批文件数上限（1..10）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="预演：不删除不备份"),
    no_log: bool = typer.Option(False, "--no-log", help="本次不写执行日志"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="输出详细诊断信息"),
) -> None:
    """清理：把命中文件移入回收站（删前备份，高危二次确认）。"""
    state: AgentState = ctx.obj or AgentState()
    fmt = _command_fmt(state, False)
    cli_layer: dict[str, Any] = {}
    if task:
        cli_layer["task"] = list(task)
    if all_tasks:
        cli_layer["all_tasks"] = True
    if dir_opt:
        cli_layer["dir"] = dir_opt
    if pattern:
        cli_layer["patterns"] = list(pattern)
    if recursive is not None:
        cli_layer["recursive"] = recursive
    if config:
        cli_layer["config"] = config
    if yes:
        cli_layer["yes"] = True
    if no_backup:
        cli_layer["no_backup"] = True
    if max_batch is not None:
        cli_layer["max_batch"] = max_batch
    if dry_run:
        cli_layer["dry_run"] = True
    if no_log:
        cli_layer["no_log"] = True
    cfg, eff = _gather(state, cli_layer)
    is_dry_run = bool(eff.get("dry_run"))
    if eff.get("no_backup"):
        cfg.backup_enabled = False
    if fmt == "json" and not (eff.get("yes") or state.non_interactive):
        raise ConfigError("json 模式需要 --yes 或 --non-interactive 之一明确确认策略")
    targets = _resolve_targets(cfg, eff)
    ui = _select_ui(state, fmt, auto_yes=bool(eff.get("yes")))
    outcomes: list[CleanOutcome] = []
    for rt in targets:
        task_cfg = replace(cfg, target_dir=rt.dir, filename_patterns=rt.patterns,
                           recursive=rt.recursive)
        outcomes.append(
            Cleaner(task_cfg, ui, send2trash, dry_run=is_dry_run,
                    task_name=rt.name).run()
        )
    log_written = cfg.log_enabled and not eff.get("no_log")
    code = _aggregate_exit_code(outcomes)
    rendered = render_summary(build_summary(outcomes))
    if fmt == "json":
        emit_success(_clean_payload(cfg, targets, outcomes, is_dry_run, code,
                                    log_written))
    elif rendered:
        typer.echo(rendered)
    if log_written:
        ExecutionLog(cfg.log_file).record(build_record("clean", outcomes, is_dry_run))
    if code:
        raise typer.Exit(code=code)


def _clean_payload(cfg: AppConfig, targets: list[RuntimeTask],
                   outcomes: list[CleanOutcome], is_dry_run: bool, code: int,
                   log_written: bool) -> dict[str, Any]:
    """clean 的 json 信封 data（Spec §6.3）。"""
    tasks: list[dict[str, Any]] = []
    for rt, outcome in zip(targets, outcomes, strict=True):
        trashed = outcome.trashed
        by_pattern: list[dict[str, Any]] = []
        for fo in trashed:
            key = fo.pattern if fo.pattern is not None else "(无模式)"
            entry = next((p for p in by_pattern if p["pattern"] == key), None)
            if entry is None:
                entry = {"pattern": key, "files": 0, "bytes": 0}
                by_pattern.append(entry)
            entry["files"] += 1
            entry["bytes"] += fo.size
        tasks.append({
            "name": rt.name,
            "dir": str(outcome.target_dir),
            "trashed": len(trashed),
            "bytes": sum(f.size for f in trashed),
            "by_pattern": by_pattern,
            "files": [
                {"path": str(f.path), "size": f.size, "pattern": f.pattern,
                 "status": f.status}
                for f in outcome.results
            ],
        })
    backup_dir = next((str(o.backup_dir) for o in outcomes
                       if o.backup_dir is not None), None)
    return {
        "command": "clean",
        "result": judge_result(outcomes, is_dry_run),
        "exit_code": code,
        "duration_seconds": sum(o.duration_seconds for o in outcomes),
        "total_matched": sum(o.total_matched for o in outcomes),
        "trashed": sum(len(o.trashed) for o in outcomes),
        "failed": sum(len(o.failed) for o in outcomes),
        "aborted": any(o.aborted for o in outcomes),
        "backup_dir": backup_dir,
        "log_file": str(cfg.log_file) if log_written else None,
        "summary": render_summary(build_summary(outcomes)),
        "tasks": tasks,
    }




def main() -> None:
    """CLI 入口点（pyproject.toml 中 efc = efc.cli:main）。"""
    fmt = _resolve_format()
    code = 0
    try:
        ensure_supported_platform()
        rc = app(standalone_mode=False)
        if isinstance(rc, int):
            code = rc
    except NoArgsIsHelpError:
        code = 0  # click 已打印帮助（与 standalone 行为一致）
    except UsageError as e:
        code = 2
        _report_error(fmt, code, e.format_message())
    except EfcError as e:
        code = e.exit_code
        _report_error(fmt, code, str(e))
    except typer.Abort:
        code = 3
        _report_error(fmt, code, "用户中止")
    except Exception as e:  # noqa: BLE001
        code = 1
        traceback.print_exc(file=sys.stderr)
        _report_error(fmt, code, f"内部错误: {e}")
    raise SystemExit(code)
