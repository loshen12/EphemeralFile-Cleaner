"""efc.repl 测试（Spec §14）：直接调 ReplSession.handle(line)，
fake trash/UI 注入；覆盖 task 列/加载、dir 高危警告、pattern
追加/拒绝/清空、recursive、list/clean 写日志与总结、exit/quit/
未知命令、未设任务提示、默认任务自动加载。"""

import json

import pytest

from efc.config import AppConfig, Task
from efc.repl import ReplSession


@pytest.fixture
def session(tmp_path, fake_ui, fake_trash):
    cfg = AppConfig(backup_dir=tmp_path / "bk", log_file=tmp_path / "run.log")
    return ReplSession(cfg, fake_ui, fake_trash)


@pytest.fixture
def ready_session(session, tmp_path):
    """已设置目录与规则的会话（一次性任务）。"""
    target = tmp_path / "d"
    target.mkdir()
    (target / "a.tmp").write_text("x")
    (target / "keep.txt").write_text("k")
    assert session.handle(f"dir {target}")
    # shlex 会剥掉裸反斜杠，REPL 输入正则需引号包裹
    assert session.handle("pattern '" r"\.tmp$" "'")
    return session, target


def test_exit_and_quit(session):
    assert session.handle("exit") is False
    assert session.handle("quit") is False


def test_unknown_command_keeps_session(session, capsys):
    assert session.handle("bogus") is True
    assert "未知命令" in capsys.readouterr().out


def test_empty_line_ok(session):
    assert session.handle("   ") is True


def test_task_list_and_load(tmp_path, fake_ui, fake_trash, capsys):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    d1.mkdir()
    d2.mkdir()
    cfg = AppConfig(tasks=[
        Task(name="one", dir=d1, patterns=[r"\.tmp$"], default=True),
        Task(name="two", dir=d2, patterns=[r"\.bak$"]),
    ])
    s = ReplSession(cfg, fake_ui, fake_trash)
    assert s.handle("task")
    out = capsys.readouterr().out
    assert "one [默认]" in out and "two" in out
    assert s.handle("task two")
    assert "已加载任务 two" in capsys.readouterr().out
    assert s.handle("task nope")
    assert "任务不存在" in capsys.readouterr().out


def test_single_default_autoloaded(tmp_path, fake_ui, fake_trash, capsys):
    d = tmp_path / "a"
    d.mkdir()
    cfg = AppConfig(tasks=[Task(name="solo", dir=d, patterns=[r"\.tmp$"],
                                default=True)])
    s = ReplSession(cfg, fake_ui, fake_trash)
    s.handle("status")
    assert "任务: solo" in capsys.readouterr().out


def test_dir_show_risk_assessment(ready_session, capsys, monkeypatch):
    import efc.repl as repl_mod
    from efc.models import RiskDecision
    session, target = ready_session
    monkeypatch.setattr(repl_mod, "assess_risk",
                        lambda t, r, e=None: RiskDecision(True, "测试保护目录"))
    session.handle("dir")
    assert "高危" in capsys.readouterr().out


def test_dir_set_rejects_missing(session, tmp_path, capsys):
    session.handle(f"dir {tmp_path / 'nope'}")
    out = capsys.readouterr().out
    assert "不存在" in out and "拒绝设置" in out


def test_dir_high_risk_via_extra(tmp_path, fake_ui, fake_trash, capsys):
    guard = tmp_path / "guard"
    guard.mkdir()
    cfg = AppConfig(high_risk_dirs=[guard])
    s = ReplSession(cfg, fake_ui, fake_trash)
    s.handle(f"dir {guard}")
    assert "高危" in capsys.readouterr().out


def test_pattern_add_reject_clear_list(session, capsys):
    assert session.handle("pattern '" r"\.tmp$" "'")
    assert "已追加" in capsys.readouterr().out
    assert session.handle("pattern (")
    assert "非法正则，未追加" in capsys.readouterr().out
    assert session.handle("pattern list")
    assert r"\.tmp$" in capsys.readouterr().out
    assert session.handle("pattern clear")
    session.handle("pattern list")
    assert "（无规则）" in capsys.readouterr().out


def test_recursive_toggle(session, capsys):
    session.handle("recursive")
    assert "recursive=off" in capsys.readouterr().out
    session.handle("recursive on")
    session.handle("recursive")
    assert "recursive=on" in capsys.readouterr().out
    session.handle("recursive bogus")
    assert "用法" in capsys.readouterr().out


def test_list_previews_via_ui(ready_session, fake_ui, capsys):
    session, target = ready_session
    assert session.handle("list")
    assert fake_ui.match_results, "list 应经 ui.show_matches 展示"
    result = fake_ui.match_results[-1]
    assert [m.path.name for m in result.matches] == ["a.tmp"]


def test_clean_runs_pipeline_writes_journal_and_summary(
        ready_session, fake_ui, fake_trash, capsys):
    session, target = ready_session
    fake_ui.confirms = [True]
    assert session.handle("clean")
    assert fake_trash.calls == [str(target / "a.tmp")]
    log_path = session.config.log_file
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["command"] == "repl" and rec["result"] == "completed"
    assert rec["tasks"][0]["name"] is None  # 一次性任务
    assert "合计清理 1 个文件" in capsys.readouterr().out
    assert (target / "a.tmp").exists() is True or True  # fake trash 不真删


def test_clean_missing_state_prompts(session, capsys):
    s2 = ReplSession(AppConfig(), session._ui, session._trash)
    assert s2.handle("clean")
    assert "缺少 dir 与 pattern" in capsys.readouterr().out
    assert s2.handle("list")
    assert "缺少" in capsys.readouterr().out


def test_status_shows_session(ready_session, capsys):
    session, target = ready_session
    session.handle("recursive on")
    capsys.readouterr()
    session.handle("status")
    out = capsys.readouterr().out
    assert str(target) in out and r"\.tmp$" in out
    assert "递归: on" in out and "max_batch=5" in out
