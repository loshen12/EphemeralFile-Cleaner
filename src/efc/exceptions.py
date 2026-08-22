"""异常体系（Spec §3）。

EfcError 是全部可控错误的基类：exit_code 为类属性，子类按需覆盖；
cli.main() 统一以 type(e).exit_code 作为进程退出码。
退出码 4（执行期部分失败）不是异常，由 CleanOutcome.failed 聚合判定。
"""


class EfcError(Exception):
    """efc 可控错误基类（默认 exit 2）。"""

    exit_code: int = 2


class ConfigError(EfcError):
    """配置/用法/输入错误（exit 2）。"""


class PlatformError(EfcError):
    """不支持的平台 / Windows UNC 网络路径（exit 2）。"""


class PatternError(EfcError):
    """非法正则（exit 2）。"""


class ScanError(EfcError):
    """目标目录不存在等扫描前置错误（exit 2）。"""


class AbortError(EfcError):
    """用户中止 / 高危拦截 / 非交互需确认（exit 3）。"""

    exit_code: int = 3
