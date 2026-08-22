# Spec — EphemeralFile Cleaner 技术实现方案

> 版本：v2.0（2026-08-22）｜基于 PRD.md v2.0，替代原 dev.md v1.1 的技术部分
> 本文是唯一实现依据：模块边界、数据模型、公共接口、契约不得自行更改；业务规则见 PRD §5。

## 1. 技术选型与环境

- Python **>= 3.10**（使用 `X | None` 语法）；打包 `pyproject.toml`（setuptools），入口点 `efc = efc.cli:main`，src 布局；
- 运行依赖：`typer[all]>=0.12`（含 rich）、`send2trash>=1.8`；
- 开发依赖：`pytest>=8.0`；工具 `mypy>=1.0`（strict，类型错误不得合入）、`ruff`（line-length 100，py310）；
- 运行平台：Windows 10+ / macOS 12+ / Linux 桌面发行版（需 freedesktop Trash，如 gvfs）；
- 输出约定：面向用户消息中文；JSON 输出 `ensure_ascii=False` + UTF-8、单行。

## 2. 项目结构

```
ephemeral-file-cleaner/
├── pyproject.toml
├── README.md
├── config.example.json
├── docs/{PRD.md, Spec.md, Plan.md, AGENTS.md(在根目录)}
├── src/efc/
│   ├── __init__.py        # __version__ = "1.0.0"
│   ├── exceptions.py      # EfcError 及子类（exit_code 体系）
│   ├── models.py          # FileMatch/ScanResult/RiskDecision/FileOutcome/CleanOutcome
│   ├── config.py          # AppConfig/Task + 任务清单 + 加载/合并/校验/保存 + env/--stdin 解析
│   ├── scanner.py         # 正则编译 + 目录扫描
│   ├── safety.py          # 平台守卫/卷根·保护根高危判定/UNC 拦截/批量校验
│   ├── backup.py          # 备份与 manifest
│   ├── ui.py              # UI 协议：ConsoleUI / AutoUI
│   ├── cleaner.py         # 清理流水线（依赖注入 ui 与 trash 函数）
│   ├── summary.py         # 清理总结聚合与渲染（纯函数）
│   ├── journal.py         # 执行日志 JSONL
│   ├── output.py          # Agent JSON 信封与退出码映射
│   ├── repl.py            # REPL 会话
│   └── cli.py             # Typer 入口：scan/clean/repl/task/patterns
└── tests/
    ├── conftest.py        # fixtures：tree / fake_trash / fake_ui
    └── test_<module>.py   # 按模块一一对应
```

**模块依赖方向（单向，禁止反向）**：`cli/repl → cleaner, summary, journal, output`；`cleaner → scanner, safety, backup, ui`；`scanner/safety/backup/ui → config, models, exceptions`；`summary/journal/output → models, exceptions`。Cleaner **不依赖** summary/journal/output（由 cli/repl 消费 `CleanOutcome` 后调用）。

**分层约束**：入口层不写业务逻辑；业务层不碰 rich 渲染；JSON 输出必须走 `output.emit_success/emit_error` 到 stdout，禁止 rich 输出 JSON（非 tty 下 80 列折行会破坏 JSON）。

## 3. 数据模型

```python
# exceptions.py —— exit_code 是类属性，子类覆盖；main() 统一 type(e).exit_code
class EfcError(Exception): exit_code: int = 2
class ConfigError(EfcError): ...    # 2 配置/用法/输入
class PlatformError(EfcError): ...  # 2 不支持平台 / Windows UNC
class PatternError(EfcError): ...   # 2 非法正则
class ScanError(EfcError): ...      # 2 目标目录不存在等
class AbortError(EfcError): ...     # 3 用户中止/高危拦截/非交互需确认

# config.py
@dataclass
class Task:
    name: str                          # 全清单唯一
    dir: Path | None = None
    patterns: list[str] = field(default_factory=list)
    recursive: bool = False
    default: bool = False              # True → 默认任务清单

@dataclass
class AppConfig:
    # 运行时解析结果（不落盘；由 cli/repl 按任务解析规则逐任务填充）
    target_dir: Path | None = None
    filename_patterns: list[str] = field(default_factory=list)
    recursive: bool = False
    # 持久化字段（save_config 只写这些）
    confirm: bool = True
    max_batch: int = 5                 # DEFAULT_MAX_BATCH；HARD_MAX_BATCH=10，越界 ConfigError
    backup_enabled: bool = True
    backup_dir: Path = Path(".efc-backup")
    ignore_case: bool = True
    high_risk_dirs: list[Path] = field(default_factory=list)
    log_enabled: bool = True
    log_file: Path = Path(".efc.log")
    tasks: list[Task] = field(default_factory=list)   # Cleaner 不消费

# models.py
@dataclass
class FileMatch:
    path: Path; relative: str; size: int; mtime: float
    pattern: str                        # 命中的第一个模式（按模式汇总唯一归属）
@dataclass
class ScanResult:
    root: Path; recursive: bool
    matches: list[FileMatch]            # 按 str(path) 排序（确定性）
    scanned_dirs: int
@dataclass
class RiskDecision:
    high_risk: bool; reason: str | None = None
@dataclass
class FileOutcome:
    path: Path
    status: str                         # "trashed" | "backup_failed" | "trash_failed"
    backup_path: Path | None = None
    error: str | None = None
    size: int = 0
    pattern: str | None = None
@dataclass
class CleanOutcome:
    total_matched: int
    results: list[FileOutcome]
    batches: int
    backup_dir: Path | None
    aborted: bool = False
    task_name: str | None = None        # 一次性任务为 None
    target_dir: Path | None = None
    duration_seconds: float = 0.0
    total_bytes: int = 0                # 仅 trashed 合计
    @property trashed -> list[FileOutcome]   # status=="trashed"
    @property failed  -> list[FileOutcome]   # backup_failed + trash_failed

# summary.py
@dataclass
class PatternStats: pattern: str; files: int; bytes: int
@dataclass
class TaskStats:
    name: str | None                    # None=一次性任务
    dir: Path; files: int; bytes: int
    by_pattern: list[PatternStats]      # 保持命中顺序
@dataclass
class RunSummary:
    tasks: list[TaskStats]; total_files: int; total_bytes: int
    duration_seconds: float; failed_files: int

# journal.py
@dataclass
class JFile: path: str; size: int; pattern: str | None; status: str
@dataclass
class JTarget: name: str | None; dir: str; files: list[JFile]
@dataclass
class JournalRecord:
    ts: str; command: str; dry_run: bool
    result: str                         # completed|partial|aborted|dry_run
    duration_seconds: float; tasks: list[JTarget]
```

`result` 判定优先级（跨任务汇总）：任一 aborted → `aborted`；否则有失败文件 → `partial`；否则 dry_run → `dry_run`；否则 `completed`。

## 4. 配置系统

### 4.1 config.json Schema

```json
{
  "tasks": [
    {"name": "downloads", "dir": "D:\\Downloads",
     "patterns": ["^~\\$", "\\.tmp$", "\\.bak$", "^Thumbs\\.db$"],
     "recursive": true, "default": true},
    {"name": "mac-downloads", "dir": "~/Downloads",
     "patterns": ["^~\\$", "\\.tmp$"], "recursive": false, "default": false}
  ],
  "confirm": true, "max_batch": 5, "backup_enabled": true,
  "backup_dir": "~/.efc/backup", "ignore_case": true,
  "high_risk_dirs": [], "log_enabled": true, "log_file": ".efc.log"
}
```

- `tasks[].name` 唯一（必填）；`dir` 必填（add 时必须存在，支持 `~` 展开）；`patterns` 必填字符串数组；`recursive`/`default` 缺省 false；
- `ignore_case`：正则忽略大小写（win/mac 文件系统不区分大小写，Linux 敏感，按需配置）；
- v1.0 顶层 `target_dir`/`filename_patterns`/`recursive` 旧键出现 → `ConfigError`（明确失败优于静默忽略）。

### 4.2 加载规则

- 查找链：`--config PATH` > `$EFC_CONFIG` > `./config.json` > `~/.efc/config.json` > 内置默认（缺失不算错误）；相对路径按 CWD 解析；指定的配置不存在或 JSON 解析失败 → `ConfigError`；
- 加载时校验：`max_batch ∈ [1,10]`；每任务 patterns 为字符串数组、name 唯一、dir 非空；`backup_dir` resolve + `~` 展开。

### 4.3 任务解析规则（scan/clean/repl 共用）

1. `--task NAME`（可重复，任一不存在 → ConfigError）→ 按 CLI 顺序；
2. 否则 `--all-tasks` → 全部任务（配置顺序）；
3. 否则 `--dir`（配 `--pattern`）→ 一次性任务（不落盘，task_name=None）；
4. 否则默认任务清单（`default: true`，配置顺序）；
5. 均无且默认清单为空 → exit 2，提示 `efc task add --default` / `--task` / `--dir/--pattern`。

互斥：`--dir` 与 `--task`/`--all-tasks` 同用 → exit 2。覆盖：CLI `--pattern` 对每任务整体替换其 patterns；`--recursive` 三态覆盖每任务递归。解析结果逐任务写入 `AppConfig.target_dir/filename_patterns/recursive`（运行时字段），Cleaner 只消费这三字段。

### 4.4 输入来源（Agent 模式）

优先级：**CLI 显式参数 > `--stdin` JSON > 环境变量（EFC_\*） > config.json > 默认值**；同键整体覆盖（列表整体替换）。传输级标志（`--format`/`--non-interactive`/`--stdin`）只来自 CLI 与环境变量。

| 环境变量 | 对应参数 | 说明 |
|---|---|---|
| `EFC_CONFIG` | --config | 配置文件路径 |
| `EFC_FORMAT` | --format | text \| json |
| `EFC_NON_INTERACTIVE` | --non-interactive | 1/true 启用 |
| `EFC_TASK` | --task | 任务名列表，**换行**分隔（cmd 无换行时用 `;`） |
| `EFC_DIR` | --dir | 目标目录 |
| `EFC_PATTERNS` | --pattern | 正则列表，换行或 `;` 分隔 |
| `EFC_RECURSIVE` | --recursive | 1/0 |
| `EFC_DRY_RUN` | --dry-run | 1/0 |
| `EFC_YES` | --yes | 1/0 |
| `EFC_MAX_BATCH` | --max-batch | 1..10 |
| `EFC_BACKUP_DIR` | backup_dir | 备份根目录 |
| `EFC_LOG_FILE` | log_file | 执行日志路径 |

`--stdin` 负载 Schema（未知键/类型不符 → ConfigError；TTY 下使用 → ConfigError；`command` 缺省取 CLI 子命令）：

```json
{
  "command": "clean", "config": "path/to/config.json",
  "task": ["downloads"], "all_tasks": true,
  "dir": "D:\\Downloads", "patterns": ["^~\\$", "\\.tmp$"],
  "recursive": true, "yes": true, "max_batch": 5,
  "backup_enabled": true, "backup_dir": "C:\\Users\\shen\\.efc\\backup",
  "dry_run": true, "no_backup": true, "no_log": true
}
```

## 5. 核心接口

```python
# config.py
def load_config(path: Path | None = None) -> AppConfig
def merged(base: AppConfig, overrides: dict) -> AppConfig   # None 不覆盖；覆盖后 validate()
def save_config(cfg: AppConfig, path: Path) -> None
    # UTF-8/ensure_ascii=False/indent=2；只写持久化字段；原子写（临时文件+os.replace）；失败 ConfigError
def add_task(cfg, *, name, dir=None, patterns=None, recursive=None,
             default=None, replace_patterns=False) -> None
    # 新增（dir 必填）/同名更新（仅覆盖显式字段）；patterns 追加去重或整体替换；
    # default=None 不动标记；落盘前校验 dir 存在+正则可编译+整体 validate，失败 ConfigError 不写盘
def remove_task(cfg, *, name=None, dir=None) -> bool
def list_tasks(cfg) -> list[Task]          # 配置顺序
def resolve_task(cfg, name: str) -> Task   # 缺失 ConfigError
def default_tasks(cfg) -> list[Task]       # default=True 按配置顺序
def read_env_overrides() -> dict           # EFC_* → 覆盖字典；非法值 ConfigError
def read_stdin_payload() -> dict           # 非 TTY 才允许；未知键/类型不符 ConfigError
def merge_overrides(cfg, *layers: dict) -> AppConfig  # 逐层叠加，每层后 validate()

# scanner.py
def compile_patterns(patterns: list[str], ignore_case: bool) -> list[re.Pattern]
    # 非法抛 PatternError（消息含模式原文与 re 错误）
def scan(root: Path, patterns: list[re.Pattern], recursive: bool,
         exclude: list[Path] | None = None) -> ScanResult
    # recursive=True: os.walk(followlinks=False)；False: root.iterdir() 仅顶层；
    # 仅 is_file()；对 file.name 做 re.search；记录命中第一个模式；
    # exclude 目录（resolve+normcase）整棵跳过；无权限子目录跳过并计数；输出按 str(path) 排序

# safety.py
SUPPORTED_PLATFORMS = frozenset({"win32", "darwin", "linux"})
def ensure_supported_platform() -> None    # 否则 PlatformError（消息含当前平台）
def is_unc(path: Path) -> bool             # PureWindowsPath.drive 以 \\ 开头；posix 恒 False
def protected_roots(extra: list[Path]) -> list[Path]   # 见 §8 平台保护根
def home_root() -> Path | None             # Path.home()；取不到 None
def volume_root(path: Path) -> Path | None  # win 盘符根；posix 最近 ismount 祖先（含 /）
def assess_risk(target: Path, recursive: bool, extra: list[Path]) -> RiskDecision  # 见 §8
def validate_batch_size(n: int) -> None    # not 1<=n<=10 → ConfigError

# backup.py
class BackupRun:
    def __init__(self, base_dir: Path)    # base_dir/<YYYYmmdd-HHMMSS.fff>/ 建目录
    @property root -> Path
    def backup_file(self, src: Path, relative: str) -> Path   # shutil.copy2 → root/relative，parents=True；异常上抛
    def write_manifest(self, outcomes: list[FileOutcome], meta: dict) -> Path
    # manifest.json：{run_at, target_dir, patterns, recursive, results:[{original,backup,status,size,error}]}，UTF-8/indent=2
def new_run(base_dir: Path) -> BackupRun  # 工厂（不直接暴露 BackupRun.__init__）

# ui.py
class UI(Protocol):
    def confirm(self, message: str) -> bool
    def confirm_high_risk(self, path: Path, reason: str) -> bool
    def confirm_next_batch(self, done: int, total: int) -> bool
    def show_matches(self, result: ScanResult) -> None
    def show_summary(self, outcome: CleanOutcome) -> None
    def error(self, message: str) -> None
class AutoUI:    # --yes/测试用：confirm/confirm_next_batch 恒 True；confirm_high_risk 恒 False
class ConsoleUI: # typer.confirm/rich；confirm_high_risk 逐字符输入 normcase 路径，一次不匹配 False；
                # 开关 no_color/progress/interactive；--format json 时 no_color=True/progress=False/人读输出走 stderr

# cleaner.py
class Cleaner:
    """只处理一个已解析任务（config 三运行时字段须已就绪）；任务清单解析不在本类职责。"""
    def __init__(self, config: AppConfig, ui: UI, trash: Callable[[str], None] = send2trash)
    def run(self) -> CleanOutcome          # 流水线见 §7

# summary.py
def build_summary(outcomes: list[CleanOutcome]) -> RunSummary
    # 同一目标目录（normcase）合并、name 取首个；pattern=None 归入 "(无模式)"；只统计 trashed
def format_bytes(n: int) -> str            # B/KB/MB/GB 自适应，2 位小数（B 取整）
def format_duration(sec: float) -> str     # <60s → "N 秒"，否则 "N.N min"
def render_summary(s: RunSummary) -> str   # §9 格式；tasks 为空返回空串

# journal.py
class ExecutionLog:
    def __init__(self, path: Path)
    def record(self, rec: JournalRecord) -> None
    # open(a, utf-8) 追加单行 JSON（ensure_ascii=False）；写失败仅 stderr 警告不抛异常

# output.py
def emit_success(data: dict) -> None       # stdout 单行 {"data": data}
def emit_error(code: int, msg: str) -> None  # stdout 单行 {"code": code, "msg": msg}
def exit_code_for(exc: BaseException) -> int  # EfcError→exit_code；click.UsageError→2；其余→1

# repl.py
class ReplSession:
    def __init__(self, config: AppConfig, ui: UI, trash=fake|send2trash)
    def handle(self, line: str) -> bool    # shlex.split；False=退出
    def run(self) -> None                  # input(prompt="efc> ")；EOF/Ctrl+C 优雅退出
```

## 6. CLI 契约

### 6.1 用法

```
efc [--format text|json] [--non-interactive] [--stdin] <command> ...   # 全局选项在子命令前
efc --version
efc scan  [--task NAME]... [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive] [--config PATH] [--json] [-v]
efc clean [--task NAME]... [--all-tasks] [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive]
          [--config PATH] [--yes] [--no-backup] [--max-batch N] [--dry-run] [--no-log] [-v]
efc repl  [--config PATH] [-v]
efc task add    --name NAME [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive]
                [--default/--no-default] [--replace-patterns] [--config PATH]
efc task list   [--config PATH] [--json]
efc task remove [--name NAME | --dir PATH] [--config PATH]
efc patterns [--task NAME] [--config PATH] [--json]
```

- `--json` 仅 scan/task list/patterns 提供，是 `--format json` 简写（同时出现以 `--format` 为准）；
- `--yes` 跳过普通确认，不能绕过高危；`--no-backup` 是显式弃权备份（README 醒目警告）；`--no-log` 本次不写日志；
- `efc --format json --help/--version` 仍输出人类文本（元信息不属业务契约，exit 0）。

### 6.2 输出路由

- text 模式：表格/进度/总结走 rich（stdout/stderr 按交互场景）；错误 → stderr 一行 `错误: ...`；
- json 模式：**stdout 只允许一行结果 JSON**（成功 `{"data":...}` / 失败 `{"code":N,"msg":...}`，code===退出码）；所有人读输出（表格/进度/警告）走 stderr 或静默；自动 `ConsoleUI(no_color=True, progress=False)`。

### 6.3 JSON 信封 data 内容

| 命令 | data |
|---|---|
| scan | `{tasks:[{task, root, recursive, scanned_dirs, count, matches:[{path, relative, size, mtime}]}]}`；一次性任务 task=null；mtime 用 `datetime.fromtimestamp().isoformat()`；relative 用 `/` 分隔 |
| clean | `{command, result(completed/partial/aborted/dry_run), exit_code, duration_seconds, total_matched, trashed, failed, aborted, backup_dir, log_file, summary, tasks:[{name, dir, trashed, bytes, by_pattern:[{pattern, files, bytes}], files:[{path, size, pattern, status}]}]}` |
| task add | `{saved, task:{name, dir, patterns, recursive, default}, config_file}` |
| task list | `{tasks:[{name, dir, patterns, recursive, default}], confirm, max_batch, backup_enabled, backup_dir, ignore_case, log_enabled, log_file, high_risk_dirs}` |
| task remove | `{removed}` |
| patterns | `{tasks:[{task, dir, default, patterns}]}`（无 `--task` 时列全部） |

### 6.4 入口与异常捕获（cli.py）

```python
@dataclass
class AgentState: format: str = "text"; non_interactive: bool = False; stdin: bool = False
# @app.callback() 存 ctx.obj；--version 打印后 typer.Exit()

def _resolve_format() -> str
    # app() 之前独立解析：sys.argv 的 --format/--json 优先，其次 EFC_FORMAT，默认 text。
    # 不依赖 callback（子命令解析异常场景 callback 可能未执行）

def main() -> None:
    # 启动 ensure_supported_platform()
    # try app()：click.UsageError → 2；EfcError → type(e).exit_code；其余 → 1（"内部错误"）
    # json 模式错误 → emit_error 到 stdout；text 模式 → stderr "错误: ..."；同码退出
```

- repl 的 agent 限制：`--format json`/`--non-interactive`/`--stdin` 任一 → ConfigError（exit 2）；
- `--non-interactive`：`ConsoleUI(interactive=False, no_color=True, progress=False)`，确认自动通过（高危仍拒 → code 3），全程无 input()/阻塞；
- scan 与 `clean --dry-run` 行为一致（只读）；多任务 clean 全部结束后：汇总 CleanOutcome → build_summary → render_summary 打印；`log_enabled` 且未 `--no-log` → 写**一条** JournalRecord（dry-run 也写，result=dry_run）；退出码按 PRD §5.3。

## 7. 清理流水线（Cleaner.run 步骤）

1. `time.perf_counter()` 记开始；
2. `ensure_supported_platform()`；`is_unc(target)`（posix 恒 False 直接通过）；
3. 任务目录存在性（`ScanError`）；
4. `assess_risk()` → 高危且 `ui.confirm_high_risk()` False → `AbortError`；
5. `compile_patterns()`；`scan(exclude=[backup_dir, log_file])`（二者在任务目录内时整棵排除）；
6. 零命中：提示并返回空 `CleanOutcome`（exit 0）；
7. `ui.show_matches()`；`confirm` 开启时 `ui.confirm("确认将 N 个文件移入回收站?")` 拒绝 → aborted；
8. `backup_enabled` 时先备份空间预检：`shutil.disk_usage(backup_dir)` 可用 < 匹配总字节×1.05 → `AbortError`（exit 3，零 trash）；通过后 `backup.new_run()`；
9. 分批循环（每批 ≤ max_batch）：批间（非首批）`ui.confirm_next_batch()` 拒绝则停止（已删不回滚，aborted=True）；批内逐文件：备份失败 → `backup_failed` 跳过；`trash(str(path))` 异常 → `trash_failed` 继续；FileOutcome 携带 size/pattern；
10. `write_manifest()`；写回 duration_seconds/total_bytes（仅 trashed 合计）；`ui.show_summary()`；返回 outcome。

dry_run 跳过实际 trash 调用，正常走 scan/确认流程。`trash` 为构造注入：生产 send2trash，测试 fake（记录调用）。

## 8. 高危判定（assess_risk）

target 需已 `resolve()`；满足任一即 high_risk（reason 给人话原因）：

- a) target 是卷根/盘符根：`volume_root(target) == target`（win `C:\`；posix `/` 与各挂载点）；
- b) target 等于某系统保护根或 home 根（normcase 比对）；
- c) target 位于某**系统**保护根之内（后代）；home 根不参与本条（用户目录是常规清理对象，home 子目录不自动高危）；
- d) recursive=True 且 target 是某系统保护根的祖先（如 `/` 之于 `/usr`、`C:\` 之于 `C:\Windows`）。

平台保护根（展开环境变量并 resolve，取不到跳过，加 extra）：

| 平台 | 内置保护根 |
|---|---|
| win32 | `%SystemRoot%`（默认 C:\Windows）、`%ProgramFiles%`、`%ProgramFiles(x86)%`、`%ProgramData%` |
| darwin | /System、/Library、/usr、/bin、/sbin、/etc、/private/etc、/Applications |
| linux | /、/usr、/bin、/sbin、/lib、/lib64、/etc、/var、/boot、/opt、/root、/home |

所有路径比较一律 `os.path.normcase(os.fspath(p))` 归一（posix 下 normcase 恒等，无需分支）。

## 9. 清理总结格式（render_summary）

```
本次对 D:\Downloads、D:\Temp 等 2 条路径完成文件清理，合计清理 12 个文件，合计大小 3.4 MB，合计用时 0.5 min，具体为
一、本次 D:\Downloads（downloads）完成清理 8 个文件，合计大小 2.8 MB，具体为：
1. "^~\$"模式：清理 3 个文件，合计大小 0.2 MB；
2. "\.tmp$"模式：清理 5 个文件，合计大小 2.6 MB；
二、本次 D:\Temp 完成清理 4 个文件，合计大小 0.6 MB，具体为：
1. "\.tmp$"模式：清理 4 个文件，合计大小 0.6 MB；
```

- 只统计 trashed；失败文件末尾追加「另有 N 个文件清理失败（详见执行日志）」；
- 单一任务首行不写"等 N 条路径"；任务名为 None（一次性）不带括号后缀；
- 全部未清理（0 文件）仅输出「本次未清理任何文件。」

## 10. REPL 设计

- 启动横幅（版本 + "输入 help 查看命令"），`efc> ` 循环；初始会话任务：默认任务清单恰一个时自动加载，否则空；
- `task`/`dir`/`pattern`/`recursive` 只改会话内状态，不写回 config（持久化用 `efc task add`）；`clean` 与 CLI 走**同一条** Cleaner 流水线（禁止复制第二套逻辑），结束后同样写日志、输出总结；
- 命令表：

| 命令 | 行为 |
|---|---|
| `task [NAME]` | 无参：列任务清单（含 default 标记）；有参：加载命名任务（不存在提示不退出） |
| `dir [PATH]` | 无参：显示当前目录与高危评估；有参：设置（resolve/存在性/UNC·高危即时警告但不中止会话） |
| `pattern [REGEX \| clear \| list]` | 追加（即时编译校验，非法不追加）/清空/列出 |
| `recursive [on\|off]` | 查看/切换 |
| `list` | 用当前状态 scan 并 `ui.show_matches()`（只读） |
| `clean` | 构造 Cleaner.run；dir/patterns 未设时提示缺什么 |
| `status` | 汇总 dir/patterns/recursive/confirm/max_batch/backup |
| `help` / `exit` / `quit` | 命令表 / 退出（EOF 同） |

- 未知命令 → 提示不退出；每条命令异常打印错误回到提示符；Ctrl+C 清行，连按或 EOF 退出。

## 11. 异常处理约定

- 用户可见错误：中文一句话 + 下一行动建议（"请检查 --dir 或 config.json"）；verbose 打印 traceback 到 stderr；
- 单文件级错误（备份/回收站失败）不中断批次与整轮，聚合进 manifest 与退出码（4）；
- 扫描期 PermissionError 子目录：跳过并计数（verbose 记录）；
- 日志写入失败：stderr 警告，不影响退出码。

## 12. 安全红线的技术落点（对 PRD §5.1）

| 红线 | 落点 |
|---|---|
| 平台守卫 | `safety.ensure_supported_platform()`，CLI 入口与 REPL 启动各调一次 |
| 唯一删除入口 | `send2trash.send2trash()` 经 Cleaner 构造注入；`src/` 禁止 `os.remove/os.unlink/Path.unlink/shutil.rmtree/os.rmdir`（备份 `shutil.copy2` 是复制，不受限） |
| UNC 拦截 | `safety.is_unc()`（win）在流水线步骤 2 |
| 高危确认 | `assess_risk` + `ui.confirm_high_risk`（normcase 逐字符比对，一次不匹配即 False）；非交互 AutoUI 恒 False → AbortError(3) |
| 批量上限 | `safety.validate_batch_size`，越界 ConfigError（不钳制） |
| 不跟随链接 | `os.walk(followlinks=False)` 默认保持 |
| 备份先于删除 | 流水线步骤 8/9 顺序 + 空间预检（×1.05）先于 new_run |
| Agent 契约 | `output.emit_*` 单行 stdout；人读输出 stderr；`--non-interactive` 无 input() |

## 13. 平台实现要点

- **normcase**：所有路径比较归一；posix 恒等无需分支；
- **volume_root**：posix 用 `os.path.ismount` 向上找最近挂载点（`/` 恒真）；Windows 用盘符根；
- **send2trash 后端**：win SHFileOperation（网络卷可能物理删除 → UNC 入口拒绝 + README 警告）；mac Finder/osxtrash（SSH/无 GUI 抛错 → trash_failed，不物理删除）；linux GIO/freedesktop（无 gvfs 抛错 → trash_failed）；
- **Windows 编码**：管道 stdout GBK 重编码会损坏中文 → JSON 显式 UTF-8 + `ensure_ascii=False`；PowerShell `$` 需单引号；
- **Linux 大小写**：文件系统敏感，`ignore_case` 语义按平台文档说明，可按任务关闭；
- **时间戳目录**：`time.strftime("%Y%m%d-%H%M%S") + f".{ms:03d}"` 防同秒冲突。

## 14. 测试策略

- 原则：`trash` 与 `UI` 全部注入 fake（不 monkeypatch 全局、不 mock input）；CLI 用 `typer.testing.CliRunner` 且 `monkeypatch.chdir(tmp_path)` 隔离根目录 config.json；REPL 直接调 `ReplSession.handle(line)`；
- conftest fixtures：`tree`（`{~$a.docx, keep.txt, x.tmp}` + `b/{y.TMP, ~$b.docx}` + `b/c/{z.tmp}`）、`fake_trash`（记录调用路径）、`fake_ui`（可编程 confirm 序列；confirm_high_risk 可设期望路径）；
- 平台测试：monkeypatch `sys.platform` 参数化 win32/darwin/linux；保护根用 fake 列表（`extra` + monkeypatch `protected_roots`）；posix 挂载点 monkeypatch `os.path.ismount`；
- json 模式断言：stdout 用 `python -c "import json,sys; json.load(sys.stdin)"` 验证可解析；text 模式断言 stdout/stderr 关键词；
- 真实回收站用例标记 `@pytest.mark.skipif(os.environ.get("EFC_REAL_TRASH") != "1")`，仅本地手动跑，不上 CI；

覆盖域（test_<module>.py 与模块一一对应）：

| 文件 | 覆盖要点 |
|---|---|
| test_config.py | 默认值/加载/合并优先级/max_batch 边界/坏 JSON/任务名重复/旧键拒绝/add_task 新建·更新·去重·替换·default/remove/list/resolve/default_tasks/save 原子且只写持久化字段/校验失败不写盘 |
| test_input.py | EFC_* 解析（含 EFC_TASK 换行·分号）/布尔数值转换/非法值/stdin 合法·非法·未知键·类型不符/TTY 拒绝/优先级链 |
| test_scanner.py | 命中·不命中·多模式 OR·ignore_case·匹配文件名非路径·递归开关·exclude·排序·非法正则·目录不存在·首模式归属 |
| test_safety.py | 三平台守卫参数化/保护根矩阵（win+posix）/卷根（含模拟挂载）/home 根规则/extra/normcase/UNC/batch 边界 |
| test_backup.py | 保留相对结构/copy2 保留 mtime/manifest 字段/多批次 |
| test_cleaner.py | fake trash 收绝对路径串·次数=成功数/备份失败未送 trash/trash 失败继续/confirm 拒绝 aborted/高危+AutoUI AbortError/13 文件 3 批/批间拒绝/0 命中/UNC·不支持平台/size·pattern·task_name 携带/空间不足 AbortError/排除 backup_dir·log_file |
| test_summary.py | 聚合（同 dir 合并·None 归"(无模式)"·只计 trashed）/format_bytes·duration/render 分节·空输入 |
| test_journal.py | 追加单行 JSONL/字段完整/result 判定/写失败不抛 |
| test_output.py | 信封格式/exit_code_for 三分支 |
| test_cli.py | scan json tasks 结构·无任务 exit 2·--task 选取/clean --yes·--dry-run·--max-batch 11·--no-log/task add 写盘·校验拒绝·default 标记/task list·remove/patterns 全部·单任务·空·--json/多任务（--task 重复·--all-tasks·默认清单）聚合·互斥·单条日志/agent（json 信封·code 1·2·3·--stdin·--non-interactive·env 优先级）/repl 拒绝 agent 标志/--json 简写等价 |
| test_repl.py | task 列·加载/dir·高危警告/pattern 追加·拒绝·清空/recursive/list/clean（fake 注入）写日志·总结/exit·quit/未知命令/未设任务提示 |

## 15. 附录：config.example.json

见 §4.1（随仓库提供同名文件，与实现保持一致）。
