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
from dataclasses import dataclass
from typing import Any

import typer
from typer._click.exceptions import (  # typer>=0.27 内置 click
    NoArgsIsHelpError,
    UsageError,
)

from efc import __version__
from efc.exceptions import ConfigError, EfcError
from efc.output import emit_error
from efc.safety import ensure_supported_platform

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
    non_interactive: bool = False
    stdin: bool = False


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
    typer.echo("REPL 交互会话尚未接入（T019 将补齐 efc> 循环）")


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
