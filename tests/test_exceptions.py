"""efc.exceptions 测试：exit_code 类属性体系（Spec §3）。"""

import pytest

from efc.exceptions import (
    AbortError,
    ConfigError,
    EfcError,
    PatternError,
    PlatformError,
    ScanError,
)


@pytest.mark.parametrize(
    ("exc_cls", "code"),
    [
        (EfcError, 2),
        (ConfigError, 2),
        (PlatformError, 2),
        (PatternError, 2),
        (ScanError, 2),
        (AbortError, 3),
    ],
)
def test_exit_code_is_class_attribute(exc_cls: type[EfcError], code: int) -> None:
    # main() 统一 type(e).exit_code：类与实例类型两条路径都成立
    assert exc_cls.exit_code == code
    assert type(exc_cls("boom")).exit_code == code


@pytest.mark.parametrize(
    "exc_cls",
    [ConfigError, PlatformError, PatternError, ScanError, AbortError],
)
def test_hierarchy(exc_cls: type[EfcError]) -> None:
    assert issubclass(exc_cls, EfcError)
    assert isinstance(exc_cls("x"), Exception)


def test_message_preserved() -> None:
    assert str(AbortError("用户中止")) == "用户中止"
