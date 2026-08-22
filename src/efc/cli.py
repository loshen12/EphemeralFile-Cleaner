"""EphemeralFile Cleaner 命令行入口（T001 项目骨架）。

scan / clean / repl / task / patterns 命令与 Agent 交互（--format json、
--non-interactive、--stdin）在 T016 及后续任务中实现；此处仅提供可运行的
Typer 应用与 main() 入口点，保证 `efc --help` / `efc --version` 可用。
"""

import sys

import typer

from efc import __version__

app = typer.Typer(
    help="EphemeralFile Cleaner — 临时文件清理（回收站安全删除）",
    no_args_is_help=True,
)


@app.callback()
def main_options(
    version: bool = typer.Option(False, "--version", help="显示版本号并退出"),
) -> None:
    """全局回调。T016 将在此补充 --format / --non-interactive / --stdin 传输级选项。"""
    if version:
        print(f"efc {__version__}")
        raise typer.Exit()


def main() -> None:
    """CLI 入口点（pyproject.toml 中 efc = efc.cli:main）。完整异常处理见 T016。"""
    if "--version" in sys.argv:
        # 骨架阶段（尚无子命令）typer 不会执行回调中的 --version，此处独立预检；
        # 与 Spec.md「CLI 契约」的 _resolve_format() 思路一致，均不依赖 callback。
        print(f"efc {__version__}")
        return
    app()
