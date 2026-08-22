"""efc.config 输入解析测试：EFC_* 环境变量、--stdin 负载、合并优先级（Spec §14）。"""

import io
import sys

import pytest

from efc.config import AppConfig, merge_overrides, read_env_overrides, read_stdin_payload
from efc.exceptions import ConfigError

EFC_VARS = [
    "EFC_CONFIG", "EFC_FORMAT", "EFC_NON_INTERACTIVE", "EFC_TASK", "EFC_DIR",
    "EFC_PATTERNS", "EFC_RECURSIVE", "EFC_DRY_RUN", "EFC_YES", "EFC_MAX_BATCH",
    "EFC_BACKUP_DIR", "EFC_LOG_FILE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in EFC_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


# ---------- EFC_* 解析 ----------


def test_env_empty_returns_nothing():
    assert read_env_overrides() == {}


def test_env_task_split_by_newline_and_semicolon(monkeypatch):
    monkeypatch.setenv("EFC_TASK", "a\nb")
    assert read_env_overrides()["task"] == ["a", "b"]
    monkeypatch.setenv("EFC_TASK", "a;b;c")
    assert read_env_overrides()["task"] == ["a", "b", "c"]
    monkeypatch.setenv("EFC_TASK", "a\nb;c")
    assert read_env_overrides()["task"] == ["a", "b", "c"]


def test_env_patterns_split(clean_env):
    clean_env.setenv("EFC_PATTERNS", "^~\\$\n\\.tmp$;\\.bak$")
    assert read_env_overrides()["patterns"] == ["^~\\$", "\\.tmp$", "\\.bak$"]


def test_env_bool_and_int_conversion(monkeypatch):
    monkeypatch.setenv("EFC_DRY_RUN", "1")
    assert read_env_overrides()["dry_run"] is True
    monkeypatch.setenv("EFC_DRY_RUN", "0")
    assert read_env_overrides()["dry_run"] is False
    monkeypatch.setenv("EFC_YES", "true")
    assert read_env_overrides()["yes"] is True
    monkeypatch.setenv("EFC_NON_INTERACTIVE", "false")
    assert read_env_overrides()["non_interactive"] is False
    monkeypatch.setenv("EFC_RECURSIVE", "1")
    assert read_env_overrides()["recursive"] is True
    monkeypatch.setenv("EFC_MAX_BATCH", "7")
    assert read_env_overrides()["max_batch"] == 7


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("EFC_FORMAT", "yaml"),
        ("EFC_DRY_RUN", "yes"),
        ("EFC_RECURSIVE", "on"),
        ("EFC_MAX_BATCH", "11"),
        ("EFC_MAX_BATCH", "0"),
        ("EFC_MAX_BATCH", "abc"),
    ],
)
def test_env_invalid_values_rejected(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigError):
        read_env_overrides()


def test_env_empty_string_treated_as_unset(monkeypatch):
    monkeypatch.setenv("EFC_DIR", "")
    assert read_env_overrides() == {}


def test_env_simple_string_vars(monkeypatch):
    monkeypatch.setenv("EFC_DIR", "D:\\Downloads")
    monkeypatch.setenv("EFC_BACKUP_DIR", "~/.efc/backup")
    monkeypatch.setenv("EFC_LOG_FILE", "run.log")
    monkeypatch.setenv("EFC_CONFIG", "my.json")
    monkeypatch.setenv("EFC_FORMAT", "json")
    out = read_env_overrides()
    assert out == {
        "config": "my.json", "format": "json", "dir": "D:\\Downloads",
        "backup_dir": "~/.efc/backup", "log_file": "run.log",
    }


# ---------- --stdin 负载 ----------


def feed_stdin(monkeypatch, payload: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))


def test_stdin_valid_payload(monkeypatch):
    feed_stdin(monkeypatch, '{"command": "clean", "task": ["downloads"], '
                            '"dir": "d", "patterns": ["\\\\.tmp$"], "recursive": true, '
                            '"yes": true, "max_batch": 5, "backup_enabled": true, '
                            '"backup_dir": "bd", "dry_run": true, "no_backup": false, '
                            '"no_log": true, "all_tasks": false, "config": "c.json"}')
    assert read_stdin_payload() == {
        "command": "clean", "task": ["downloads"], "dir": "d",
        "patterns": ["\\.tmp$"], "recursive": True, "yes": True, "max_batch": 5,
        "backup_enabled": True, "backup_dir": "bd", "dry_run": True,
        "no_backup": False, "no_log": True, "all_tasks": False, "config": "c.json",
    }


def test_stdin_null_values_pass_through(monkeypatch):
    feed_stdin(monkeypatch, '{"dir": null, "max_batch": null}')
    assert read_stdin_payload() == {"dir": None, "max_batch": None}


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ("not json", "JSON"),
        ("", "为空"),
        ("[1,2]", "对象"),
        ('{"unknown": 1}', "未知键"),
        ('{"max_batch": "5"}', "整数"),
        ('{"max_batch": true}', "整数"),
        ('{"recursive": "yes"}', "bool"),
        ('{"patterns": "abc"}', "字符串数组"),
        ('{"task": [1]}', "字符串数组"),
    ],
)
def test_stdin_invalid_payloads_rejected(monkeypatch, payload, match):
    feed_stdin(monkeypatch, payload)
    with pytest.raises(ConfigError, match=match):
        read_stdin_payload()


def test_stdin_rejects_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", TtyStream(""))
    with pytest.raises(ConfigError, match="TTY"):
        read_stdin_payload()


# ---------- 优先级链：CLI > stdin > env > config ----------


def test_priority_chain_max_batch():
    env_layer = {"max_batch": 3}
    stdin_layer = {"max_batch": 7}
    cli_layer = {"max_batch": 9}
    assert merge_overrides(AppConfig(), env_layer).max_batch == 3
    assert merge_overrides(AppConfig(), env_layer, stdin_layer).max_batch == 7
    assert merge_overrides(AppConfig(), env_layer, stdin_layer, cli_layer).max_batch == 9


def test_priority_chain_dir_and_patterns(tmp_path):
    env_layer = {"dir": str(tmp_path / "env"), "patterns": ["env"]}
    stdin_layer = {"dir": str(tmp_path / "stdin"), "patterns": ["stdin"]}
    cli_layer = {"patterns": ["cli"]}
    out = merge_overrides(AppConfig(), env_layer, stdin_layer, cli_layer)
    assert out.target_dir == tmp_path / "stdin"  # dir：CLI 层未提供 → stdin 生效
    assert out.filename_patterns == ["cli"]  # 列表键整体替换，不做合并


def test_priority_chain_config_file_value_survives(monkeypatch):
    # 无任何覆盖层时保持 config.json 值（此处 AppConfig(max_batch=4) 模拟已加载配置）
    cfg = AppConfig(max_batch=4)
    assert merge_overrides(cfg).max_batch == 4
    assert merge_overrides(cfg, {"recursive": True}).max_batch == 4
