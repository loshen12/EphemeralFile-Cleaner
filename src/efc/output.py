"""Agent JSON 信封与退出码映射（Spec §5/§6.2）。

stdout 只允许一行结果 JSON：成功 {"data":...} / 失败 {"code":N,"msg":...}，
code 与进程退出码一致；禁止 rich 输出 JSON（非 tty 下 80 列折行会破坏单行）。
"""

import json
import sys
from typing import Any

from typer._click.exceptions import UsageError  # typer>=0.27 内置 click

from efc.exceptions import EfcError


def emit_success(data: dict[str, Any]) -> None:
    """stdout 单行成功信封 {"data": data}（ensure_ascii=False）。"""
    print(json.dumps({"data": data}, ensure_ascii=False), file=sys.stdout)


def emit_error(code: int, msg: str) -> None:
    """stdout 单行失败信封 {"code": code, "msg": msg}。"""
    print(json.dumps({"code": code, "msg": msg}, ensure_ascii=False), file=sys.stdout)


def exit_code_for(exc: BaseException) -> int:
    """EfcError → exit_code；UsageError → 2；其余 → 1。"""
    if isinstance(exc, EfcError):
        return exc.exit_code
    if isinstance(exc, UsageError):
        return 2
    return 1
