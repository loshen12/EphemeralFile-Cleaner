"""efc.output 测试（Spec §14）：信封格式/exit_code_for 三分支。"""

import json

from typer._click.exceptions import UsageError

from efc.exceptions import AbortError, ConfigError, EfcError, ScanError
from efc.output import emit_error, emit_success, exit_code_for


def test_emit_success_single_line_envelope(capsys):
    emit_success({"ok": "中文", "items": [1, 2]})
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")
    assert json.loads(out) == {"data": {"ok": "中文", "items": [1, 2]}}  # 中文不转义


def test_emit_error_single_line_envelope(capsys):
    emit_error(3, "高危目标未获确认")
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {"code": 3, "msg": "高危目标未获确认"}


def test_exit_code_for_efc_errors():
    assert exit_code_for(ConfigError("x")) == 2
    assert exit_code_for(ScanError("x")) == 2
    assert exit_code_for(EfcError("x")) == 2
    assert exit_code_for(AbortError("x")) == 3


def test_exit_code_for_usage_error():
    assert exit_code_for(UsageError("bad option")) == 2


def test_exit_code_for_unknown_exception():
    assert exit_code_for(ValueError("boom")) == 1
    assert exit_code_for(KeyError("k")) == 1
