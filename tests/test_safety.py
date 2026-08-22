"""efc.safety 测试：三平台守卫/保护根矩阵/卷根（含模拟挂载）/home 根规则/extra/
normcase/UNC/batch 边界（Spec §14，T009 验收）。"""

import os
import sys
from pathlib import Path

import pytest

import efc.safety as safety
from efc.exceptions import ConfigError, PlatformError


@pytest.fixture
def fake_roots(monkeypatch):
    """以受控假保护根列表替换 protected_roots，避免宿主差异。"""
    def install(roots: list[Path]):
        monkeypatch.setattr(safety, "protected_roots", lambda extra=None: list(roots))
    return install


@pytest.fixture
def fake_mounts(monkeypatch):
    def install(mounts: set[str]):
        monkeypatch.setattr(os.path, "ismount", lambda p: str(p) in mounts)
    return install


# ---------- 平台守卫 ----------


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_platform_guard_supported(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    safety.ensure_supported_platform()  # 不抛即通过


@pytest.mark.parametrize("platform", ["aix", "sunos5", "os2"])
def test_platform_guard_unsupported(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(PlatformError, match=platform):
        safety.ensure_supported_platform()


# ---------- UNC ----------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (Path("\\\\server\\share"), True),
        (Path("//server/share"), True),
        (Path("\\\\server\\share\\sub"), True),
        (Path("C:\\Users"), False),
        (Path("/mnt/net"), False),
        (Path("relative"), False),
    ],
)
def test_is_unc(path, expected):
    assert safety.is_unc(path) is expected


# ---------- 卷根 ----------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (Path("C:\\Users\\x"), Path("C:\\")),
        (Path("D:\\Data"), Path("D:\\")),
        (Path("\\\\server\\share"), None),  # UNC：流水线入口已拒，不归卷根
        (Path("relative"), None),
    ],
)
def test_volume_root_win32(monkeypatch, path, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    assert safety.volume_root(path) == expected


def test_volume_root_posix_nearest_mount(monkeypatch, fake_mounts):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_mounts({"/", "/mnt/net"})
    assert safety.volume_root(Path("/mnt/net/sub")) == Path("/mnt/net")
    assert safety.volume_root(Path("/a/b")) == Path("/")
    assert safety.volume_root(Path("/")) == Path("/")


# ---------- 保护根 ----------


def test_protected_roots_win32_env(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramData", raising=False)
    roots = safety.protected_roots([])
    assert Path("C:\\Windows").resolve() in roots
    assert Path("C:\\Program Files").resolve() in roots
    assert len(roots) == 2  # 取不到的环境变量跳过


def test_protected_roots_extra_appended_and_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    extra = tmp_path / "guard"
    extra.mkdir()
    roots = safety.protected_roots([extra, extra])
    assert extra.resolve() in roots
    assert roots.count(extra.resolve()) == 1  # normcase 去重


def test_protected_roots_posix_only_existing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    expected = {"/", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc",
                "/var", "/boot", "/opt", "/root", "/home"}
    roots = safety.protected_roots([])
    assert all(r.is_absolute() and r.exists() for r in roots)
    # 宿主符号链接会被 resolve（如 mac /var → /private/var），按 resolve 后形态比较
    resolved_expected = {str(Path(x).resolve()) for x in expected}
    assert {str(r) for r in roots} <= resolved_expected
    assert str(Path("/").resolve()) in {str(r) for r in roots}


# ---------- assess_risk 矩阵 ----------


def test_risk_equal_to_protected_root(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_roots([Path("/guard")])
    decision = safety.assess_risk(Path("/guard"), False, [])
    assert decision.high_risk and "保护目录" in decision.reason


def test_risk_descendant_of_protected_root(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_roots([Path("/guard")])
    assert safety.assess_risk(Path("/guard/sub/deep"), False, []).high_risk


def test_risk_normal_dir_not_high(monkeypatch, fake_roots, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_roots([Path("/guard")])
    assert not safety.assess_risk(tmp_path, False, []).high_risk


def test_risk_recursive_ancestor(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_roots([Path("/guard/usr")])
    assert safety.assess_risk(Path("/guard"), True, []).high_risk   # d) 递归覆盖保护目录
    assert not safety.assess_risk(Path("/guard"), False, []).high_risk


def test_risk_volume_root_posix(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_roots([])
    decision = safety.assess_risk(Path("/"), False, [])
    assert decision.high_risk and "卷根" in decision.reason


def test_risk_volume_root_win32_wiring(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "win32")
    fake_roots([])
    monkeypatch.setattr(safety, "volume_root", lambda p: p)  # 模拟盘符根命中
    assert safety.assess_risk(Path("/data"), False, []).high_risk


def test_risk_home_root_high_but_subdir_not(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_roots([])
    assert safety.assess_risk(Path.home(), False, []).high_risk
    assert not safety.assess_risk(Path.home() / "Downloads", False, []).high_risk


def test_risk_home_subtree_exempts_home_ancestors(monkeypatch):
    """linux 的 / 是 home 祖先保护根：home 子树不因此高危，home 之外仍受约束。"""
    monkeypatch.setattr(sys, "platform", "linux")
    assert not safety.assess_risk(Path.home() / "work", False, []).high_risk
    assert safety.assess_risk(Path("/tmp"), False, []).high_risk


def test_risk_extra_roots_participate(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    guard = tmp_path / "guard"
    guard.mkdir()
    assert safety.assess_risk(guard, False, [guard]).high_risk                # 等于 extra
    assert safety.assess_risk(guard / "sub", False, [guard]).high_risk        # 位于 extra 内


def test_risk_normcase_equality(monkeypatch, fake_roots):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())  # 模拟 Windows 折叠
    fake_roots([Path("/Guard")])
    assert safety.assess_risk(Path("/guard"), False, []).high_risk
    assert safety.assess_risk(Path("/Guard/Sub"), False, []).high_risk
    assert not safety.assess_risk(Path("/other"), False, []).high_risk


# ---------- 批量上限 ----------


@pytest.mark.parametrize("ok", [1, 5, 10])
def test_batch_size_bounds_ok(ok):
    safety.validate_batch_size(ok)  # 不抛即通过


@pytest.mark.parametrize("bad", [0, 11, -1, True])
def test_batch_size_out_of_range(bad):
    with pytest.raises(ConfigError, match="max_batch"):
        safety.validate_batch_size(bad)
