"""efc.cli 测试（Spec §14）：TestScan / TestClean / TestRepl（text 模式）与
main() 异常信封。CliRunner 驱动 app；退出码来自 _translate 的 typer.Exit；
错误信封（UsageError 等解析级错误）经 argv 直驱 cli.main() 验证。

trash 经 monkeypatch 替换 efc.cli.send2trash 注入 fake（不触真实回收站）；
CLI 测试一律 monkeypatch.chdir(tmp_path) 隔离根目录 config.json。
"""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import efc.cli as cli_mod
from efc.cli import app, main

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """隔离 CWD/HOME 与 EFC_* 环境变量，避免读到真实配置。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for var in ("EFC_CONFIG", "EFC_FORMAT", "EFC_NON_INTERACTIVE", "EFC_TASK",
                "EFC_DIR", "EFC_PATTERNS", "EFC_RECURSIVE", "EFC_DRY_RUN",
                "EFC_YES", "EFC_MAX_BATCH", "EFC_BACKUP_DIR", "EFC_LOG_FILE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture
def cli_trash(monkeypatch, fake_trash):
    monkeypatch.setattr(cli_mod, "send2trash", fake_trash)
    return fake_trash


def write_config(data: dict, name: str = "config.json") -> Path:
    path = Path(name)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def run_main(monkeypatch, args: list[str]) -> int:
    """argv 直驱真实入口 main()，返回退出码（解析级错误的信封走此路径）。"""
    monkeypatch.setattr(sys, "argv", ["efc", *args])
    try:
        main()
    except SystemExit as e:
        return e.code or 0
    return 0


def norm(text: str) -> str:
    """rich 表格按 80 列折行会打散中文短语，空白归一后再断言。"""
    return " ".join(text.split())


class TestScan:
    def test_one_off_text_table_to_stderr(self, tree):
        result = runner.invoke(app, ["scan", "--dir", str(tree),
                                     "--pattern", r"\.tmp$", "--recursive"])
        assert result.exit_code == 0
        # 默认 ignore_case=True：x.tmp/y.TMP/z.tmp 均命中；长路径单元格可能被 rich 截断
        assert "命中 3 个文件" in norm(result.stderr)
        assert r"\.tmp$" in norm(result.stderr)
        assert "命中" not in norm(result.stdout)  # 表格不走 stdout

    def test_one_off_json_task_null(self, tree):
        result = runner.invoke(app, ["--format", "json", "scan", "--dir", str(tree),
                                     "--pattern", r"\.tmp$", "--recursive"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]["tasks"]
        assert len(data) == 1 and data[0]["task"] is None
        assert data[0]["count"] == 3  # ignore_case=True：x.tmp/y.TMP/z.tmp
        assert data[0]["matches"][0]["relative"] == "b/c/z.tmp"  # 按 str(path) 排序
        assert "T" in data[0]["matches"][0]["mtime"]  # ISO 时间

    def test_task_selection_from_config(self, tree):
        write_config({"tasks": [
            {"name": "office", "dir": str(tree), "patterns": [r"^~\$"]},
        ]})
        result = runner.invoke(app, ["--format", "json", "scan", "--task", "office"])
        assert result.exit_code == 0
        tasks = json.loads(result.stdout)["data"]["tasks"]
        assert tasks[0]["task"] == "office" and tasks[0]["count"] == 1

    def test_unknown_task_name_exit_2(self):
        write_config({"tasks": [
            {"name": "a", "dir": ".", "patterns": [r"\.tmp$"]},
        ]})
        result = runner.invoke(app, ["scan", "--task", "nope"])
        assert result.exit_code == 2
        assert "任务不存在" in result.stderr

    def test_no_source_and_empty_defaults_exit_2(self):
        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 2
        assert "efc task add --default" in result.stderr

    def test_dir_and_task_mutually_exclusive(self, tree):
        write_config({"tasks": [
            {"name": "a", "dir": str(tree), "patterns": [r"\.tmp$"]},
        ]})
        result = runner.invoke(app, ["scan", "--dir", str(tree), "--task", "a"])
        assert result.exit_code == 2
        assert "互斥" in result.stderr or "不能与" in result.stderr

    def test_json_shorthand_equivalent(self, tree):
        r1 = runner.invoke(app, ["--format", "json", "scan", "--dir", str(tree),
                                 "--pattern", r"\.tmp$"])
        r2 = runner.invoke(app, ["scan", "--dir", str(tree),
                                 "--pattern", r"\.tmp$", "--json"])
        assert r1.exit_code == r2.exit_code == 0
        assert json.loads(r1.stdout) == json.loads(r2.stdout)
        # 同时出现以 --format 为准
        r3 = runner.invoke(app, ["--format", "text", "scan", "--dir", str(tree),
                                 "--pattern", r"\.tmp$", "--json"])
        assert "命中" in r3.stderr and r3.stdout == ""

    def test_default_list_used_when_no_args(self, tree):
        write_config({"tasks": [
            {"name": "d", "dir": str(tree), "patterns": [r"\.tmp$"],
             "recursive": True, "default": True},
            {"name": "x", "dir": str(tree), "patterns": [r"\.zz$"]},
        ]})
        result = runner.invoke(app, ["--format", "json", "scan"])
        tasks = json.loads(result.stdout)["data"]["tasks"]
        assert [t["task"] for t in tasks] == ["d"]  # 只有 default 进默认清单

    def test_pattern_override_replaces(self, tree):
        write_config({"tasks": [
            {"name": "a", "dir": str(tree), "patterns": [r"^~\$"],
             "recursive": True},
        ]})
        result = runner.invoke(app, ["--format", "json", "scan", "--task", "a",
                                     "--pattern", r"\.tmp$"])
        tasks = json.loads(result.stdout)["data"]["tasks"]
        assert tasks[0]["count"] == 3  # x.tmp/y.TMP/z.tmp，^~\$ 被整体替换


class TestClean:
    def test_yes_cleans_and_outputs_summary(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("xxx")
        (target / "keep.txt").write_text("k")
        result = runner.invoke(app, ["clean", "--dir", str(target),
                                     "--pattern", r"\.tmp$", "--yes"])
        assert result.exit_code == 0
        assert "合计清理 1 个文件" in norm(result.stdout)
        assert cli_trash.calls == [str(target / "a.tmp")]  # fake 记录删除意图
        assert (target / "keep.txt").exists()
        backup_dirs = list(Path(".efc-backup").iterdir())
        assert len(backup_dirs) == 1
        assert (backup_dirs[0] / "a.tmp").read_text() == "xxx"
        assert (backup_dirs[0] / "manifest.json").is_file()

    def test_dry_run_zero_trash(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        result = runner.invoke(app, ["clean", "--dir", str(target),
                                     "--pattern", r"\.tmp$", "--yes", "--dry-run"])
        assert result.exit_code == 0
        assert cli_trash.calls == []
        assert (target / "a.tmp").exists()
        assert not Path(".efc-backup").exists()

    def test_no_backup(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        result = runner.invoke(app, ["clean", "--dir", str(target),
                                     "--pattern", r"\.tmp$", "--yes", "--no-backup"])
        assert result.exit_code == 0
        assert cli_trash.calls and not Path(".efc-backup").exists()

    def test_no_log_and_default_log(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        r1 = runner.invoke(app, ["clean", "--dir", str(target),
                                 "--pattern", r"\.tmp$", "--yes", "--no-log"])
        assert r1.exit_code == 0 and not Path(".efc.log").exists()
        (target / "b.tmp").write_text("x")
        r2 = runner.invoke(app, ["clean", "--dir", str(target),
                                 "--pattern", r"\.tmp$", "--yes"])
        assert r2.exit_code == 0
        lines = Path(".efc.log").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1  # 单条记录
        rec = json.loads(lines[0])
        assert rec["command"] == "clean" and rec["result"] == "completed"

    def test_zero_matches_exit_0(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "keep.txt").write_text("k")
        result = runner.invoke(app, ["clean", "--dir", str(target),
                                     "--pattern", r"\.tmp$", "--yes"])
        assert result.exit_code == 0
        assert cli_trash.calls == []

    def test_max_batch_out_of_range_exit_2(self, tmp_path, cli_trash):
        result = runner.invoke(app, ["clean", "--dir", str(tmp_path),
                                     "--pattern", r"\.tmp$", "--yes",
                                     "--max-batch", "11"])
        assert result.exit_code == 2
        assert "max_batch" in result.stderr

    def test_default_list_runs_without_args(self, tmp_path, cli_trash):
        t1 = tmp_path / "one"
        t1.mkdir()
        (t1 / "a.tmp").write_text("x")
        t2 = tmp_path / "two"
        t2.mkdir()
        (t2 / "b.TMP").write_text("x")
        write_config({"tasks": [
            {"name": "one", "dir": str(t1), "patterns": [r"\.tmp$"], "default": True},
            {"name": "two", "dir": str(t2), "patterns": [r"\.tmp$"],
             "default": True, "recursive": False},
        ]})
        result = runner.invoke(app, ["clean", "--yes"])
        assert result.exit_code == 0
        assert sorted(cli_trash.calls) == [str(t1 / "a.tmp"), str(t2 / "b.TMP")]
        assert "等 2 条路径" in norm(result.stdout)  # 聚合总结分节

    def test_confirm_flow_via_stdin(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        r_yes = runner.invoke(app, ["clean", "--dir", str(target),
                                    "--pattern", r"\.tmp$"], input="y\n")
        assert r_yes.exit_code == 0 and len(cli_trash.calls) == 1
        (target / "a.tmp").write_text("x")
        r_no = runner.invoke(app, ["clean", "--dir", str(target),
                                   "--pattern", r"\.tmp$"], input="n\n")
        assert r_no.exit_code == 3  # aborted
        assert len(cli_trash.calls) == 1  # 拒绝后零新增删除

    def test_high_risk_yes_aborts(self, tmp_path, cli_trash):
        target = tmp_path / "guard"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        write_config({
            "tasks": [{"name": "g", "dir": str(target), "patterns": [r"\.tmp$"]}],
            "high_risk_dirs": [str(target)],
        })
        result = runner.invoke(app, ["clean", "--task", "g", "--yes"])
        assert result.exit_code == 3  # AutoUI 高危恒拒 → AbortError
        assert cli_trash.calls == []

    def test_missing_dir_exit_2(self, tmp_path, cli_trash):
        result = runner.invoke(app, ["clean", "--dir", str(tmp_path / "nope"),
                                     "--pattern", r"\.tmp$", "--yes"])
        assert result.exit_code == 2
        assert "不存在" in result.stderr


class TestRepl:
    def test_repl_enters_prompt_and_exits(self):
        result = runner.invoke(app, ["repl"], input="exit\n")
        assert result.exit_code == 0
        assert "efc>" in result.stdout
        assert "再见" in result.stdout

    def test_repl_rejects_agent_flags(self):
        for args in (["--format", "json", "repl"],
                     ["--non-interactive", "repl"],
                     ["--stdin", "repl"]):
            result = runner.invoke(app, args)
            assert result.exit_code == 2, args
            assert "repl" in result.stderr


class TestMainEnvelope:
    def test_usage_error_json_envelope(self, monkeypatch, capsys):
        code = run_main(monkeypatch, ["--format", "json", "no-such-cmd"])
        captured = capsys.readouterr()
        assert code == 2
        envelope = json.loads(captured.out)
        assert envelope["code"] == 2 and "msg" in envelope

    def test_usage_error_text_stderr(self, monkeypatch, capsys):
        code = run_main(monkeypatch, ["no-such-cmd"])
        captured = capsys.readouterr()
        assert code == 2 and "错误" in captured.err

    def test_unsupported_platform_exit_2(self, monkeypatch, capsys, tree):
        monkeypatch.setattr(sys, "platform", "aix")
        code = run_main(monkeypatch, ["scan", "--dir", str(tree),
                                      "--pattern", r"\.tmp$"])
        captured = capsys.readouterr()
        assert code == 2
        assert "不支持的平台" in captured.err

    def test_unexpected_exception_exit_1(self, monkeypatch, capsys, tree):
        def boom(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(cli_mod, "_gather", boom)
        code = run_main(monkeypatch, ["scan", "--dir", str(tree),
                                      "--pattern", r"\.tmp$"])
        captured = capsys.readouterr()
        assert code == 1
        assert "内部错误" in captured.err

    def test_format_json_help_still_human(self, monkeypatch, capsys):
        code = run_main(monkeypatch, ["--format", "json", "--help"])
        captured = capsys.readouterr()
        assert code == 0
        assert "Usage" in captured.out + captured.err
        assert "data" not in captured.out


class TtyStream:
    def isatty(self) -> bool:
        return True


class TestTask:
    def test_add_writes_config(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        result = runner.invoke(app, ["task", "add", "--name", "t",
                                     "--dir", str(d), "--pattern", r"\.tmp$",
                                     "--default"])
        assert result.exit_code == 0
        data = json.loads(Path("config.json").read_text(encoding="utf-8"))
        assert data["tasks"][0]["name"] == "t"
        assert data["tasks"][0]["default"] is True

    def test_add_update_dedup_and_replace(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        runner.invoke(app, ["task", "add", "--name", "t", "--dir", str(d),
                            "--pattern", r"\.tmp$"])
        runner.invoke(app, ["task", "add", "--name", "t",
                            "--pattern", r"\.tmp$", "--pattern", r"\.bak$"])
        r = runner.invoke(app, ["--format", "json", "task", "list"])
        tasks = json.loads(r.stdout)["data"]["tasks"]
        assert tasks[0]["patterns"] == [r"\.tmp$", r"\.bak$"]  # 追加去重
        runner.invoke(app, ["task", "add", "--name", "t",
                            "--pattern", r"^x", "--replace-patterns"])
        r = runner.invoke(app, ["--format", "json", "task", "list"])
        assert json.loads(r.stdout)["data"]["tasks"][0]["patterns"] == [r"^x"]

    def test_add_validation_failure_keeps_config(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        runner.invoke(app, ["task", "add", "--name", "t", "--dir", str(d),
                            "--pattern", r"\.tmp$"])
        before = Path("config.json").read_text(encoding="utf-8")
        r1 = runner.invoke(app, ["task", "add", "--name", "bad",
                                 "--dir", str(tmp_path / "nope")])
        r2 = runner.invoke(app, ["task", "add", "--name", "bad2", "--dir", str(d),
                                 "--pattern", "("])
        assert r1.exit_code == r2.exit_code == 2
        assert Path("config.json").read_text(encoding="utf-8") == before  # 不写盘

    def test_list_shows_default_marker(self, tmp_path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        runner.invoke(app, ["task", "add", "--name", "one", "--dir", str(d1),
                            "--pattern", "a", "--default"])
        runner.invoke(app, ["task", "add", "--name", "two", "--dir", str(d2),
                            "--pattern", "b"])
        result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0
        assert "one [默认]" in result.stdout and "two:" in result.stdout

    def test_list_json_envelope_full_config(self, tmp_path):
        result = runner.invoke(app, ["task", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]
        assert set(data) == {"tasks", "confirm", "max_batch", "backup_enabled",
                             "backup_dir", "ignore_case", "log_enabled",
                             "log_file", "high_risk_dirs"}
        assert data["max_batch"] == 5

    def test_remove_by_name_and_missing(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        runner.invoke(app, ["task", "add", "--name", "t", "--dir", str(d),
                            "--pattern", "p"])
        r = runner.invoke(app, ["--format", "json", "task", "remove", "--name", "t"])
        assert r.exit_code == 0 and json.loads(r.stdout)["data"]["removed"] is True
        r = runner.invoke(app, ["--format", "json", "task", "remove", "--name", "t"])
        assert json.loads(r.stdout)["data"]["removed"] is False
        assert json.loads(Path("config.json").read_text(encoding="utf-8"))["tasks"] == []

    def test_remove_requires_exactly_one(self):
        result = runner.invoke(app, ["task", "remove"])
        assert result.exit_code == 2


class TestPatterns:
    def test_patterns_all_and_single(self, tmp_path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        runner.invoke(app, ["task", "add", "--name", "one", "--dir", str(d1),
                            "--pattern", "p1"])
        runner.invoke(app, ["task", "add", "--name", "two", "--dir", str(d2),
                            "--pattern", "p2"])
        r_all = runner.invoke(app, ["patterns"])
        assert "one" in r_all.stdout and "two" in r_all.stdout
        r_one = runner.invoke(app, ["--format", "json", "patterns", "--task", "one"])
        tasks = json.loads(r_one.stdout)["data"]["tasks"]
        assert [t["task"] for t in tasks] == ["one"]
        assert tasks[0]["patterns"] == ["p1"]

    def test_patterns_unknown_task_exit_2(self):
        result = runner.invoke(app, ["patterns", "--task", "nope"])
        assert result.exit_code == 2

    def test_patterns_empty_list_exit_0(self):
        result = runner.invoke(app, ["patterns"])
        assert result.exit_code == 0
        assert "任务清单为空" in result.stdout
        r = runner.invoke(app, ["--format", "json", "patterns"])
        assert json.loads(r.stdout)["data"]["tasks"] == []


class TestAgent:
    def test_clean_json_envelope_structure(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("xx")
        result = runner.invoke(app, ["--format", "json", "clean", "--dir",
                                     str(target), "--pattern", r"\.tmp$",
                                     "--yes", "--no-log"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]
        assert set(data) >= {"command", "result", "exit_code", "duration_seconds",
                             "total_matched", "trashed", "failed", "aborted",
                             "backup_dir", "log_file", "summary", "tasks"}
        assert data["command"] == "clean" and data["result"] == "completed"
        assert data["trashed"] == 1 and data["tasks"][0]["files"][0]["status"] == "trashed"
        assert data["tasks"][0]["by_pattern"] == [
            {"pattern": r"\.tmp$", "files": 1, "bytes": 2}]

    def test_stdin_payload_drives_clean(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        payload = json.dumps({"dir": str(target), "patterns": [r"\.tmp$"],
                              "dry_run": True})
        result = runner.invoke(app, ["--format", "json", "--non-interactive",
                                     "--stdin", "clean"], input=payload)
        assert result.exit_code == 0
        data = json.loads(result.stdout)["data"]
        assert data["result"] == "dry_run" and cli_trash.calls == []

    def test_stdin_command_mismatch_exit_2(self, tmp_path):
        payload = json.dumps({"command": "scan", "dir": str(tmp_path),
                              "patterns": [r"\.tmp$"]})
        result = runner.invoke(app, ["--format", "json", "--non-interactive",
                                     "--stdin", "clean"], input=payload)
        assert result.exit_code == 2

    def test_stdin_tty_rejected(self, monkeypatch, capsys):
        # CliRunner 会替换 sys.stdin，TTY 场景经真实入口 main() 直驱
        monkeypatch.setattr(sys, "stdin", TtyStream())
        code = run_main(monkeypatch, ["--stdin", "scan", "--dir", ".",
                                      "--pattern", "x"])
        captured = capsys.readouterr()
        assert code == 2
        assert "TTY" in captured.err or "管道" in captured.err

    def test_non_interactive_high_risk_exit_3(self, tmp_path, cli_trash):
        target = tmp_path / "guard"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        write_config({
            "tasks": [{"name": "g", "dir": str(target), "patterns": [r"\.tmp$"]}],
            "high_risk_dirs": [str(target)],
        })
        result = runner.invoke(app, ["--non-interactive", "clean", "--task", "g"])
        assert result.exit_code == 3
        assert cli_trash.calls == []

    def test_json_requires_confirm_strategy(self, tmp_path):
        result = runner.invoke(app, ["--format", "json", "clean", "--dir",
                                     str(tmp_path), "--pattern", r"\.tmp$"])
        assert result.exit_code == 2
        assert "--yes" in result.stderr

    def test_env_priority_applies(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        (target / "b.tmp").write_text("x")
        import os as _os
        _os.environ["EFC_MAX_BATCH"] = "1"
        try:
            result = runner.invoke(app, ["clean", "--dir", str(target),
                                         "--pattern", r"\.tmp$", "--yes"])
            assert result.exit_code == 0
        finally:
            _os.environ.pop("EFC_MAX_BATCH", None)
        # 批大小 1：两文件两批（fake UI 自动确认批次）


class TestMultiTask:
    def test_task_flag_order_preserved(self, tmp_path, cli_trash):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "x.tmp").write_text("x")
        (b / "y.tmp").write_text("x")
        write_config({"tasks": [
            {"name": "A", "dir": str(a), "patterns": [r"\.tmp$"]},
            {"name": "B", "dir": str(b), "patterns": [r"\.tmp$"]},
        ]})
        result = runner.invoke(app, ["clean", "--task", "B", "--task", "A",
                                     "--yes", "--no-log"])
        assert result.exit_code == 0
        assert cli_trash.calls == [str(b / "y.tmp"), str(a / "x.tmp")]  # CLI 顺序

    def test_all_tasks_runs_everything(self, tmp_path, cli_trash):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "x.tmp").write_text("x")
        (b / "y.tmp").write_text("x")
        write_config({"tasks": [
            {"name": "A", "dir": str(a), "patterns": [r"\.tmp$"]},
            {"name": "B", "dir": str(b), "patterns": [r"\.tmp$"]},
        ]})
        result = runner.invoke(app, ["clean", "--all-tasks", "--yes"])
        assert result.exit_code == 0
        assert len(cli_trash.calls) == 2
        assert "等 2 条路径" in norm(result.stdout)

    def test_single_journal_record_for_all_tasks(self, tmp_path, cli_trash):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "x.tmp").write_text("x")
        (b / "y.tmp").write_text("x")
        write_config({"tasks": [
            {"name": "A", "dir": str(a), "patterns": [r"\.tmp$"]},
            {"name": "B", "dir": str(b), "patterns": [r"\.tmp$"]},
        ]})
        runner.invoke(app, ["clean", "--all-tasks", "--yes"])
        lines = Path(".efc.log").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1  # 仅一条
        rec = json.loads(lines[0])
        assert [t["name"] for t in rec["tasks"]] == ["A", "B"]  # 含全部任务

    @pytest.mark.parametrize("extra", [["--task", "A"], ["--all-tasks"]])
    def test_dir_mutually_exclusive_with_all(self, tmp_path, extra):
        a = tmp_path / "a"
        a.mkdir()
        write_config({"tasks": [
            {"name": "A", "dir": str(a), "patterns": [r"\.tmp$"]},
        ]})
        result = runner.invoke(app, ["clean", "--dir", str(a), *extra,
                                     "--pattern", r"\.tmp$", "--yes"])
        assert result.exit_code == 2

    def test_any_failure_exit_4(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        (target / "a.tmp").write_text("x")
        (target / "b.tmp").write_text("x")
        cli_trash.fail_names = {"b.tmp"}
        result = runner.invoke(app, ["clean", "--dir", str(target),
                                     "--pattern", r"\.tmp$", "--yes", "--no-log"])
        assert result.exit_code == 4
        assert len(cli_trash.calls) == 1

    def test_batch_refusal_aborts_exit_3(self, tmp_path, cli_trash):
        target = tmp_path / "d"
        target.mkdir()
        for i in range(7):
            (target / f"f{i}.tmp").write_text("x")
        result = runner.invoke(app, ["clean", "--dir", str(target),
                                     "--pattern", r"\.tmp$", "--max-batch", "3"],
                               input="y\nn\n")  # 首确认 y，批间 n
        assert result.exit_code == 3
        assert len(cli_trash.calls) == 3  # 第一批后停止
