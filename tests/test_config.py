"""efc.config 测试：默认值/加载/合并/保存与任务清单增删查（Spec §14）。"""

import json
from pathlib import Path

import pytest

from efc.config import (
    AppConfig,
    Task,
    add_task,
    default_tasks,
    list_tasks,
    load_config,
    merged,
    remove_task,
    resolve_task,
    save_config,
    validate,
)
from efc.exceptions import ConfigError


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """隔离 HOME，避免读到真实 ~/.efc/config.json。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_config(data: dict, name: str = "config.json") -> Path:
    path = Path(name)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ---------- 默认值与加载 ----------


def test_default_when_no_config():
    cfg = load_config()
    assert cfg == AppConfig()
    assert cfg.confirm is True and cfg.max_batch == 5
    assert cfg.backup_dir == Path(".efc-backup")
    assert cfg.tasks == []


def test_load_full_config():
    write_config({
        "tasks": [
            {"name": "downloads", "dir": "~/Downloads",
             "patterns": ["^~\\$", "\\.tmp$"], "recursive": True, "default": True},
            {"name": "tmp", "dir": ".", "patterns": ["\\.log$"]},
        ],
        "confirm": False, "max_batch": 3, "backup_enabled": False,
        "backup_dir": "~/.efc/backup", "ignore_case": False,
        "high_risk_dirs": ["~/important"], "log_enabled": False,
        "log_file": "~/run.log",
    })
    cfg = load_config()
    assert [t.name for t in cfg.tasks] == ["downloads", "tmp"]
    t0, t1 = cfg.tasks
    assert t0.dir == Path("~/Downloads").expanduser() and t0.default and t0.recursive
    assert t1.default is False and t1.recursive is False
    assert cfg.confirm is False and cfg.max_batch == 3
    assert cfg.backup_dir == Path("~/.efc/backup").expanduser().resolve()
    assert cfg.high_risk_dirs == [Path("~/important").expanduser()]
    assert cfg.log_file == Path("~/run.log").expanduser()


def test_load_explicit_missing_path(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.json")


def test_load_bad_json():
    Path("config.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config()


def test_load_top_level_not_object():
    Path("config.json").write_text("[1,2]", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config()


@pytest.mark.parametrize("key", ["target_dir", "filename_patterns", "recursive"])
def test_load_legacy_top_level_keys_rejected(key):
    write_config({key: "x"})
    with pytest.raises(ConfigError, match="v1.0"):
        load_config()


def test_load_unknown_top_level_key_rejected():
    write_config({"confirmm": True})
    with pytest.raises(ConfigError, match="未知配置键"):
        load_config()


def test_load_duplicate_task_names():
    write_config({"tasks": [
        {"name": "a", "dir": ".", "patterns": []},
        {"name": "a", "dir": ".", "patterns": []},
    ]})
    with pytest.raises(ConfigError, match="任务名重复"):
        load_config()


@pytest.mark.parametrize("bad", [0, 11, -1])
def test_load_max_batch_out_of_range(bad):
    write_config({"max_batch": bad})
    with pytest.raises(ConfigError, match="max_batch"):
        load_config()


@pytest.mark.parametrize("ok", [1, 10])
def test_load_max_batch_bounds_ok(ok):
    write_config({"max_batch": ok})
    assert load_config().max_batch == ok


def test_load_task_missing_required_fields():
    write_config({"tasks": [{"dir": ".", "patterns": []}]})
    with pytest.raises(ConfigError, match="name"):
        load_config()
    write_config({"tasks": [{"name": "a", "patterns": []}]})
    with pytest.raises(ConfigError, match="dir"):
        load_config()


def test_load_task_missing_patterns():
    write_config({"tasks": [{"name": "a", "dir": "."}]})
    with pytest.raises(ConfigError, match="patterns"):
        load_config()


def test_load_task_wrong_pattern_type():
    write_config({"tasks": [{"name": "a", "dir": ".", "patterns": [1]}]})
    with pytest.raises(ConfigError):
        load_config()


# ---------- 合并 ----------


def test_merged_applies_cli_namespace_keys(tmp_path):
    base = AppConfig()
    out = merged(base, {
        "dir": str(tmp_path), "patterns": ["^~\\$"], "recursive": True,
        "max_batch": 4, "backup_dir": "bk", "log_file": "run.log",
        "confirm": False, "backup_enabled": False, "ignore_case": False,
        "high_risk_dirs": ["hr"],
    })
    assert out.target_dir == tmp_path
    assert out.filename_patterns == ["^~\\$"]
    assert out.recursive is True and out.max_batch == 4
    assert out.backup_dir == (tmp_path / "bk").resolve()
    assert out.log_file == Path("run.log")
    assert out.confirm is False and out.backup_enabled is False
    assert out.ignore_case is False and out.high_risk_dirs == [Path("hr")]
    assert base.target_dir is None  # 原对象不被改动


def test_merged_none_does_not_override():
    out = merged(AppConfig(), {"max_batch": None, "dir": None, "patterns": None})
    assert out.max_batch == 5 and out.target_dir is None and out.filename_patterns == []


def test_merged_invalid_value_rejected():
    with pytest.raises(ConfigError):
        merged(AppConfig(), {"max_batch": 11})
    with pytest.raises(ConfigError):
        merged(AppConfig(), {"patterns": "not-a-list"})
    with pytest.raises(ConfigError):
        merged(AppConfig(), {"recursive": "yes"})


def test_merged_ignores_transport_keys():
    out = merged(AppConfig(), {"dry_run": True, "yes": True, "task": ["a"],
                               "format": "json", "no_log": True})
    assert out == AppConfig()


# ---------- 保存 ----------


def test_save_writes_only_persistent_fields(tmp_path):
    cfg = merged(AppConfig(), {"dir": str(tmp_path), "patterns": ["x"], "recursive": True})
    out_path = tmp_path / "out" / "config.json"
    save_config(cfg, out_path)
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(data) == {
        "tasks", "confirm", "max_batch", "backup_enabled", "backup_dir",
        "ignore_case", "high_risk_dirs", "log_enabled", "log_file",
    }
    assert not list((tmp_path / "out").glob("*.tmp"))  # 原子写：无临时文件残留


def test_save_round_trip(tmp_path):
    cfg = AppConfig()
    add_task(cfg, name="t", dir=str(tmp_path), patterns=[r"\.tmp$"], default=True)
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert [t.name for t in loaded.tasks] == ["t"]
    assert loaded.tasks[0].patterns == [r"\.tmp$"]
    assert loaded.tasks[0].default is True


def test_save_invalid_config_rejected(tmp_path):
    cfg = AppConfig(max_batch=11)
    with pytest.raises(ConfigError):
        save_config(cfg, tmp_path / "config.json")
    assert not (tmp_path / "config.json").exists()  # 校验失败不写盘


# ---------- 任务清单增删查 ----------


def test_add_task_create_requires_dir():
    with pytest.raises(ConfigError, match="dir"):
        add_task(AppConfig(), name="t")


def test_add_task_create_dir_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        add_task(AppConfig(), name="t", dir=tmp_path / "nope")


def test_add_task_create_and_update(tmp_path):
    cfg = AppConfig()
    add_task(cfg, name="t", dir=str(tmp_path), patterns=[r"\.tmp$"], recursive=True)
    # 同名更新：仅覆盖显式字段
    add_task(cfg, name="t", patterns=[r"\.tmp$", r"\.bak$"], default=True)
    t = resolve_task(cfg, "t")
    assert t.dir == tmp_path and t.recursive is True
    assert t.patterns == [r"\.tmp$", r"\.bak$"]  # 追加去重
    assert t.default is True


def test_add_task_replace_patterns(tmp_path):
    cfg = AppConfig()
    add_task(cfg, name="t", dir=str(tmp_path), patterns=[r"\.tmp$"])
    add_task(cfg, name="t", patterns=[r"\.bak$"], replace_patterns=True)
    assert resolve_task(cfg, "t").patterns == [r"\.bak$"]


def test_add_task_default_toggle(tmp_path):
    cfg = AppConfig()
    add_task(cfg, name="t", dir=str(tmp_path), default=True)
    add_task(cfg, name="t", default=False)
    assert resolve_task(cfg, "t").default is False
    add_task(cfg, name="t")  # default=None 不动标记
    assert resolve_task(cfg, "t").default is False


def test_add_task_bad_pattern_keeps_cfg_unchanged(tmp_path):
    cfg = AppConfig()
    add_task(cfg, name="t", dir=str(tmp_path), patterns=[r"\.tmp$"])
    snapshot = list_tasks(cfg)
    with pytest.raises(ConfigError, match="非法正则"):
        add_task(cfg, name="t", patterns=["("])
    assert list_tasks(cfg) == snapshot


def test_add_task_name_and_validate_failures(tmp_path):
    with pytest.raises(ConfigError):
        add_task(AppConfig(), name="  ")
    cfg = AppConfig()
    add_task(cfg, name="t", dir=str(tmp_path))
    # add_task 不允许制造重名（同名是更新），此处校验 validate 直接拒绝重名清单
    bad = AppConfig()
    bad.tasks = [Task(name="t", dir=tmp_path), Task(name="t", dir=tmp_path)]
    with pytest.raises(ConfigError, match="任务名重复"):
        validate(bad)


def test_remove_task_by_name_and_dir(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    cfg = AppConfig()
    add_task(cfg, name="a", dir=str(tmp_path / "a"))
    add_task(cfg, name="b", dir=str(tmp_path / "b"))
    assert remove_task(cfg, name="a") is True
    assert [t.name for t in list_tasks(cfg)] == ["b"]
    assert remove_task(cfg, name="a") is False
    assert remove_task(cfg, dir=str(tmp_path / "b")) is True
    assert list_tasks(cfg) == []


def test_remove_task_requires_exactly_one_criterion():
    with pytest.raises(ConfigError):
        remove_task(AppConfig())
    with pytest.raises(ConfigError):
        remove_task(AppConfig(), name="a", dir="b")


def test_resolve_task_unknown():
    with pytest.raises(ConfigError, match="任务不存在"):
        resolve_task(AppConfig(), "nope")


def test_default_tasks_order_and_filter(tmp_path):
    cfg = AppConfig()
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        add_task(cfg, name=name, dir=str(d), default=name != "a")
    assert [t.name for t in default_tasks(cfg)] == ["b", "c"]
    assert [t.name for t in list_tasks(cfg)] == ["a", "b", "c"]
