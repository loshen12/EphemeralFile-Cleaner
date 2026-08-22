"""配置系统（Spec §4）：AppConfig/Task 数据模型、加载/合并/校验/保存、任务清单增删查、
Agent 输入（环境变量 EFC_* 与 --stdin JSON 负载）解析。

任务清单 tasks[] 是任务唯一持久化形式；v1.0 顶层默认目标字段
（target_dir/filename_patterns/recursive）出现在 config.json 即 ConfigError。
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from efc.exceptions import ConfigError

DEFAULT_MAX_BATCH = 5
HARD_MAX_BATCH = 10

# config.json 顶层允许的持久化键
_PERSISTENT_KEYS = frozenset({
    "tasks", "confirm", "max_batch", "backup_enabled", "backup_dir",
    "ignore_case", "high_risk_dirs", "log_enabled", "log_file",
})
# v1.0 顶层默认目标字段：出现即明确失败，不做静默迁移
_LEGACY_KEYS = frozenset({"target_dir", "filename_patterns", "recursive"})
_TASK_KEYS = frozenset({"name", "dir", "patterns", "recursive", "default"})


@dataclass
class Task:
    """长期任务：目标目录 + 文件名正则组。name 全清单唯一。"""

    name: str
    dir: Path | None = None
    patterns: list[str] = field(default_factory=list)
    recursive: bool = False
    default: bool = False  # True → 默认任务清单


@dataclass
class AppConfig:
    """运行时解析结果（不落盘）+ 持久化字段（save_config 只写这些）。"""

    # 运行时解析结果（由 cli/repl 按任务解析规则逐任务填充）
    target_dir: Path | None = None
    filename_patterns: list[str] = field(default_factory=list)
    recursive: bool = False
    # 持久化字段
    confirm: bool = True
    max_batch: int = DEFAULT_MAX_BATCH
    backup_enabled: bool = True
    backup_dir: Path = Path(".efc-backup")
    ignore_case: bool = True
    high_risk_dirs: list[Path] = field(default_factory=list)
    log_enabled: bool = True
    log_file: Path = Path(".efc.log")
    tasks: list[Task] = field(default_factory=list)  # Cleaner 不消费


# ---------- 内部工具 ----------


def _expand(p: str | Path) -> Path:
    return Path(os.path.expanduser(os.fspath(p)))


def _norm_path(p: str | Path) -> str:
    return os.path.normcase(os.fspath(_expand(p).resolve()))


def _bool_value(v: Any, key: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(f"{key} 必须是布尔值")
    return v


def _bool_key(data: dict[str, Any], key: str) -> bool:
    return _bool_value(data[key], f"配置键 {key}")


def _int_key(data: dict[str, Any], key: str) -> int:
    v = data[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"配置键 {key} 必须是整数")
    return v


def _str_key(data: dict[str, Any], key: str) -> str:
    v = data[key]
    if not isinstance(v, str):
        raise ConfigError(f"配置键 {key} 必须是字符串")
    return v


def _str_list_key(data: dict[str, Any], key: str) -> list[str]:
    v = data[key]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"配置键 {key} 必须是字符串数组")
    return list(v)


# ---------- 校验 ----------


def validate(cfg: AppConfig) -> None:
    """整体校验；违反即 ConfigError（Spec §4.2）。"""
    if isinstance(cfg.max_batch, bool) or not isinstance(cfg.max_batch, int) \
            or not 1 <= cfg.max_batch <= HARD_MAX_BATCH:
        raise ConfigError(
            f"max_batch 必须在 1..{HARD_MAX_BATCH} 之间，当前为 {cfg.max_batch!r}"
        )
    seen: set[str] = set()
    for task in cfg.tasks:
        if not isinstance(task.name, str) or not task.name.strip():
            raise ConfigError("任务 name 必须是非空字符串")
        if task.name in seen:
            raise ConfigError(f"任务名重复: {task.name}")
        seen.add(task.name)
        if task.dir is None or not str(task.dir).strip():
            raise ConfigError(f"任务 {task.name} 缺少 dir")
        for p in task.patterns:
            if not isinstance(p, str):
                raise ConfigError(f"任务 {task.name} 的 patterns 必须是字符串数组")


# ---------- 加载 ----------


def load_config(path: Path | None = None) -> AppConfig:
    """加载配置。查找链：显式 path > ./config.json > ~/.efc/config.json > 内置默认。

    缺失候选不算错误（返回默认值）；显式指定的 path 不存在或解析失败 → ConfigError。
    """
    if path is not None:
        return _parse_file(_expand(path))
    for candidate in (Path.cwd() / "config.json", _expand("~/.efc/config.json")):
        if candidate.is_file():
            return _parse_file(candidate)
    return AppConfig()


def _parse_file(path: Path) -> AppConfig:
    if not path.is_file():
        raise ConfigError(f"配置文件不存在或不是文件: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"配置文件读取失败: {path}（{e}）") from e
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置文件不是合法 JSON: {path}（{e}）") from e
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是 JSON 对象: {path}")
    return _from_dict(data)


def _from_dict(data: dict[str, Any]) -> AppConfig:
    legacy = _LEGACY_KEYS & set(data)
    if legacy:
        raise ConfigError(
            "v1.0 顶层默认目标字段已取消: " + ", ".join(sorted(legacy))
            + "；请改用 tasks[] 任务清单（参见 config.example.json）"
        )
    unknown = set(data) - _PERSISTENT_KEYS
    if unknown:
        raise ConfigError(f"未知配置键: " + ", ".join(sorted(unknown)))
    cfg = AppConfig()
    if "tasks" in data:
        raw_tasks = data["tasks"]
        if not isinstance(raw_tasks, list):
            raise ConfigError("配置键 tasks 必须是任务对象数组")
        cfg.tasks = [_task_from_dict(i, t) for i, t in enumerate(raw_tasks)]
    if "confirm" in data:
        cfg.confirm = _bool_key(data, "confirm")
    if "max_batch" in data:
        cfg.max_batch = _int_key(data, "max_batch")
    if "backup_enabled" in data:
        cfg.backup_enabled = _bool_key(data, "backup_enabled")
    if "backup_dir" in data:
        cfg.backup_dir = _expand(_str_key(data, "backup_dir")).resolve()
    if "ignore_case" in data:
        cfg.ignore_case = _bool_key(data, "ignore_case")
    if "high_risk_dirs" in data:
        cfg.high_risk_dirs = [
            _expand(s) for s in _str_list_key(data, "high_risk_dirs")
        ]
    if "log_enabled" in data:
        cfg.log_enabled = _bool_key(data, "log_enabled")
    if "log_file" in data:
        cfg.log_file = _expand(_str_key(data, "log_file"))
    validate(cfg)
    return cfg


def _task_from_dict(index: int, raw: Any) -> Task:
    if not isinstance(raw, dict):
        raise ConfigError(f"tasks[{index}] 必须是 JSON 对象")
    unknown = set(raw) - _TASK_KEYS
    if unknown:
        raise ConfigError(f"tasks[{index}] 含未知键: " + ", ".join(sorted(unknown)))
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"tasks[{index}].name 必须是非空字符串")
    d = raw.get("dir")
    if not isinstance(d, str) or not d.strip():
        raise ConfigError(f"任务 {name} 的 dir 必须是非空字符串")
    if "patterns" not in raw:
        raise ConfigError(f"任务 {name} 缺少 patterns")
    patterns = raw["patterns"]
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ConfigError(f"任务 {name} 的 patterns 必须是字符串数组")
    recursive = raw.get("recursive", False)
    default = raw.get("default", False)
    if not isinstance(recursive, bool) or not isinstance(default, bool):
        raise ConfigError(f"任务 {name} 的 recursive/default 必须是布尔值")
    return Task(
        name=name,
        dir=_expand(d),
        patterns=list(patterns),
        recursive=recursive,
        default=default,
    )


# ---------- 合并 ----------


def merged(base: AppConfig, overrides: dict[str, Any]) -> AppConfig:
    """以 CLI 参数命名空间键应用覆盖（None 不覆盖），返回新 AppConfig 并 validate。

    识别键：dir/patterns/recursive/max_batch/confirm/backup_enabled/backup_dir/
    ignore_case/high_risk_dirs/log_file（dir→target_dir、patterns→filename_patterns）。
    其余键（yes/dry_run/task/format 等传输级与 CLI 层标志）不映射到 AppConfig，
    由 cli 层单独消费。
    """
    cfg = replace(base)
    v: Any
    if (v := overrides.get("dir")) is not None:
        cfg.target_dir = _expand(v)
    if (v := overrides.get("patterns")) is not None:
        if not isinstance(v, list) or not all(isinstance(p, str) for p in v):
            raise ConfigError("patterns 必须是字符串数组")
        cfg.filename_patterns = list(v)
    if (v := overrides.get("recursive")) is not None:
        cfg.recursive = _bool_value(v, "recursive")
    if (v := overrides.get("max_batch")) is not None:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError("max_batch 必须是整数")
        cfg.max_batch = v
    if (v := overrides.get("confirm")) is not None:
        cfg.confirm = _bool_value(v, "confirm")
    if (v := overrides.get("backup_enabled")) is not None:
        cfg.backup_enabled = _bool_value(v, "backup_enabled")
    if (v := overrides.get("backup_dir")) is not None:
        cfg.backup_dir = _expand(v).resolve()
    if (v := overrides.get("ignore_case")) is not None:
        cfg.ignore_case = _bool_value(v, "ignore_case")
    if (v := overrides.get("high_risk_dirs")) is not None:
        if not isinstance(v, list):
            raise ConfigError("high_risk_dirs 必须是数组")
        cfg.high_risk_dirs = [_expand(x) for x in v]
    if (v := overrides.get("log_file")) is not None:
        cfg.log_file = _expand(v)
    validate(cfg)
    return cfg


def merge_overrides(cfg: AppConfig, *layers: dict[str, Any]) -> AppConfig:
    """逐层叠加（后层覆盖前层，即优先级从低到高），每层应用后 validate()。"""
    for layer in layers:
        cfg = merged(cfg, layer)
    return cfg


# ---------- 保存 ----------


def save_config(cfg: AppConfig, path: Path) -> None:
    """原子写（临时文件 + os.replace）；只写持久化字段；失败 ConfigError。

    写入失败时临时文件可能残留（删除 API 为项目红线，不主动清理）。
    """
    validate(cfg)
    data: dict[str, Any] = {
        "tasks": [
            {
                "name": t.name,
                "dir": str(t.dir),
                "patterns": list(t.patterns),
                "recursive": t.recursive,
                "default": t.default,
            }
            for t in cfg.tasks
        ],
        "confirm": cfg.confirm,
        "max_batch": cfg.max_batch,
        "backup_enabled": cfg.backup_enabled,
        "backup_dir": str(cfg.backup_dir),
        "ignore_case": cfg.ignore_case,
        "high_risk_dirs": [str(p) for p in cfg.high_risk_dirs],
        "log_enabled": cfg.log_enabled,
        "log_file": str(cfg.log_file),
    }
    path = _expand(path)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        raise ConfigError(
            f"配置写入失败: {path}（{e}；临时文件可能残留于 {tmp}）"
        ) from e


# ---------- 任务清单：增删查 ----------


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _compile_check(patterns: list[str]) -> None:
    for p in patterns:
        try:
            re.compile(p)
        except re.error as e:
            raise ConfigError(f"非法正则 {p!r}: {e}") from e


def _existing_dir(value: str | Path, name: str) -> Path:
    p = _expand(value)
    if not p.exists():
        raise ConfigError(f"任务 {name} 的目录不存在: {p}")
    return p


def add_task(
    cfg: AppConfig,
    *,
    name: str,
    dir: str | Path | None = None,
    patterns: list[str] | None = None,
    recursive: bool | None = None,
    default: bool | None = None,
    replace_patterns: bool = False,
) -> None:
    """新增（dir 必填且必须存在）/同名更新（仅覆盖显式字段）。

    patterns 默认追加去重，replace_patterns=True 整体替换；default=None 不动
    标记。任何校验失败（dir 不存在、正则不可编译、整体 validate 不过）抛
    ConfigError 且不改动 cfg——是否落盘由调用方随后调用 save_config 决定。
    """
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("任务 name 必须是非空字符串")
    name = name.strip()
    incoming = list(patterns or [])
    existing = next((t for t in cfg.tasks if t.name == name), None)
    if existing is None:
        if dir is None:
            raise ConfigError(f"新增任务 {name} 必须指定 dir")
        task = Task(
            name=name,
            dir=_existing_dir(dir, name),
            patterns=_dedup(incoming),
            recursive=bool(recursive),
            default=bool(default),
        )
        new_tasks = [*cfg.tasks, task]
    else:
        task = Task(
            name=existing.name,
            dir=_existing_dir(dir, name) if dir is not None else existing.dir,
            patterns=(
                _dedup(incoming)
                if replace_patterns
                else _dedup([*existing.patterns, *incoming])
            ),
            recursive=existing.recursive if recursive is None else recursive,
            default=existing.default if default is None else default,
        )
        new_tasks = [task if t.name == name else t for t in cfg.tasks]
    _compile_check(task.patterns)
    validate(replace(cfg, tasks=new_tasks))
    cfg.tasks = new_tasks


def remove_task(
    cfg: AppConfig, *, name: str | None = None, dir: str | Path | None = None
) -> bool:
    """按 name 或 dir（normcase 比对）移除任务；返回是否移除了任务。"""
    before = len(cfg.tasks)
    if name is not None and dir is None:
        cfg.tasks = [t for t in cfg.tasks if t.name != name]
    elif dir is not None and name is None:
        needle = _norm_path(dir)
        cfg.tasks = [
            t for t in cfg.tasks if t.dir is None or _norm_path(t.dir) != needle
        ]
    else:
        raise ConfigError("必须且只能指定 name 或 dir 之一")
    return len(cfg.tasks) < before


def list_tasks(cfg: AppConfig) -> list[Task]:
    """按配置顺序返回任务清单副本。"""
    return list(cfg.tasks)


def resolve_task(cfg: AppConfig, name: str) -> Task:
    """按名取任务；未知名 → ConfigError。"""
    for task in cfg.tasks:
        if task.name == name:
            return task
    raise ConfigError(f"任务不存在: {name}")


def default_tasks(cfg: AppConfig) -> list[Task]:
    """default=True 的任务，按配置顺序。"""
    return [t for t in cfg.tasks if t.default]


# ---------- Agent 输入：环境变量与 --stdin ----------

# --stdin 负载 Schema（Spec §4.4）；传输级标志只来自 CLI 与环境变量，不在此列
_STDIN_SCHEMA: dict[str, type[Any]] = {
    "command": str,
    "config": str,
    "task": list,
    "all_tasks": bool,
    "dir": str,
    "patterns": list,
    "recursive": bool,
    "yes": bool,
    "max_batch": int,
    "backup_enabled": bool,
    "backup_dir": str,
    "dry_run": bool,
    "no_backup": bool,
    "no_log": bool,
}
_STDIN_LIST_STR_KEYS = frozenset({"task", "patterns"})


def _parse_env_bool(var: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true"):
        return True
    if value in ("0", "false"):
        return False
    raise ConfigError(f"环境变量 {var} 的值无效: {raw}（期望 1/0/true/false）")


def _split_env_list(raw: str) -> list[str]:
    """换行或分号分隔（cmd 无换行时用 ;），保序去空。"""
    return [part.strip() for part in re.split(r"[\n;]", raw) if part.strip()]


def read_env_overrides() -> dict[str, Any]:
    """EFC_* 环境变量 → 覆盖字典（CLI 参数命名空间）；非法值 ConfigError。

    空字符串视为未设置。传输级标志（format/non_interactive）与 CLI 层标志
    （task/dry_run/yes）一并产出，由 cli 层消费；merged() 只映射 AppConfig 键。
    """
    out: dict[str, Any] = {}

    def raw(var: str) -> str | None:
        value = os.environ.get(var)
        return value if value else None

    if v := raw("EFC_CONFIG"):
        out["config"] = v
    if v := raw("EFC_FORMAT"):
        if v not in ("text", "json"):
            raise ConfigError(f"EFC_FORMAT 的值无效: {v}（期望 text/json）")
        out["format"] = v
    if v := raw("EFC_NON_INTERACTIVE"):
        out["non_interactive"] = _parse_env_bool("EFC_NON_INTERACTIVE", v)
    if v := raw("EFC_TASK"):
        out["task"] = _split_env_list(v)
    if v := raw("EFC_DIR"):
        out["dir"] = v
    if v := raw("EFC_PATTERNS"):
        out["patterns"] = _split_env_list(v)
    if v := raw("EFC_RECURSIVE"):
        out["recursive"] = _parse_env_bool("EFC_RECURSIVE", v)
    if v := raw("EFC_DRY_RUN"):
        out["dry_run"] = _parse_env_bool("EFC_DRY_RUN", v)
    if v := raw("EFC_YES"):
        out["yes"] = _parse_env_bool("EFC_YES", v)
    if v := raw("EFC_MAX_BATCH"):
        try:
            n = int(v.strip())
        except ValueError as e:
            raise ConfigError(
                f"EFC_MAX_BATCH 的值无效: {v}（期望 1..{HARD_MAX_BATCH} 的整数）"
            ) from e
        if not 1 <= n <= HARD_MAX_BATCH:
            raise ConfigError(
                f"EFC_MAX_BATCH 的值无效: {v}（期望 1..{HARD_MAX_BATCH} 的整数）"
            )
        out["max_batch"] = n
    if v := raw("EFC_BACKUP_DIR"):
        out["backup_dir"] = v
    if v := raw("EFC_LOG_FILE"):
        out["log_file"] = v
    return out


def read_stdin_payload() -> dict[str, Any]:
    """读取 --stdin JSON 负载并校验 Schema。

    TTY 下使用 / 输入为空 / 非法 JSON / 非对象 / 未知键 / 类型不符 →
    ConfigError；值为 null 的键原样返回（merged() 按 None 不覆盖跳过）。
    """
    stdin = sys.stdin
    if stdin is None or stdin.isatty():
        raise ConfigError("--stdin 只接受管道输入，不能在交互终端（TTY）下使用")
    raw_text = stdin.read()
    if not raw_text.strip():
        raise ConfigError("--stdin 输入为空，需要 JSON 对象")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"--stdin 输入不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("--stdin 输入必须是 JSON 对象")
    for key, value in data.items():
        if key not in _STDIN_SCHEMA:
            raise ConfigError(f"--stdin 未知键: {key}")
        if value is None:
            continue
        expected = _STDIN_SCHEMA[key]
        if expected is list or key in _STDIN_LIST_STR_KEYS:
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ConfigError(f"--stdin 键 {key} 必须是字符串数组")
        elif expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"--stdin 键 {key} 必须是整数")
        elif not isinstance(value, expected):
            raise ConfigError(f"--stdin 键 {key} 必须是 {expected.__name__}")
    return data
