"""安全层（Spec §8/§12）：平台守卫、Windows UNC 拦截、保护根、卷根、高危判定、批量上限。

高危判定规则（满足任一即 high_risk，reason 给人话原因）：
a) target 是卷根/盘符根；
b) target 等于某系统保护根（含 extra）或 home 根；
c) target 位于某保护根之内（后代）——home 子树内的目标不因作为 home
   祖先的系统保护根（如 linux 的 /、/home）触发本条（用户目录是常规
   清理对象，home 子目录不自动高危）；
d) recursive=True 且 target 是某保护根的祖先。
所有路径比较一律 normcase 归一（posix 下恒等，无需分支）。
"""

import os
import sys
from pathlib import Path, PureWindowsPath

from efc.config import HARD_MAX_BATCH
from efc.exceptions import ConfigError, PlatformError
from efc.models import RiskDecision

SUPPORTED_PLATFORMS = frozenset({"win32", "darwin", "linux"})

_WIN_ENV_ROOTS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
_POSIX_ROOTS: dict[str, list[str]] = {
    "darwin": [
        "/System", "/Library", "/usr", "/bin", "/sbin", "/etc",
        "/private/etc", "/Applications",
    ],
    "linux": [
        "/", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc",
        "/var", "/boot", "/opt", "/root", "/home",
    ],
}


def _norm(p: Path) -> str:
    return os.path.normcase(os.fspath(p.expanduser().resolve()))


def _is_descendant(child: str, ancestor: str) -> bool:
    """child 是否位于 ancestor 之内（不含相等）；二者均为 normcase 归一后的绝对路径。"""
    if child == ancestor:
        return False
    prefix = ancestor if ancestor.endswith(("\\", "/")) else ancestor + os.sep
    return child.startswith(prefix)


def ensure_supported_platform() -> None:
    """平台守卫：win32/darwin/linux 放行，其余 PlatformError（exit 2）。"""
    if sys.platform not in SUPPORTED_PLATFORMS:
        raise PlatformError(
            f"不支持的平台: {sys.platform}（支持 Windows / macOS / Linux）"
        )


def is_unc(path: Path) -> bool:
    """Windows UNC 路径（\\\\server\\share）判定；posix 路径恒 False。"""
    return PureWindowsPath(os.fspath(path)).drive.startswith("\\")


def home_root() -> Path | None:
    """用户主目录；取不到返回 None。"""
    try:
        return Path.home()
    except (RuntimeError, OSError):
        return None


def protected_roots(extra: list[Path] | None = None) -> list[Path]:
    """平台内置保护根 + extra（high_risk_dirs）；展开/取不到跳过；resolve 归一并去重。"""
    roots: list[Path] = []
    if sys.platform == "win32":
        for var in _WIN_ENV_ROOTS:
            value = os.environ.get(var)
            if value:
                roots.append(Path(value).expanduser().resolve())
    else:
        for raw in _POSIX_ROOTS.get(sys.platform, []):
            p = Path(raw)
            if p.exists():
                roots.append(p.resolve())
    for item in extra or []:
        roots.append(Path(item).expanduser().resolve())
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = _norm(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def volume_root(path: Path) -> Path | None:
    """win：盘符根（如 C:\\）；posix：最近的 ismount 祖先（含 /）。"""
    if sys.platform == "win32":
        drive = PureWindowsPath(os.fspath(path)).drive
        if not drive or drive.startswith("\\"):
            return None
        return Path(drive + "\\")
    p = Path(os.path.abspath(os.fspath(path)))
    for candidate in (p, *p.parents):
        if os.path.ismount(candidate):
            return candidate
    return Path("/")


def assess_risk(
    target: Path, recursive: bool, extra: list[Path] | None = None
) -> RiskDecision:
    """按 §8 规则 a-d 评估 target（内部会 resolve + normcase）。"""
    resolved = target.expanduser().resolve()
    t = _norm(resolved)
    # a) 卷根/盘符根
    vr = volume_root(resolved)
    if vr is not None and _norm(vr) == t:
        return RiskDecision(True, f"目标目录是卷根/盘符根：{resolved}")
    roots = protected_roots(extra)
    home = home_root()
    home_n = _norm(home) if home is not None else None
    # b) 等于保护根或 home 根
    for r in roots:
        if t == _norm(r):
            return RiskDecision(True, f"目标目录是保护目录：{r}")
    if home_n is not None and t == home_n:
        return RiskDecision(True, "目标目录是用户主目录（home 根）")
    # c) 位于保护根之内；home 子树豁免 home 祖先保护根
    in_home = home_n is not None and _is_descendant(t, home_n)
    for r in roots:
        rn = _norm(r)
        if in_home and home_n is not None and (
            home_n == rn or _is_descendant(home_n, rn)
        ):
            continue  # home 子目录是常规清理对象
        if _is_descendant(t, rn):
            return RiskDecision(True, f"目标目录位于保护目录内：{r}")
    # d) recursive 且 target 是保护根的祖先
    if recursive:
        for r in roots:
            if _is_descendant(_norm(r), t):
                return RiskDecision(True, f"递归清理范围覆盖保护目录：{r}")
    return RiskDecision(False)


def validate_batch_size(n: int) -> None:
    """批量上限硬契约：not 1<=n<=10 → ConfigError（不钳制）。"""
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= HARD_MAX_BATCH:
        raise ConfigError(f"max_batch 必须在 1..{HARD_MAX_BATCH} 之间，当前为 {n!r}")
