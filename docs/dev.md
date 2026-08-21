# EphemeralFile Cleaner 开发文档

> 版本：v1.0（2026-08-21）
> 交付物：一个可运行的 Python CLI 项目（包名 `efc`，命令名 `efc`），含 pytest 测试与 README。
> 本文档是唯一实现依据：下游编码模型按本文档实现，不自行更改安全规则、模块边界与公共 API。

---

## 1. 项目概述

一个 Windows 专用的一次性/临时文件清理 CLI 工具：给定目标目录与一组文件名正则，递归（可选）扫描，把**文件名匹配任一正则的文件**移入 **Windows 回收站**（且仅允许 `send2trash`，硬删除 API 全项目禁用）。删除前自动备份到本地备份目录并写入 manifest，高危目录需二次确认，单次执行按小批量（≤10）分批推进。每次 `clean` 结束后按目标目录/模式维度输出**清理总结**（数量/大小/用时），并把本次执行的详细信息（含**具体文件名**）追加写入**执行日志**。支持 **Agent 无头调用**：`--format json`（stdout 单行 JSON 结果、日志走 stderr）、`--non-interactive`（关交互/彩色/进度、执行完即退）、`--stdin`（JSON 批量输入，大输入走管道），并严格区分退出码；原有人类交互模式完全保留。

四个命令组：

| 命令 | 作用 |
|---|---|
| `efc scan` | 只读预览：列出将命中的文件（支持 `--json`） |
| `efc clean` | 执行清理：安全门 → 扫描 → 确认 → 备份 → 分批入回收站 |
| `efc repl` | 交互会话：`dir / pattern / list / clean / exit` 等命令 |
| `efc config add/list/remove` | 命令行维护 config.json：增加/查看/删除「目标目录 + 对应的一组文件名正则」目标条目 |
| `efc patterns` | 列举当前生效的文件名规则（正则列表）；支持 `--target`、`--json` |

## 2. 强制安全约束（红线，实现时不可妥协）

以下规则为**硬性约束**，任何代码路径不得绕过：

1. **仅 Win32 运行**：`sys.platform != "win32"` 时启动即报错退出（exit code 2），由 `safety.ensure_win32()` 统一实施，CLI 入口与 REPL 启动时各调用一次。
2. **回收站而非硬删除**：删除文件的唯一入口是 `send2trash.send2trash()`。**全项目禁止** `os.remove` / `os.unlink` / `pathlib.Path.unlink()` / `shutil.rmtree` / `os.rmdir` 等任何删除调用（备份用 `shutil.copy2` 是复制，不受限）。
3. **UNC/网络路径直接拒绝**：形如 `\\server\share\...` 的路径无法保证进入回收站，`safety.is_unc()` 检出即报错退出（exit 2）。
4. **盘符根目录一律高危**：`C:\`、`D:\` 等任何盘符根视为高危目录，走高危确认流程。
5. **高危目录二次确认**：目标目录为系统保护目录、位于保护目录之内、或（递归模式下）是保护目录的祖先（如 `C:\` 之于 `C:\Windows`）时，必须**交互式逐字符输入完整目标路径**才能继续；`--yes`、`confirm: false`、管道/非 TTY 环境**均不能**绕过（非交互环境遇到高危直接 abort，exit 3）。路径比对用 `os.path.normcase` 归一化，输入一次不匹配即中止。
6. **删前备份**：`backup_enabled` 默认 true。逐文件备份成功后才允许送回收站；某文件备份失败则**跳过该文件**（记入 manifest 的 `backup_failed`，不删除它），绝不"先删后补"。
7. **小批量 ≤ 10**：`max_batch` 取值范围 1..10，配置或 CLI 给出 >10 的值直接抛 `ConfigError`（不是钳制）。分批之间在交互模式下需确认"继续下一批"。
8. **只匹配文件，不动目录**：目录永不进入删除流程；清理后留下的空目录保持原样（不 rmdir）。
9. **不跟随符号链接/junction**：`os.walk(..., followlinks=False)`。
10. **默认确认**：`confirm` 默认 true；非 TTY 且未显式 `--yes` 时需要确认的场景直接 abort（fail-safe），绝不静默执行。
11. **Agent 契约（`--format json`）**：stdout 只允许一行结果 JSON（`{"data":...}` 或 `{"code":N,"msg":"..."}`），日志/警告一律走 stderr；**业务错误绝不返回 0**；`--non-interactive` 全程不得调用 `input()` 或阻塞等待（否则挂起）。

## 3. 技术选型与环境

- Python **>= 3.10**（使用 `X | None` 现代类型语法）
- 依赖：`typer[all]>=0.12`（含 rich，用于表格/彩色输出）、`send2trash>=1.8`
- 开发依赖：`pytest>=8.0`
- 开发工具（建议）：`mypy>=1.0` 静态类型检查、`ruff`（lint/format），并在 pyproject 中配置；类型错误不得合入。
- 打包：`pyproject.toml`（setuptools 或 hatchling 均可），入口点 `efc = efc.cli:main`
- 开发/运行平台：Windows（Git Bash / PowerShell / cmd 均可运行）

## 4. 项目结构

```
ephemeral-file-cleaner/
├── pyproject.toml
├── README.md
├── config.example.json
├── docs/
│   └── dev.md                 # 本文档
├── src/
│   └── efc/
│       ├── __init__.py        # __version__ = "1.0.0"
│       ├── exceptions.py      # EfcError 及子类
│       ├── models.py          # 数据类：FileMatch / ScanResult / CleanOutcome / RiskDecision
│       ├── config.py          # AppConfig/Target + 加载/合并/校验/保存 + 环境变量/--stdin 输入解析
│       ├── scanner.py         # 正则编译 + 目录扫描
│       ├── safety.py          # 平台守卫 / 高危判定 / UNC 拦截 / 批量校验
│       ├── backup.py          # 备份与 manifest
│       ├── ui.py              # ConsoleUI / AutoUI（UI 协议）
│       ├── cleaner.py         # 清理流水线（依赖注入 ui 与 trash 函数）
│       ├── summary.py         # 清理总结：按目标目录/模式聚合与渲染（纯函数）
│       ├── journal.py         # 执行日志：JSONL 追加写入每次 clean 的具体文件
│       ├── output.py          # Agent 响应：JSON 信封（data / code+msg）与退出码映射
│       ├── repl.py            # REPL 会话
│       └── cli.py             # Typer 入口：scan / clean / repl / config / patterns
└── tests/
    ├── conftest.py            # 临时目录树 fixture、fake_trash、FakeUI
    ├── test_config.py
    ├── test_input.py          # 环境变量与 --stdin 输入解析、优先级
    ├── test_scanner.py
    ├── test_safety.py
    ├── test_backup.py
    ├── test_cleaner.py
    ├── test_summary.py        # 清理总结聚合与渲染
    ├── test_journal.py        # 执行日志 JSONL 写入
    ├── test_output.py         # Agent 响应信封与退出码映射
    ├── test_cli.py
    └── test_repl.py
```

模块依赖方向（单向，禁止反向）：`cli/repl → cleaner, summary, journal, output`；`cleaner → scanner, safety, backup, ui`；`scanner/safety/backup/ui → config/models/exceptions`；`summary/journal/output → models/exceptions`。Cleaner **不依赖** summary/journal/output——后三者由 cli/repl 层消费 `CleanOutcome` 后调用。

## 5. 配置系统

### 5.1 config.json 完整 Schema

```json
{
  "targets": [
    {"name": "downloads", "dir": "D:\\Downloads",
     "patterns": ["^~\\$", "\\.tmp$", "\\.bak$", "^Thumbs\\.db$"],
     "recursive": true}
  ],
  "target_dir": "D:\\Downloads",
  "filename_patterns": ["^~\\$", "\\.tmp$"],
  "recursive": true,
  "confirm": true,
  "max_batch": 5,
  "backup_enabled": true,
  "backup_dir": "C:\\Users\\shen\\.efc\\backup",
  "ignore_case": true,
  "high_risk_dirs": [],
  "log_enabled": true,
  "log_file": ".efc.log"
}
```

字段说明：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `target_dir` | str | null | 目标目录；CLI `--dir` 优先 |
| `filename_patterns` | str[] | `[]` | 文件名正则列表，**任一命中即匹配**（OR） |
| `recursive` | bool | false | 是否递归子目录 |
| `confirm` | bool | true | 执行前确认 |
| `max_batch` | int | 5 | 每批删除文件数，**1..10，越界抛 ConfigError** |
| `backup_enabled` | bool | true | 删前备份 |
| `backup_dir` | str | `<cwd>/.efc-backup` | 备份根目录（每次运行建时间戳子目录） |
| `ignore_case` | bool | true | 正则是否忽略大小写（Windows 文件系统不区分大小写） |
| `high_risk_dirs` | str[] | `[]` | 追加的额外高危目录 |
| `log_enabled` | bool | true | 每次 clean 执行后写执行日志 |
| `log_file` | str | `.efc.log` | 执行日志路径（JSONL，追加写）；位于目标目录内时扫描自动排除 |

**`targets`：命名目标列表（新增，`efc config add` 的落盘目标）**

一个「目标」= 目标目录 + 对应的一组文件名正则。顶层 `target_dir`/`filename_patterns`/`recursive` 是名为 `"default"` 的默认目标（向后兼容保留），`targets` 数组用于管理多个命名目标：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `targets[].name` | str | 否 | 唯一标识；缺省 `"default"`（即顶层默认目标） |
| `targets[].dir` | str | 是 | 目标目录 |
| `targets[].patterns` | str[] | 是 | 该目录对应的一组文件名正则（任一命中即匹配） |
| `targets[].recursive` | bool | 否 | 缺省跟随顶层 `recursive` |

约定：`targets` 与顶层默认目标是**同一套数据**——`efc config add` 不带 `--name` 时写顶层字段（默认目标），带 `--name` 时写/更新 `targets` 条目；运行时按 §5.2 的目标解析规则合并为单一有效目标。

**冲突规则**：`targets` 中**不允许**出现名为 `"default"` 的条目——`config add --name default` 一律写入顶层字段，绝不产生 targets 条目；加载 config.json 时若同时存在顶层 `target_dir`/`filename_patterns` 和名为 `"default"` 的 targets 条目 → `ConfigError`（exit 2），避免数据歧义。

### 5.2 优先级与加载规则

优先级：**CLI 显式参数 > `--stdin` JSON > 环境变量（EFC_\*） > config.json > 内置默认值**。CLI 中显式传入的值覆盖配置；未传（None）则回落到配置；配置缺字段则用默认值。同键时高优先级源**整体覆盖**低优先级源（列表键也整体替换，不做合并）。

**目标解析规则（scan/clean/repl 共用）**：目标解析以「一个目标」为单位（目录 + 一组正则）；`scan`/`repl` 单目标执行，`clean` 支持一次执行多个目标（对每个目标循环调用本规则逐目标解析，见 §6.10）：

1. CLI 指定 `--target NAME` → 取 `targets` 中对应条目（不存在 → ConfigError），以其 `dir/patterns/recursive` 为基底；
2. 否则 CLI 指定 `--dir` → 以顶层默认目标的 `patterns/recursive` 为基底；
3. 否则用顶层默认目标（`target_dir`/`filename_patterns`/`recursive`）；
4. 均缺 → 报错 exit 2，提示用 `efc config add` 或 `--dir/--pattern` 补齐。

无论哪种基底，CLI 的 `--dir`/`--pattern`/`--recursive` 仍可逐个覆盖；解析结果最终写入 `AppConfig.target_dir/filename_patterns/recursive` 三个字段，下游模块只消费这三个字段。该解析逻辑由 `config.resolve_target()`（§5.3）统一实现，cli/repl/patterns 均调用它。

加载规则：

- `--config PATH` 指定配置文件；默认查找顺序：`$EFC_CONFIG` 环境变量 → `./config.json` → `~/.efc/config.json`；都不存在则用纯默认值（不算错误）。
- 指定的配置文件不存在 → `ConfigError`（exit 2）；JSON 解析失败 → `ConfigError` 并附解析错误信息。
- 加载时校验：`max_batch ∈ [1,10]`；`filename_patterns` 为非空字符串数组（允许空数组，运行时再报"无匹配模式"）；`backup_dir`、`target_dir` 存在时转 `Path.resolve()`。

### 5.3 公共 API

```python
# config.py
DEFAULT_MAX_BATCH = 5
HARD_MAX_BATCH = 10

@dataclass
class Target:
    name: str = "default"
    dir: Path | None = None
    patterns: list[str] = field(default_factory=list)
    recursive: bool | None = None      # None = 跟随顶层 recursive

@dataclass
class AppConfig:
    target_dir: Path | None = None
    filename_patterns: list[str] = field(default_factory=list)
    recursive: bool = False
    confirm: bool = True
    max_batch: int = DEFAULT_MAX_BATCH
    backup_enabled: bool = True
    backup_dir: Path = Path(".efc-backup")
    ignore_case: bool = True
    high_risk_dirs: list[Path] = field(default_factory=list)
    log_enabled: bool = True
    log_file: Path = Path(".efc.log")
    targets: list[Target] = field(default_factory=list)  # 仅 config 持久化层使用，Cleaner 不消费

    def validate(self) -> None: ...          # 抛 ConfigError（不校验 targets）

def load_config(path: Path | None = None) -> AppConfig: ...
def merged(base: AppConfig, overrides: dict) -> AppConfig:
    """overrides 中值为 None 的键不覆盖；覆盖后重新 validate()。"""

def save_config(cfg: AppConfig, path: Path) -> None: ...
    """写回 config.json：UTF-8、ensure_ascii=False、indent=2，保留 targets 与全部顶层字段；
    原子写入（临时文件 + os.replace）；写入失败抛 ConfigError。"""

def add_target(cfg: AppConfig, *, name: str, dir: Path,
               patterns: list[str], recursive: bool | None = None,
               replace_patterns: bool = False) -> None: ...
    """name=="default" 写顶层字段（target_dir/filename_patterns/recursive）；
    否则更新/新增 targets 条目（同名则更新）。
    patterns 默认『追加并去重』，replace_patterns=True 时整体替换。
    落盘前校验：dir 必须存在、每个正则必须可编译，然后对合并后的完整配置
    cfg.validate()；任一失败抛 ConfigError，调用方不得写盘。"""

def remove_target(cfg: AppConfig, *, name: str | None = None,
                  dir: Path | None = None) -> bool: ...
    """按 name 或 dir 定位并移除；name=="default" 或命中顶层默认目标时清空顶层
    target_dir/filename_patterns/recursive 字段。返回是否实际移除。"""

def list_targets(cfg: AppConfig) -> list[Target]: ...
    """顶层默认目标（若存在）并入 targets，按 name 排序；供 efc config list 与 repl status。"""

def resolve_target(cfg: AppConfig, name: str | None = None) -> Target: ...
    """§5.2 目标解析规则的实现：name 指定时取 targets 中对应条目（缺失抛 ConfigError）；
    否则返回顶层默认目标（flat 字段）。供 scan/clean/patterns/repl 共用。"""
```

### 5.4 输入来源（Agent 模式：CLI / 环境变量 / --stdin）

完整优先级：**CLI 显式参数 > `--stdin` JSON > 环境变量（EFC_\*） > config.json > 内置默认值**。同键时高优先级源整体覆盖低优先级（列表键也整体替换，不做合并）。

- **CLI**：既有各命令参数（§6.10 / §7.1）。
- **环境变量**（下表）：在 config 解析前读取，作为覆盖层。
- **`--stdin`**：布尔标志，从 stdin 读一段 JSON 作为业务参数。**大输入**（大量 patterns/targets）走管道/重定向，避免命令行长度与转义问题：`echo '{"patterns":[...]}' | efc --stdin clean`。TTY 下给出 `--stdin` 直接报错 exit 2（防止挂起等待）。
- **传输级标志** `--format`/`--non-interactive`/`--stdin` 只来自 CLI 与环境变量，**不来自** stdin 负载；负载只含业务参数。

环境变量表：

| 变量 | 对应参数 | 说明 |
|---|---|---|
| `EFC_CONFIG` | --config | 配置文件路径 |
| `EFC_FORMAT` | --format | `text` \| `json` |
| `EFC_NON_INTERACTIVE` | --non-interactive | `1`/`true` 启用 |
| `EFC_TARGET` | --target | 目标名列表，**换行**分隔 |
| `EFC_DIR` | --dir | 目标目录 |
| `EFC_PATTERNS` | --pattern | 正则列表，**换行**分隔 |
| `EFC_RECURSIVE` | --recursive | `1`/`0` |
| `EFC_DRY_RUN` | --dry-run | `1`/`0` |
| `EFC_YES` | --yes | `1`/`0` |
| `EFC_MAX_BATCH` | --max-batch | 1..10 |
| `EFC_BACKUP_DIR` | backup_dir | 备份根目录 |
| `EFC_LOG_FILE` | log_file | 执行日志路径 |

> 列表型环境变量（`EFC_TARGET`/`EFC_PATTERNS`）默认以**换行**（`\n`）分隔；因 Windows cmd 无法在环境变量中嵌入换行，同时支持以 `;`（分号）分隔（若正则本身含分号，请改用换行分隔或走 `--stdin`）。

**`--stdin` 负载 Schema**（未知键 → ConfigError exit 2，保证输入严格；`command` 缺省取 CLI 子命令）：

```json
{
  "command": "clean",
  "config": "path/to/config.json",
  "target": ["downloads"],
  "all_targets": true,
  "dir": "D:\\Downloads",
  "patterns": ["^~\\$", "\\.tmp$"],
  "recursive": true,
  "yes": true,
  "max_batch": 5,
  "backup_enabled": true,
  "backup_dir": "C:\\Users\\shen\\.efc\\backup",
  "dry_run": true,
  "no_backup": true,
  "no_log": true
}
```

`target`/`patterns` 为字符串数组，其余为对应标量；类型不符（如 patterns 不是数组）→ ConfigError。

公共 API 新增（config.py）：

```python
def read_env_overrides() -> dict: ...
    """解析全部 EFC_* 为覆盖字典；布尔/数值按上表转换，非法值抛 ConfigError。"""

def read_stdin_payload() -> dict: ...
    """读取并解析 stdin JSON（非 TTY 才允许）；未知键/类型不符抛 ConfigError。"""

def merge_overrides(cfg: AppConfig, *layers: dict) -> AppConfig:
    """按优先级依次叠加（列表键整体替换），每层后 validate()。"""
```

## 6. 核心模块设计

### 6.1 exceptions.py / models.py

```python
# exceptions.py
class EfcError(Exception):
    """基类：携带 exit_code（json 信封 code === 进程退出码）与用户可读消息。
    exit_code 是**类属性**，由子类显式覆盖（如 AbortError.exit_code = 3）；
    main() 统一用 type(exc).exit_code 读取，不依赖实例属性，避免混淆。"""
    exit_code: int = 2
class ConfigError(EfcError): ...          # exit_code=2
class PlatformError(EfcError): ...        # exit_code=2（非 win32 / UNC 路径）
class PatternError(EfcError): ...         # exit_code=2（非法正则）
class ScanError(EfcError): ...            # exit_code=2（目标目录不存在等）
class AbortError(EfcError): ...           # exit_code=3（用户拒绝 / 高危拦截 / 非交互需确认）
```

```python
# models.py
@dataclass
class FileMatch:
    path: Path          # 绝对路径
    relative: str       # 相对 target_dir 的 posix 风格路径
    size: int           # 字节
    mtime: float        # os.path.getmtime
    pattern: str        # 命中的第一个模式（按模式汇总的唯一归属）

@dataclass
class ScanResult:
    root: Path
    recursive: bool
    matches: list[FileMatch]   # 按 str(path) 排序，保证确定性
    scanned_dirs: int

@dataclass
class RiskDecision:
    high_risk: bool
    reason: str | None = None   # 高危原因，用于提示与测试断言

@dataclass
class FileOutcome:
    path: Path
    status: str                  # "trashed" | "backup_failed" | "trash_failed"
    backup_path: Path | None = None
    error: str | None = None
    size: int = 0                # 文件字节数（供总结/日志聚合）
    pattern: str | None = None   # 命中的第一个模式（供按模式汇总）

@dataclass
class CleanOutcome:
    total_matched: int
    results: list[FileOutcome]
    batches: int
    backup_dir: Path | None      # 本次运行备份目录（含 manifest）
    aborted: bool = False        # 用户中止（未删任何文件时 results 为空）
    target_name: str = "default" # 目标名（供总结/日志）
    target_dir: Path | None = None   # 目标目录（供总结/日志）
    duration_seconds: float = 0.0    # 本次流水线用时
    total_bytes: int = 0             # 已入回收站（trashed）文件合计字节

    @property
    def trashed(self) -> list[FileOutcome]: ...
    @property
    def failed(self) -> list[FileOutcome]: ...   # backup_failed + trash_failed
```

### 6.2 scanner.py —— 扫描与正则匹配

```python
def compile_patterns(patterns: list[str], ignore_case: bool) -> list[re.Pattern]:
    """re.compile 每个模式；任一非法抛 PatternError（消息含该模式与 re 的错误）。"""

def scan(root: Path, patterns: list[re.Pattern], recursive: bool,
         exclude: list[Path] | None = None) -> ScanResult:
    """只读扫描。recursive=True 用 os.walk(root, followlinks=False)；
    False 用 root.iterdir() 只看当前层。仅收集 is_file() 的文件，
    对 file.name 做搜索，**记录命中的第一个模式**写入 FileMatch.pattern
    （保证按模式汇总时每文件唯一归属）。exclude 中的目录（按 resolve 后
    normcase 比较）整棵跳过（用于排除备份目录/日志文件自身）。"""
```

要点：
- 匹配对象是**文件名**（`file.name`），不是全路径；语义为 `re.search`（子串匹配），文档与 README 中说明。
- `ignore_case=True` 时编译加 `re.IGNORECASE`。
- 目标目录不存在或不是目录 → `ScanError`；无权限遍历的子目录跳过并计数（`scanned_dirs` 仍正常）。

### 6.3 safety.py —— 平台与高危守卫

```python
def ensure_win32() -> None:
    """sys.platform != 'win32' 抛 PlatformError('本工具仅支持 Windows (win32)')。"""

def is_unc(path: Path) -> bool:
    """PureWindowsPath(path).drive 以 '\\\\' 开头即 UNC。"""

def protected_roots(extra: list[Path]) -> list[Path]:
    """内置保护根（展开环境变量并 resolve）：
    %SystemRoot%（默认 C:\\Windows）、%ProgramFiles%、%ProgramFiles(x86)%、
    %ProgramData%、%USERPROFILE%，加上 extra。"""

def drive_root(path: Path) -> Path | None: ...

def assess_risk(target: Path, recursive: bool, extra: list[Path]) -> RiskDecision:
    """高危判定，target 需已 resolve()。满足任一条即 high_risk=True：
    a) target 是任一盘符根（如 C:\\）；
    b) target 等于某个保护根（normcase 比对）；
    c) target 位于某个保护根之内（是其后代）；
    d) recursive=True 且 target 是某个保护根的祖先（如 C:\\ 包含 C:\\Windows）。
    reason 给出人话原因，如 '目标位于系统目录 C:\\Windows 内'。"""

def validate_batch_size(n: int) -> None:
    """not 1 <= n <= HARD_MAX_BATCH(=10) 抛 ConfigError。"""
```

内置保护根从环境变量取（`os.environ.expand`），取不到的环境变量跳过该项。所有路径比较一律 `os.path.normcase(os.fspath(p))` 后进行。

### 6.4 backup.py —— 删前备份

```python
class BackupRun:
    def __init__(self, base_dir: Path): ...   # base_dir/<YYYYmmdd-HHMMSS.fff>/
    @property
    def root(self) -> Path: ...
    def backup_file(self, src: Path, relative: str) -> Path:
        """shutil.copy2(src, root/relative)，parents=True；返回备份路径。
        任何异常向上抛（由 cleaner 捕获转 FileOutcome(status='backup_failed')）。"""
    def write_manifest(self, outcomes: list[FileOutcome], meta: dict) -> Path:
        """写 manifest.json：{run_at, target_dir, patterns, recursive,
        results:[{original, backup, status, size, error}]}，UTF-8、indent=2。"""

def new_run(base_dir: Path) -> BackupRun: ...
```

要点：备份保留相对目录结构（`a/b/x.tmp` → `<run>/a/b/x.tmp`）；manifest 是唯一的审计记录，恢复操作本期不实现（README 中注明可用资源管理器手工从备份目录拷回，并把 `efc restore`（按 manifest 恢复）列入 Roadmap 后续版本）。

### 6.5 ui.py —— UI 协议（依赖注入，测试免 mock input）

```python
class UI(Protocol):
    def confirm(self, message: str) -> bool: ...
    def confirm_high_risk(self, path: Path, reason: str) -> bool: ...
    def confirm_next_batch(self, done: int, total: int) -> bool: ...
    def show_matches(self, result: ScanResult) -> None: ...
    def show_summary(self, outcome: CleanOutcome) -> None: ...
    def error(self, message: str) -> None: ...

class ConsoleUI:
    """基于 typer.confirm / rich 的实现。confirm_high_risk：
    打印原因，要求用户逐字符输入 normcase 后的完整路径，一次不匹配返回 False。
    支持 `no_color`/`progress`/`interactive` 开关：`--format json` 时 `no_color=True`、
    `progress=False`，所有人读输出走 stderr（stdout 留给 JSON 信封）。"""

class AutoUI:
    """--yes / 测试用：confirm/confirm_next_batch 恒 True；
    confirm_high_risk 恒 False（高危永不被自动放行）。"""
```

### 6.6 cleaner.py —— 清理流水线

```python
class Cleaner:
    """只处理一个已解析的目标：config.target_dir / config.filename_patterns
    必须在构造前由 cli/repl 层按 §5.2 目标解析规则就绪；targets 列表的解析不在本类职责内。"""

    def __init__(self, config: AppConfig, ui: UI,
                 trash: Callable[[str], None] = send2trash): ...

    def run(self) -> CleanOutcome:
        """完整流水线（见 §9），任何 gate 失败抛 AbortError/相应异常，
        已开始执行后不回滚（回收站本身即软删除兜底）。"""
```

流水线步骤（`run()` 内部顺序，供实现与测试对照）：

1. 记录开始时间 `time.perf_counter()`。
2. `ensure_win32()`；`is_unc(target)` 检查。
3. 目标目录存在性检查（`ScanError`）。
4. `assess_risk()` → 若高危：`ui.confirm_high_risk()` 返回 False 即抛 `AbortError`。
5. `compile_patterns()`；`scan(exclude=[backup_dir, log_file])`——备份目录与日志文件若位于目标目录内则整棵跳过（防止清理到自己的备份/日志）。
6. 零命中：打印提示，返回空 `CleanOutcome`（exit 0）。
7. `ui.show_matches()`；若 `confirm`：`ui.confirm("确认将 N 个文件移入回收站?")` 拒绝则 abort（aborted=True）。
8. 若 `backup_enabled`：先做**备份磁盘空间预检**——汇总所有匹配文件的总字节，用 `shutil.disk_usage(backup_dir)` 检查可用空间，若可用 < 总字节 × 1.05 → `AbortError`（exit 3）并提示，**绝不先删后失败**；预检通过后再 `backup.new_run()`。
9. 分批循环（每批 ≤ `max_batch`）：
   - 批间（非首批）交互模式下 `ui.confirm_next_batch()`，拒绝则停止（已删的不回滚，aborted 部分标记在 outcome：`aborted=True`）。
   - 批内逐文件：备份（失败 → 记 `backup_failed`，跳过）→ `trash(str(path))`（异常 → 记 `trash_failed`，继续下一个）；每个 `FileOutcome` 携带 `size` 与 `pattern`（取自对应 `FileMatch`）。
10. `write_manifest()`；记录结束时间，把 `duration_seconds`、`total_bytes`（仅 trashed 文件合计）写入 outcome；`ui.show_summary()`；返回 outcome。

`trash` 作为构造函数参数注入：生产传 `send2trash`，测试传 fake（记录调用并可选择性真的把文件移走来模拟）。

### 6.7 summary.py —— 清理总结（纯函数，可测）

按「目标目录 → 模式」两个维度聚合每次清理的实际结果，并渲染为用户可见的总结文本。**只统计 `status == "trashed"` 的文件**；失败文件单独计数。

```python
@dataclass
class PatternStats:
    pattern: str
    files: int
    bytes: int

@dataclass
class TargetStats:
    name: str
    dir: Path
    files: int
    bytes: int
    by_pattern: list[PatternStats]     # 组内按命中模式分组，保持命中顺序

@dataclass
class RunSummary:
    targets: list[TargetStats]
    total_files: int
    total_bytes: int
    duration_seconds: float
    failed_files: int                  # trashed 之外的所有失败文件数

def build_summary(outcomes: list[CleanOutcome]) -> RunSummary: ...
    """按传入顺序聚合多个 outcome；同一目标目录（normcase 比较）只出现一次，
    name 取首个。文件按 FileOutcome.pattern 归组；pattern 为 None 的归入 "(无模式)"。"""

def format_bytes(n: int) -> str: ...
    """B/KB/MB/GB 自适应，2 位小数（B 取整）。"""

def format_duration(sec: float) -> str: ...
    """<60 秒显示 'N 秒'，否则 'N.N min'（1 位小数）。"""

def render_summary(s: RunSummary) -> str: ...
    """渲染为 §7.7 示例格式；targets 为空时返回空串（调用方打印替代提示）。"""
```

### 6.8 journal.py —— 执行日志（JSONL 追加写）

记录**每次执行命令行完成的工作**及**具体文件名**，供事后审计。每次 CLI 执行（或 repl 的 `clean` 命令）追加**一条** JSONL 记录。

```python
@dataclass
class JFile:
    path: str                # 绝对路径（具体文件名）
    size: int
    pattern: str | None
    status: str              # trashed | backup_failed | trash_failed

@dataclass
class JTarget:
    name: str
    dir: str
    files: list[JFile]

@dataclass
class JournalRecord:
    ts: str                  # ISO8601 毫秒
    command: str             # "clean"
    dry_run: bool
    result: str              # completed | partial | aborted | dry_run
    duration_seconds: float
    targets: list[JTarget]

class ExecutionLog:
    def __init__(self, path: Path): ...
    def record(self, rec: JournalRecord) -> None: ...
        """open(path, 'a', encoding='utf-8') 追加一行 JSON（ensure_ascii=False）。
        写失败（只读/占用）仅 stderr 警告，不抛异常、不影响退出码。"""
```

`result` 判定优先级（跨目标汇总）：任一目标 `aborted` → `aborted`；否则存在失败文件 → `partial`；否则 `dry_run` → `dry_run`；否则 `completed`。

### 6.9 repl.py —— 交互会话

```python
class ReplSession:
    def __init__(self, config: AppConfig, ui: UI,
                 trash: Callable[[str], None] = send2trash): ...

    def handle(self, line: str) -> bool:
        """解析并执行一行命令；返回 False 表示应退出会话。
        语法解析用 shlex.split（路径可含空格时提示用户加引号）。"""

    def run(self) -> None:
        """循环读取 input()（prompt='efc> '）调 handle()；
        EOFError / KeyboardInterrupt 优雅退出。"""
```

命令表（动词大小写不敏感；`handle` 是纯函数式入口，测试直接调用它，无需 mock input）：

| 命令 | 参数 | 行为 |
|---|---|---|
| `dir` | `[PATH]` | 无参：显示当前目标目录与高危评估结果；有参：设置目标（resolve、存在性检查、UNC/高危即时报错提示但不中止会话） |
| `pattern` | `[REGEX \| clear \| list]` | 无参/list：列出当前模式；`clear`：清空；否则追加一个正则（即时编译校验，非法则提示不追加） |
| `recursive` | `[on\|off]` | 查看/切换递归开关 |
| `list` | — | 用当前 dir+patterns+recursive 执行 `scan` 并 `ui.show_matches()`（只读） |
| `clean` | — | 用当前状态构造 `Cleaner.run()`（同 §6.6 全套安全门；dir 或 patterns 未设置时提示缺什么）；结束后同样调用 `ExecutionLog.record()` 写执行日志并输出清理总结，与 CLI 行为一致 |
| `status` | — | 汇总显示 dir/patterns/recursive/confirm/max_batch/backup 设置 |
| `help` | — | 显示命令表 |
| `exit` / `quit` | — | 返回 False 退出（EOF 同） |

REPL 内的 `clean` 与 CLI `efc clean` 走**同一条** Cleaner 流水线，禁止复制粘贴第二套逻辑。

### 6.10 cli.py —— Typer 入口

```python
app = typer.Typer(help="EphemeralFile Cleaner — 临时文件清理（回收站安全删除）",
                  no_args_is_help=True)

@dataclass
class AgentState:
    format: str = "text"            # text | json
    non_interactive: bool = False
    stdin: bool = False

@app.callback()
def main_options(ctx: typer.Context,
                 format: str = typer.Option("text", "--format", help="输出格式：text|json"),
                 non_interactive: bool = typer.Option(False, "--non-interactive",
                                                      help="非交互：关闭确认/彩色/进度条，执行完直接退出不阻塞"),
                 stdin: bool = typer.Option(False, "--stdin", help="从 stdin 读取 JSON 业务参数（大输入用）"),
                 ) -> None:
    """传输级（全局）选项，须位于子命令之前：efc --format json --non-interactive clean ..."""
    ctx.obj = AgentState(format=format, non_interactive=non_interactive, stdin=stdin)
```

各命令经 `ctx.obj` 读取 AgentState；`--format json` 时，所有人类可读输出（表格、进度、提示）走 stderr 或静默，命令完成处调用 `output.emit_success(data)` 输出最终 JSON 信封到 stdout（text 模式不变）。`--format json` 自动强制 `ConsoleUI(no_color=True, progress=False)`，即使未显式传 `--non-interactive`。`--non-interactive` 额外关闭交互确认

@app.command()
def scan(target: Optional[str] = typer.Option(None, "--target"),
         dir: Optional[Path] = typer.Option(None, "--dir"),
         pattern: list[str] = typer.Option([], "--pattern"),
         recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive"),
         config_file: Optional[Path] = typer.Option(None, "--config"),
         json_out: bool = typer.Option(False, "--json"),
         verbose: bool = typer.Option(False, "--verbose", "-v")) -> None: ...

@app.command()
def clean(target: list[str] = typer.Option([], "--target", help="可重复：一次清理多个命名目标"),
          all_targets: bool = typer.Option(False, "--all-targets", help="清理 config 中全部目标"),
          dir: Optional[Path] = typer.Option(None, "--dir"),
          pattern: list[str] = typer.Option([], "--pattern"),
          recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive"),
          config_file: Optional[Path] = typer.Option(None, "--config"),
          yes: bool = typer.Option(False, "--yes", help="跳过普通确认（不能绕过高危确认）"),
          no_backup: bool = typer.Option(False, "--no-backup"),
          max_batch: Optional[int] = typer.Option(None, "--max-batch"),
          dry_run: bool = typer.Option(False, "--dry-run"),
          no_log: bool = typer.Option(False, "--no-log", help="本次不写执行日志"),
          verbose: bool = ...) -> None: ...

@app.command()
def repl(config_file: Optional[Path] = typer.Option(None, "--config"),
         verbose: bool = ...) -> None: ...

# ---- config 子命令组：命令行添加/管理 config.json 中的目标配置 ----
config_app = typer.Typer(help="维护 config.json：增加/查看/删除目标目录与文件名正则")
app.add_typer(config_app, name="config")

@config_app.command("add")
def config_add(dir: Path = typer.Option(..., "--dir", help="目标目录（必须存在）"),
               pattern: list[str] = typer.Option([], "--pattern", help="可重复：该目录对应的一组文件名正则"),
               name: str = typer.Option("default", "--name", help="目标名；缺省 default（写顶层字段）"),
               recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive"),
               replace_patterns: bool = typer.Option(False, "--replace-patterns",
                                                     help="整体替换该目标的模式，而非追加"),
               config_file: Optional[Path] = typer.Option(None, "--config")) -> None: ...

@config_app.command("list")
def config_list(config_file: Optional[Path] = typer.Option(None, "--config"),
                json_out: bool = typer.Option(False, "--json")) -> None: ...

@config_app.command("remove")
def config_remove(name: Optional[str] = typer.Option(None, "--name"),
                  dir: Optional[Path] = typer.Option(None, "--dir"),
                  config_file: Optional[Path] = typer.Option(None, "--config")) -> None: ...

@app.command()
def patterns(target: Optional[str] = typer.Option(None, "--target"),
             config_file: Optional[Path] = typer.Option(None, "--config"),
             json_out: bool = typer.Option(False, "--json")) -> None:
    """列举当前生效的文件名规则（正则列表）；目标解析同 §5.2，只读不改配置。"""

def _resolve_format() -> str:
    """在 app() 之前独立解析 format，**不能依赖 callback**——callback 在 typer 解析子命令时才执行，
    UsageError 等异常场景下可能未执行。优先扫描 sys.argv 中的 --format/--json，其次 EFC_FORMAT，默认 text。"""

def main() -> None:
    """入口点。text 模式：错误 → stderr 一行 '错误: ...' + sys.exit(code)；
    json 模式：错误 → stdout 一行 {"code":N,"msg":"..."} + 同样退出码；
    未预期异常统一按 code 1 处理（业务报错绝不返回 0）。"""
    fmt = _resolve_format()
    try:
        app()
    except click.UsageError as e:            # Typer/Click 用法错误
        if fmt == "json":
            output.emit_error(2, str(e))
        else:
            print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)
    except EfcError as e:
        if fmt == "json":
            output.emit_error(type(e).exit_code, str(e))
        else:
            print(f"错误: {e}", file=sys.stderr)
        sys.exit(type(e).exit_code)
    except Exception as e:                   # 未预期内部错误
        if fmt == "json":
            output.emit_error(1, f"内部错误: {e}")
        else:
            print(f"内部错误: {e}", file=sys.stderr)
        sys.exit(1)
```
```

`scan` 与 `clean --dry-run` 行为一致（只读）；`--no-backup` 会把 `backup_enabled` 置 False（红线 6 仅约束"开启备份时先备份"，`--no-backup` 是显式弃权，README 中用醒目警告标注）。

**多目标 clean（clean 特有）**：`--target` 可重复出现，或加 `--all-targets` 清理 config 中全部目标；`--dir` 与 `--target`/`--all-targets` 互斥（同用 exit 2）。每个目标独立走完整流水线（各自安全门/确认/备份/分批），按 `--target` 列表或配置顺序执行；全部结束后：
- 汇总所有 `CleanOutcome` → `build_summary()` → 打印清理总结（§7.7）；
- 若 `log_enabled` 且未 `--no-log` → `ExecutionLog.record()` 写**一条**日志（dry-run 也写，result=dry_run）；
- 退出码：任一目标有失败文件 → 4；否则任一目标 aborted → 3；否则 0。
`scan` 保持单目标预览（不聚合、不写日志）。`repl` 仅支持人类交互：`--format json` / `--non-interactive` / `--stdin` 任一带上 → ConfigError（exit 2）。

### 6.11 output.py —— Agent 响应与退出码映射

负责 `--format json` 的响应契约：stdout 只输出一行结果 JSON，错误映射为稳定退出码。

```python
def emit_success(data: dict) -> None:
    """print(json.dumps({"data": data}, ensure_ascii=False), file=sys.stdout) 单行、UTF-8。"""

def emit_error(code: int, msg: str) -> None:
    """print(json.dumps({"code": code, "msg": msg}, ensure_ascii=False), file=sys.stdout)"""

def exit_code_for(exc: BaseException) -> int:
    """EfcError → 其 exit_code；click.UsageError → 2；其余异常 → 1。"""
```

退出码是硬契约（§7.4）：json 信封的 `code` 字段 === 进程退出码。

## 7. CLI 命令设计（用户视角）

### 7.1 用法总览

```
efc [--format text|json] [--non-interactive] [--stdin] <command> ...   # 全局选项位于子命令之前
efc --version
efc scan  [--target NAME] [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive] [--config PATH] [--json] [-v]
efc clean [--target NAME]... [--all-targets] [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive]
          [--config PATH] [--yes] [--no-backup] [--max-batch N] [--dry-run] [--no-log] [-v]
efc repl  [--config PATH] [-v]

efc config add    --dir PATH [--pattern REGEX]... [--name NAME] [--recursive/--no-recursive]
                  [--replace-patterns] [--config PATH]
efc config list   [--config PATH] [--json]
efc config remove [--name NAME | --dir PATH] [--config PATH]
efc patterns [--target NAME] [--config PATH] [--json]
```

参数解析规则（`scan`/`clean` 共用）：

- `--dir`/`--pattern`/`--target` 均可省略，按 §5.2 目标解析规则与 config.json 合并；合并后仍无目标目录或模式 → 报错 exit 2，消息提示用 `efc config add`、`--dir/--pattern` 或 config.json 补齐。
- `--pattern` 可多次出现（Typer list option）。
- `--target NAME` 选择 config.json 中命名目标；`--dir`/`--pattern`/`--recursive` 仍可逐个覆盖其基底。
- `--json` 仅 `scan` 提供：输出机器可读结果（见下），人类表格输出走 rich `Table`（列：相对路径、大小、修改时间）。

### 7.2 `scan` 在 `--format json` 下的输出 Schema

```json
{"data": {
  "root": "D:\\Downloads",
  "recursive": true,
  "scanned_dirs": 7,
  "count": 2,
  "matches": [
    {"path": "D:\\Downloads\\a\\~$report.docx", "relative": "a/~$report.docx",
     "size": 162, "mtime": "2026-08-20T10:11:12"}
  ]
}}
```

（mtime 用 `datetime.fromtimestamp(...).isoformat()`；相对路径用 `/` 分隔便于跨平台阅读。`--json` 是 `--format json` 的简写，统一信封 `{"data": ...}`。）

### 7.3 `clean` 人类可读输出示例

```
目标目录: D:\Downloads（递归: 开）
高危评估: 安全
扫描完成: 7 个目录，命中 12 个文件，共 3.4 MB
  a/~$report.docx          162 B   2026-08-20 10:11
  b/tmp/x.tmp              3.2 MB  2026-08-19 18:02
  ...
确认将 12 个文件移入回收站? [y/N]:
[批次 1/3] 备份 → 回收站: 4/12 ... 完成
继续下一批? [y/N]:
...
完成: 12 个文件已入回收站，0 个失败。
备份与清单: C:\Users\shen\.efc\backup\20260821-153000.123\manifest.json
本次对 D:\Downloads 路径完成文件清理，合计清理 12 个文件，合计大小 3.4 MB，合计用时 0.5 min，具体为
一、本次 D:\Downloads（downloads）完成清理 12 个文件，合计大小 3.4 MB，具体为：
1. “^~\$”模式：清理 3 个文件，合计大小 0.2 MB；
2. “\.tmp$”模式：清理 9 个文件，合计大小 3.2 MB；
```

### 7.4 退出码

| code | 含义 | 触发示例 |
|---|---|---|
| 0 | 成功（含"0 命中，无事可做"） | 正常完成 |
| 1 | 未预期内部错误 | 未捕获异常（bug）；**任何业务报错不得返回 0** |
| 2 | 配置/用法/输入错误 | 非 win32、UNC 路径、max_batch=20、坏正则、目录不存在、stdin JSON 非法、未知负载键、Typer 用法错误 |
| 3 | 用户中止 / 安全拦截 | 拒绝确认、高危未通过二次确认、非交互/JSON 模式高危无法确认 |
| 4 | 执行期部分失败 | 存在 `trash_failed`/`backup_failed`（只要有文件成功删除仍算 4，消息中列失败项） |

退出码是 Agent 调用必须依赖的硬契约：json 信封 `code` 字段 === 进程退出码；业务报错绝不返回 0。

多目标 `clean` 的退出码按优先级取：任一目标存在失败文件 → 4；否则任一目标被中止 → 3；否则 0。

### 7.5 `efc config` —— 命令行添加/管理配置

在不手工编辑 config.json 的前提下，用命令行增加「目标目录 + 对应的一组文件名正则」等目标配置。三个子命令均为**纯配置操作**：不扫描、不删除任何文件。

示例会话：

```
$ efc config add --dir D:\Downloads --pattern "^~\$" --pattern "\.tmp$"
已保存目标 default: D:\Downloads（模式 2 个，递归: 开）

$ efc config add --name downloads --dir D:\Downloads --pattern "\.bak$" --recursive
已保存目标 downloads: D:\Downloads（模式 3 个，递归: 开）   # 同名目标追加正则并去重

$ efc config list
目标:
  default   D:\Downloads    2 个模式  递归: 开
  downloads D:\Downloads    3 个模式  递归: 开
其它设置: confirm=true, max_batch=5, backup_enabled=true, backup_dir=C:\Users\shen\.efc\backup

$ efc config remove --name downloads
已移除目标: downloads
```

行为约定：

- `add`：加载现有配置 → `add_target`（默认名 `default` 写顶层字段；`--name` 命名条目新增/更新，patterns 默认**追加并去重**，`--replace-patterns` 整体替换）→ 校验（目录必须存在、每个正则可编译、合并后 `max_batch` 合法）→ 原子写回。任一校验失败 exit 2 且**不写盘**。
- `list`：按 §5.3 `list_targets` 列出全部目标（name / dir / 模式数 / recursive），并附其它设置；`--json` 输出机器可读结构。模式只显示数量，查看具体正则列表请用 `efc patterns`（§7.6）。
- `remove`：`--name` 或 `--dir` 二选一；命中顶层默认目标时清空顶层字段；未命中任何目标 → exit 2。
- 三个子命令都支持 `--config` 指定配置文件，默认解析顺序同 §5.2 加载规则。

### 7.6 `efc patterns` —— 列举当前的文件名规则

只读查询：列出当前生效的文件名正则（名字规则），便于确认 `efc config add` 或 config.json 中已配置了哪些模式。

```
$ efc patterns
当前文件名规则（目标: default，目录: D:\Downloads）:
  1. ^~\$
  2. \.tmp$
  3. \.bak$

$ efc patterns --json
{"data": {"target": "default", "dir": "D:\\Downloads", "patterns": ["^~\\$", "\\.tmp$", "\\.bak$"]}}
```

行为约定：

- 目标解析同 §5.2（内部调用 `config.resolve_target()`）：`--target NAME` 不存在 → exit 2；未配置任何模式时打印「当前没有配置任何文件名规则」，**exit 0**（查询成功，空结果是正常状态，不是错误）。
- 仅展示，不修改任何配置；`--json` 输出 Schema（统一信封）：`{"data": {"target": str|null, "dir": str|null, "patterns": str[]}}`。
- 与 `efc config list` 的分工：`config list` 概览全部目标与其它设置（模式只显示数量），`efc patterns` 展示具体正则列表。

### 7.7 清理总结与执行日志

#### 清理总结（clean 完成后自动输出）

按「目标目录 → 模式」维度总结本次清理，格式：

```
本次对 D:\Downloads、D:\Temp 等 2 条路径完成文件清理，合计清理 12 个文件，合计大小 3.4 MB，合计用时 0.5 min，具体为
一、本次 D:\Downloads（downloads）完成清理 8 个文件，合计大小 2.8 MB，具体为：
1. “^~\$”模式：清理 3 个文件，合计大小 0.2 MB；
2. “\.tmp$”模式：清理 5 个文件，合计大小 2.6 MB；
二、本次 D:\Temp 完成清理 4 个文件，合计大小 0.6 MB，具体为：
1. “\.tmp$”模式：清理 4 个文件，合计大小 0.6 MB；
```

规则：

- 只统计**实际入回收站**（trashed）的文件；有失败文件时在总结末尾追加一行「另有 N 个文件清理失败（详见执行日志）」。
- 单一目标时首行不写"等 N 条路径"；目标名为 default 时不带括号后缀。
- 多个目标时按执行顺序编号（一、二、三…）；目标内按命中模式分组（每文件归入命中的**第一个**模式，见 §6.2）。
- 全部未清理（0 文件）时仅输出「本次未清理任何文件。」，不输出分节。

#### 执行日志（每次 clean 追加一条 JSONL）

路径由 `log_file` 配置（默认 `.efc.log`，`--no-log` 可关闭），示例一条记录：

```json
{"ts": "2026-08-21T16:00:00.123", "command": "clean", "dry_run": false,
 "result": "partial", "duration_seconds": 12.3,
 "targets": [{"name": "downloads", "dir": "D:\\Downloads",
   "files": [{"path": "D:\\Downloads\\a\\~$report.docx", "size": 162,
              "pattern": "^~\\$", "status": "trashed"}]}]}
```

- 每次 CLI `clean` 执行（含 `--dry-run` 与中止场景）写一条；`repl` 内 `clean` 同样写；`scan` 不写（无实际工作）。
- 记录**具体文件名**（绝对路径）、大小、命中的模式、状态，以及汇总用时与 result（completed/partial/aborted/dry_run，判定见 §6.8）。
- 日志写入失败只警告不影响退出码；日志文件位于目标目录内时扫描自动排除（§6.6 第 5 步）。

### 7.8 Agent 调用（--format json / --non-interactive / --stdin）

面向自动化/Agent 的无头调用：结构化 stdout/stdin + 严格退出码。原有人类交互能力完全保留（缺省 `--format text`，行为与本文档其余章节一致）。

#### 调用约定

```
efc [--format text|json] [--non-interactive] [--stdin] <command> [command 参数...]
```

- 全局选项必须位于子命令**之前**；`--json` 是 `--format json` 的简写（同时出现以 `--format` 为准）。
- 输入来源与优先级：**CLI 显式参数 > `--stdin` JSON > 环境变量（EFC_\*） > config.json > 默认值**（§5.4）。
- **大输入**（大量 patterns/targets）用 `--stdin`：`echo '{"patterns":[...],"target":[...]}' | efc --format json --non-interactive --stdin clean`；TTY 下 `--stdin` 直接报错 exit 2。
- `--format json` 隐含自动确认（无人阅读提示），但高危目录仍需二次确认——非交互下无法输入 → 直接中止并返回 `{"code":3,...}`（fail-safe，见红线 5）。
- `--format json` 的**输出路由强制规则**：stdout 只输出一行最终的 JSON 信封（`{"data":...}` 或 `{"code":...,"msg":...}`）。所有人类可读输出——包括 `show_matches` 的表格、进度条、批次提示、警告——**一律路由到 stderr 或完全静默**。`--format json` 自动禁用 Rich 彩色（`no_color=True`）和进度条（`progress=False`），即使未同时传 `--non-interactive` 也生效。Agent 调用建议同时传 `--non-interactive` 以彻底消除交互/阻塞风险。
- `--non-interactive`：关闭交互确认、彩色、进度条；**全程不调用 input()、不阻塞**，执行完立即退出。
- `repl` 不支持 agent 模式：`--format json`/`--non-interactive`/`--stdin` 任一带上 → exit 2 报错。

#### JSON 响应契约

stdout 只输出**一行**结果 JSON（UTF-8、ensure_ascii=False、换行结尾）；所有日志/警告/verbose 走 stderr。

成功：`{"data": {...}}`

| 命令 | data 内容 |
|---|---|
| scan | `{root, recursive, scanned_dirs, count, matches:[{path, relative, size, mtime}]}`（§7.2） |
| clean | `{command, result(completed/partial/aborted/dry_run), exit_code, duration_seconds, total_matched, trashed, failed, aborted, backup_dir, log_file, summary, targets:[{name, dir, trashed, bytes, by_pattern:[{pattern, files, bytes}], files:[{path, size, pattern, status}]}]}` |
| config add | `{saved, target:{name, dir, patterns, recursive}, config_file}` |
| config list | `{targets:[{name, dir, patterns, recursive}], confirm, max_batch, backup_enabled, backup_dir, ignore_case, log_enabled, log_file, high_risk_dirs}` |
| config remove | `{removed}` |
| patterns | `{target, dir, patterns}` |

失败：`{"code": <退出码>, "msg": "<错误消息>"}` —— `code` 与进程退出码**严格相等**（§7.4）。

示例：

```
$ efc --format json --non-interactive scan --dir D:\Downloads --pattern "\.tmp$"
{"data": {"root": "D:\\Downloads", "recursive": false, "scanned_dirs": 1, "count": 1,
          "matches": [{"path": "D:\\Downloads\\a.tmp", "relative": "a.tmp", "size": 12, "mtime": "2026-08-20T10:11:12"}]}}

$ echo '{"dir":"D:\\Downloads","patterns":["\\.tmp$"],"dry_run":true}' \
    | efc --format json --non-interactive --stdin clean
{"data": {"command": "clean", "result": "dry_run", "exit_code": 0, "duration_seconds": 0.8, ...}}

$ efc --format json --non-interactive clean --target 不存在
{"code": 2, "msg": "目标不存在: 不存在"}
```

## 8. REPL 会话设计

启动横幅（版本 + "输入 help 查看命令"），随后 `efc> ` 循环。REPL 启动时按 §5.2 目标解析规则把有效目标作为初始状态；`dir` 命令只改变**会话内**目标，不写回 config.json（持久化请用 `efc config add`）。REPL 内 `clean` 与 CLI 走同一流水线，结束后同样输出清理总结并写执行日志（§7.7）。REPL 仅支持人类交互：`--format json`/`--non-interactive`/`--stdin` 任一带上 → exit 2 报错。示例会话：

```
$ efc repl
efc> dir D:\Downloads
目标目录: D:\Downloads（高危评估: 安全）
efc> pattern ^~\$
已添加模式 (1): ^~\$
efc> pattern \.tmp$
已添加模式 (2): \.tmp$
efc> recursive on
递归: 开
efc> list
命中 3 个文件:
  a/~$report.docx   162 B
  b/x.tmp           3.2 MB
  c/y.TMP           12 KB
efc> clean
（走与 efc clean 相同的确认/备份/分批流程）
efc> exit
再见。
```

实现约束：

- 命令未知 → 打印 "未知命令: xxx，输入 help 查看命令"，不退出。
- `dir` 指向高危目录时**当场**给出警告（清理时仍会走二次确认）。
- 每条命令的异常（坏正则、目录不存在）打印错误后回到提示符，会话不崩。
- Ctrl+C 在输入行清空当前行；连按或 EOF 退出。

## 9. 关键流程（clean 时序）

```
main() → ensure_win32 → load_config + CLI 合并 → 解析目标列表（--target... / --all-targets / 默认单目标）
for each target（独立完整流水线）:
    Cleaner(cfg, ConsoleUI|AutoUI).run()
      ├─ UNC? ── 是 → PlatformError(exit 2)
      ├─ 目标目录不存在 → ScanError(exit 2)
      ├─ assess_risk → 高危 → ui.confirm_high_risk(输入完整路径) ─ 失败 → AbortError(exit 3)
      ├─ compile_patterns → scan(排除 backup_dir/log_file) → 0 命中 → 提示 → 继续/结束
      ├─ show_matches → (confirm 开启) ui.confirm ─ 拒绝 → aborted
      ├─ backup.new_run()
      └─ for batch in chunks(matches, max_batch):
            (非首批且交互) ui.confirm_next_batch ─ 拒绝 → 停止
            for f in batch:
                backup_file ─ 失败 → 记 backup_failed, 跳过
                trash(str(f)) ─ 异常 → 记 trash_failed, 继续
         write_manifest → 记录 duration/total_bytes → outcome
汇总所有 outcome → build_summary → 打印清理总结（§7.7）
若 log_enabled 且未 --no-log → ExecutionLog.record（一条 JSONL）
exit 0 / 3(有中止) / 4(有失败)
```

## 10. 错误处理约定

- 所有面向用户的错误消息：中文、一句话说明 + 下一行动建议（"请检查 --dir 或 config.json"）。
- `--format json` 下错误统一为 stdout 一行 `{"code":N,"msg":"..."}`（code===退出码），人类可读提示与 traceback 走 stderr；text 模式不变。
- `verbose` 开启时打印完整 traceback 到 stderr；默认只打一行错误。
- 单文件级错误（备份失败、回收站失败）**不中断**批次与整轮执行，聚合进 manifest 与退出码。
- 扫描期间 PermissionError 的子目录：跳过并在 verbose 下记录。

## 11. 测试设计（pytest）

### 11.1 策略

- 单元测试直接测模块公共 API；`trash` 与 `UI` 全部注入 fake，**不 monkeypatch 全局**、不 mock `input`。
- CLI 测试用 `typer.testing.CliRunner`；REPL 测试直接调 `ReplSession.handle(line)`。
- 唯一 optional 的真实集成测试 `test_integration_real_trash.py`（可并入 test_cleaner）：标记 `@pytest.mark.skipif(os.environ.get("EFC_REAL_TRASH") != "1")`，在 tmp 目录真实 send2trash 后断言原路径不存在。默认 CI/本机跑 mock 套件。
- 所有测试用 `tmp_path` 构造目录树，不触碰真实用户目录；高危判定测试传 fake 的保护根列表（`assess_risk` 的 `extra` 参数 + monkeypatch `safety.protected_roots` 返回值）。

### 11.2 测试矩阵

| 文件 | 覆盖点（对应需求） |
|---|---|
| test_config.py | 默认值；JSON 加载；CLI 覆盖优先级（None 不覆盖）；`max_batch` 0/11 抛 ConfigError、1/10 通过；坏 JSON / 缺文件报错；`add_target` 默认名写顶层字段、命名目标新增/同名追加去重、`replace_patterns` 整体替换；`remove_target` 按 name/dir 移除；`save_config` 原子写且保留未动字段；add 校验失败（目录不存在/坏正则/max_batch 越界）抛 ConfigError；`resolve_target` 按 name/默认解析、未知 name 抛 ConfigError |
| test_scanner.py | **正则匹配**：命中/不命中、多模式 OR、ignore_case 大小写、匹配文件名而非路径（子目录名含模式不误报）；**递归开关**：recursive=True 命中子目录文件、False 仅顶层；exclude 排除备份目录；确定性排序；非法正则 PatternError；目录不存在 ScanError；FileMatch.pattern 为命中的**第一个**模式 |
| test_safety.py | **高危拦截**：target=C:\Windows、C:\Windows\System32（后代）、C:\（盘符根）、recursive 下 C:\ 为 C:\Windows 祖先、额外高危目录；普通 tmp 目录安全；normcase 大小写不敏感比对；`ensure_win32` monkeypatch `sys.platform='linux'` 抛 PlatformError；UNC 检测；`validate_batch_size` 边界 |
| test_backup.py | 备份保留相对结构；copy2 保留 mtime；manifest.json 字段完整（original/backup/status）；多批次追加正确 |
| test_cleaner.py | **回收站删除**：fake trash 收到每个文件的**绝对路径字符串**、调用次数=成功数；备份失败 → 该文件 status=backup_failed 且**未**送 trash；trash 抛异常 → trash_failed 且继续；confirm 拒绝 → aborted 且 trash 零调用；高危 + AutoUI → AbortError 且零调用；分批边界：13 文件 max_batch=5 → 3 批（5/5/3）；批间拒绝 → 停在第 N 批；0 命中 → 空 outcome；UNC / 非 win32 拦截；FileOutcome 携带 size/pattern；outcome.duration_seconds 与 total_bytes 正确；备份空间不足（monkeypatch `shutil.disk_usage`）→ AbortError 且零 trash 调用 |
| test_cli.py | CliRunner：`scan --json` 结构与 count；`scan` 无 dir/pattern 时 exit 2；`clean --yes` 在 tmp 上（注入 fake trash）成功 exit 0 且 manifest 存在；`clean --dry-run` 零副作用；`--max-batch 11` exit 2；非 win32 monkeypatch 后任意命令 exit 2；`config add` 写文件成功且内容正确、非法输入 exit 2 且文件内容未变；`config list --json` 结构；`config remove` 生效；`scan --target NAME` 使用命名目标、`--target` 不存在 exit 2；`efc patterns` 列出默认目标与 `--target` 目标的模式、空配置输出空提示且 exit 0、`--json` 结构正确；多目标 clean（`--target` 重复 / `--all-targets`）聚合输出总结且日志仅一条记录、`--target` 与 `--all-targets` 冲突 exit 2、`--no-log` 不写日志文件、clean 输出总结文本含目录/模式层级；`--format json` 成功/错误信封与退出码（未知异常→code 1、UsageError→code 2）；`--non-interactive` 自动确认且高危返回 code 3；`--stdin` 负载驱动 clean/scan；`repl` 拒绝 agent 标志 exit 2；`--json` 简写等价 |
| test_repl.py | `handle("dir <tmp>")` 设置状态；`pattern` 追加/非法拒绝/清空；`recursive on/off`；`list` 输出命中；`clean`（FakeUI confirm=True + fake trash）删除生效且输出总结/写日志；`exit/quit` 返回 False；未知命令不退出；未设 dir 时 clean 报提示 |
| test_summary.py | 按目标目录/模式聚合：只统计 trashed、同 dir 合并、pattern=None 归入 "(无模式)"；format_bytes / format_duration；render_summary 输出含首行汇总与一、二、编号分节、空输入返回空串；失败文件单独计数 |
| test_journal.py | record 追加 JSONL 且一行一条；字段含具体文件名/模式/状态；dry_run 与 aborted 的 result 判定；写失败（只读目录）不抛异常 |
| test_input.py | 环境变量解析：EFC_PATTERNS/EFC_TARGET 换行分隔、布尔/数值、非法值抛 ConfigError；stdin 负载解析：合法/非法 JSON、未知键/类型不符报错、TTY 下 --stdin 报错；优先级 CLI > stdin > env > config（列表整体替换） |
| test_output.py | emit_success/emit_error 输出单行 JSON 信封；exit_code_for：EfcError→自身 exit_code、UsageError→2、未知异常→1 |

> test_cli.py 覆盖点横跨 scan/clean/config/patterns/agent 五大域，用例量大——建议在文件内按功能拆分为多个测试类（如 `TestScan`/`TestClean`/`TestConfig`/`TestAgent`/`TestReplRestriction`），便于定位失败与并行扩展。

### 11.3 conftest.py 关键 fixtures

```python
@pytest.fixture
def tree(tmp_path): ...        # 构造: 顶层 {~$a.docx, keep.txt, x.tmp},
                               # 子目录 b/{y.TMP, ~$b.docx}, b/c/{z.tmp}

@pytest.fixture
def fake_trash():              # 记录调用路径；可选副作用 shutil.move 到隔离"回收站"目录
    calls: list[str]

@pytest.fixture
def fake_ui():                 # 可编程 confirm 序列；confirm_high_risk 可设期望路径
```

## 12. 验收标准（Definition of Done）

- [ ] `pip install -e ".[dev]"` 后 `efc --version` / `efc --help` 正常，仅 Windows 可运行（他平台 exit 2）。
- [ ] 对 tmp 测试目录真实执行 `efc clean --yes`：文件从原位置消失，可从备份目录 + manifest 对账；未使用任何硬删除。
- [ ] 备份空间不足（如 `--backup-dir` 指向只读盘）时 clean 在删除前中止（exit 3），不产生任何 trash 调用。
- [ ] 高危目录（如 `C:\Windows` 子目录）执行 clean：必须输入完整路径才继续；`--yes` 或管道 stdin 无法绕过。
- [ ] `max_batch` 传 11 被 reject；传 5 时 13 个文件分 3 批。
- [ ] REPL 五命令 + help/status/recursive 可用，`clean` 与 CLI 行为一致。
- [ ] `efc config add --dir X --pattern A --pattern B` 后，`efc scan`（不带参数）直接使用该配置预览命中；`efc config list` 显示正确。
- [ ] 同一目标重复 `efc config add` 追加正则并去重；`--replace-patterns` 整体替换。
- [ ] `efc config add` 校验失败（坏正则、目录不存在、max_batch 越界）exit 2 且 config.json 内容不变。
- [ ] `efc config remove` 后 `efc scan` 不再使用该目标；`scan --target` 选择命名目标、未知目标名 exit 2。
- [ ] `efc patterns` 列出当前文件名规则（含 `--target` 与 `--json`），空配置输出空提示且 exit 0；与 `config list` 分工明确。
- [ ] `efc clean` 结束后输出清理总结：按目标目录→模式分层，含文件数/大小/用时；多目标时一、二、编号分节。
- [ ] 执行日志（默认 `.efc.log`）追加记录每次 clean：含**具体文件名**/大小/命中的模式/状态与 result；`--no-log` 不写。
- [ ] `clean --target A --target B` 与 `--all-targets` 可一次清理多目标并聚合总结；`--target` 与 `--all-targets` 冲突 exit 2。
- [ ] `efc --format json --non-interactive clean/scan`（可配 `--stdin`）stdout 只输出一行 JSON：成功 `{"data":...}`、失败 `{"code":N,"msg":"..."}`（code===退出码）；日志/警告走 stderr。
- [ ] 环境变量 `EFC_*` 与 `--stdin` JSON 输入生效，优先级 CLI > stdin > env > config；未知负载键 exit 2。
- [ ] `--non-interactive` 全程无 input()/阻塞；高危目标在非交互/JSON 模式返回 `{"code":3,...}` 而非继续。
- [ ] `repl` 拒绝 `--format json`/`--non-interactive`/`--stdin`（exit 2）；缺省 text 模式的人类交互行为不变。
- [ ] README 包含：安装、快速上手、config.json 说明、安全模型（回收站/备份/高危/小批量）、FAQ（如何从备份恢复）。

## 13. 实现注意事项与陷阱（Windows 专项）

1. 路径比较前必须 `Path.resolve()` + `os.path.normcase()`（Windows 大小写不敏感且 `/`、`\` 混用）。
2. `send2trash` 接受 `str`；传入前转 `str(path)`，逐文件调用以便定位单个失败。
3. `os.walk` 默认 `followlinks=False`，保持默认；junction 同样不被跟随。
4. 备份目录若位于目标目录之内，必须通过 `scan(..., exclude=[backup_root])` 排除，否则二次运行会匹配到自己的备份（P4 联动 backup/cleaner 时实现）。
5. `~$` 开头的 Office 临时文件是示例模式的常客；JSON 中写 `"^~\\$"`（注意 JSON 转义两层）。
6. rich 表格在窄终端自动换行即可，不做宽度定制；`--json` 输出必须 `ensure_ascii=False` + UTF-8（Windows 控制台中文文件名）。
7. `typer.Option(None, "--recursive/--no-recursive")` 用 `Optional[bool]` 三态（None=未指定，回落 config）。
8. `CliRunner` 下 TTY 检测不可靠——需要/不需要确认的分支一律由注入的 UI 决定，CLI 层只在 `--yes` 时换 `AutoUI`。
9. 时间戳目录名 `time.strftime("%Y%m%d-%H%M%S") + f".{ms:03d}"` 防同秒冲突。
10. config.json 写入必须原子（临时文件 + `os.replace`）且 UTF-8 `ensure_ascii=False`、`indent=2`；`config add` 在写盘前对整个合并后的配置执行 `validate()`，任何校验失败不得落盘；写文件失败（如只读/占用）抛 ConfigError（exit 2）。
11. 清理总结只统计已入回收站的文件；每文件按命中的**第一个**模式归组（scanner 记录，保证唯一归属）。
12. 执行日志用追加写（`open(..., "a")`），单条 JSON 一行；写失败降级为 stderr 警告，绝不影响清理退出码。
13. json 模式 stdout 只允许一行结果 JSON；进度/警告/verbose 一律 stderr 或静默；`--help`/`--version` 在 json 模式下仍输出人类文本（元信息不属于业务契约，exit 0）。
14. `send2trash` 在**非 NTFS** 文件系统（FAT32/exFAT/ReFS）或回收站被组策略禁用时，可能直接物理删除或失败，无法保证进回收站；建议在 `assess_risk` 阶段检测目标盘文件系统类型（`shutil.disk_usage` 只能取到卷，需用 `os` 层的卷信息或 `fsutil` 判断），非 NTFS 时打印醒目警告；README 中说明该限制。
15. **备份目录会复制全部待清理文件**——若目标含敏感数据，备份目录即敏感目录；`efc config add` 的提示与 README 中说明，建议备份目录放在加密卷或受限权限目录，清理后及时清理备份。

## 14. 附录

### config.example.json（随仓库提供）

```json
{
  "targets": [
    {"name": "downloads", "dir": "D:\\Downloads",
     "patterns": ["^~\\$", "\\.tmp$", "\\.temp$", "\\.bak$", "^Thumbs\\.db$"],
     "recursive": true}
  ],
  "confirm": true,
  "max_batch": 5,
  "backup_enabled": true,
  "backup_dir": "C:\\Users\\shen\\.efc\\backup",
  "ignore_case": true,
  "high_risk_dirs": [],
  "log_enabled": true,
  "log_file": ".efc.log"
}
```

> 注：也可省略 `targets`，改用顶层 `target_dir` / `filename_patterns` / `recursive` 作为默认目标（`efc config add` 不带 `--name` 时即写入这些字段）。

### pyproject.toml 关键片段

```toml
[project]
name = "ephemeral-file-cleaner"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = ["typer[all]>=0.12", "send2trash>=1.8"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
efc = "efc.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
```
