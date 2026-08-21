# 开发计划 plan.md

> 基于 dev.md v1.0（2026-08-21）全部需求、接口、模块设计生成。每个 Task 为独立可执行项，按依赖顺序串行推进。

---

## T001 — 项目骨架搭建

- **Task ID**：T001
- **任务描述**：创建 Python 包目录结构 `src/efc/`，编写 `pyproject.toml`（含 `typer[all]>=0.12`、`send2trash>=1.8`、`pytest>=8.0`、`mypy>=1.0`、`ruff` 依赖），`src/efc/__init__.py` 声明 `__version__ = "1.0.0"`，入口点 `efc = efc.cli:main`。`pip install -e ".[dev]"` 可成功安装。
- **前置依赖 Task**：【无】
- **预估工作量**：0.5 人天
- **交付产出物**：`pyproject.toml`、`src/efc/__init__.py`、项目目录骨架
- **验收标准**：
  - `pip install -e ".[dev]"` 无报错
  - `efc --help` 可运行（Typer 自动生成帮助）
  - `mypy src/` 无类型错误

---

## T002 — 异常类与数据模型

- **Task ID**：T002
- **任务描述**：实现 `exceptions.py`（EfcError 基类含 `exit_code` 类属性，ConfigError/PlatformError/PatternError/ScanError/AbortError 子类各自覆盖 `exit_code`）和 `models.py`（FileMatch、ScanResult、RiskDecision、FileOutcome、CleanOutcome 五个 dataclass，含 `@property` 方法 `trashed`/`failed`）。
- **前置依赖 Task**：【无】
- **预估工作量**：0.5 人天
- **交付产出物**：`src/efc/exceptions.py`、`src/efc/models.py`
- **验收标准**：
  - 所有 dataclass 字段与 dev.md §6.1 完全一致
  - `type(AbortError("msg")).exit_code == 3`
  - `CleanOutcome.trashed` 返回 `[f for f in results if f.status == "trashed"]`
  - `CleanOutcome.failed` 返回 `backup_failed + trash_failed`

---

## T003 — 配置核心：加载 / 合并 / 校验 / 保存

- **Task ID**：T003
- **任务描述**：实现 `config.py` 中的 `AppConfig` dataclass（含 `targets: list[Target]`）和 `Target` dataclass；`load_config(path)` 按 `$EFC_CONFIG → ./config.json → ~/.efc/config.json` 顺序加载 JSON 并校验（`max_batch` 1..10、正则可编译、`targets` 中不允许 `name="default"` 与顶层字段并存）；`merged(base, overrides)` 按优先级叠加（None 不覆盖、列表整体替换）；`save_config(cfg, path)` 原子写入（临时文件 + `os.replace`、UTF-8、indent=2）。
- **前置依赖 Task**：T002
- **预估工作量**：1.5 人天
- **交付产出物**：`src/efc/config.py`（`AppConfig`、`Target`、`load_config`、`merged`、`save_config`、`validate`）
- **验收标准**：
  - 加载合法 JSON 返回正确 `AppConfig`
  - `max_batch=11` → `ConfigError`（exit 2）
  - 配置文件缺失且无 env → 返回默认值，不报错
  - 指定路径不存在 → `ConfigError`
  - `save_config` 后文件内容与 `AppConfig` 序列化一致，UTF-8 无乱码
  - targets 中 `name="default"` 且顶层字段同时存在 → `ConfigError`

---

## T004 — 配置持久化：目标的增删查

- **Task ID**：T004
- **任务描述**：实现 `add_target`（name=="default" 写顶层字段/否则写 targets，patterns 追加去重或 `replace_patterns=True` 整体替换，落盘前校验：目录存在、正则可编译、完整 config 再 validate）；`remove_target`（按 name 或 dir 移除，命中默认目标清空顶层字段）；`list_targets`（顶层默认目标并入 targets 按 name 排序）；`resolve_target`（按 name 查找或返回默认目标，不存在抛 ConfigError）。
- **前置依赖 Task**：T003
- **预估工作量**：1.5 人天
- **交付产出物**：`src/efc/config.py`（新增 `add_target`、`remove_target`、`list_targets`、`resolve_target`）
- **验收标准**：
  - `add_target(name="default", dir=..., patterns=[...])` 写入 `cfg.target_dir`/`cfg.filename_patterns`
  - `add_target(name="downloads", ...)` 新增 targets 条目，同名重复 → 追加去重
  - `replace_patterns=True` → 整体替换，不追加
  - `remove_target(name="downloads")` 后 `list_targets` 不再包含此条目
  - `resolve_target("不存在")` → `ConfigError`
  - add 阶段校验失败 → 不写盘，抛 ConfigError

---

## T005 — Agent 输入：环境变量与 --stdin 解析

- **Task ID**：T005
- **任务描述**：实现 `read_env_overrides()`（解析全部 `EFC_*` 环境变量为覆盖字典，布尔 `1/true/0/false`、数值可转 int、列表型换行或 `;` 分隔）；`read_stdin_payload()`（读取 stdin JSON 并校验：非 TTY 才允许，未知键/类型不符 → ConfigError，`command` 字段可选）；`merge_overrides(cfg, *layers)`（按优先级依次叠加，列表键整体替换，每层后 validate）。
- **前置依赖 Task**：T003
- **预估工作量**：1.5 人天
- **交付产出物**：`src/efc/config.py`（新增 `read_env_overrides`、`read_stdin_payload`、`merge_overrides`）
- **验收标准**：
  - `EFC_PATTERNS="a\\.tmp\\nb\\.bak"` → 解析为 `["a\\.tmp", "b\\.bak"]`
  - `EFC_PATTERNS="a\\.tmp;b\\.bak"` → 同样结果（分号分隔）
  - `EFC_DRY_RUN=1` → `{"dry_run": True}`
  - stdin 输入 JSON 含未知键 `{"foo": 1}` → `ConfigError`（exit 2）
  - stdin 输入 `{"patterns": "not_a_list"}` → `ConfigError`（类型不符）
  - TTY 环境下 `sys.stdin.isatty() == True` + `--stdin` → `ConfigError`
  - `merge_overrides` 优先级：CLI > stdin > env > config

---

## T006 — 配置与输入公共模块测试

- **Task ID**：T006
- **任务描述**：编写 `test_config.py`（覆盖 dev.md §11.2 test_config 行全部用例：默认值、JSON 加载、CLI 覆盖、max_batch 边界、add/remove/list/resolve、save 原子写、校验失败拒绝写盘）和 `test_input.py`（覆盖 dev.md §11.2 test_input 行：环境变量解析、stdin 负载、优先级、TTY 拒绝、未知键/类型不符）。
- **前置依赖 Task**：T003、T004、T005
- **预估工作量**：1 人天
- **交付产出物**：`tests/test_config.py`、`tests/test_input.py`
- **验收标准**：
  - 两个测试文件均可独立执行 `pytest tests/test_config.py` / `pytest tests/test_input.py` 全绿
  - 覆盖 dev.md 对应行全部用例（至少 20 个断言场景）

---

## T007 — 扫描器：正则编译与目录扫描

- **Task ID**：T007
- **任务描述**：实现 `compile_patterns`（遍历 patterns 逐条 `re.compile`，`ignore_case` 时加 `re.IGNORECASE`，非法正则 → `PatternError` 含具体模式名）；`scan`（`recursive=True` 用 `os.walk(followlinks=False)`，`False` 用 `root.iterdir()`，仅收集 `is_file()`，对 `file.name` 做 `p.search(name)`，**记录命中的第一个模式**写入 `FileMatch.pattern`，`exclude` 的目录按 `resolve + normcase` 整棵跳过，返回 `ScanResult` 按 `str(path)` 排序）。
- **前置依赖 Task**：T002
- **预估工作量**：1 人天
- **交付产出物**：`src/efc/scanner.py`
- **验收标准**：
  - 多模式时文件命中第一个模式写入 `FileMatch.pattern`
  - `recursive=True` 命中子目录文件，`False` 仅顶层
  - `exclude=[Path("backup")]` 跳过该目录及其子目录
  - 目标目录不存在 → `ScanError`
  - 非法正则 → `PatternError`（消息含该模式原文）
  - 输出按 `str(path)` 排序（确定性）

---

## T008 — 扫描器测试

- **Task ID**：T008
- **任务描述**：编写 `test_scanner.py`（覆盖 dev.md §11.2 test_scanner 行：正则匹配/不命中、多模式 OR、ignore_case 大小写、文件名匹配非路径、递归开关、exclude、排序、非法正则、目录不存在）。
- **前置依赖 Task**：T007
- **预估工作量**：0.5 人天
- **交付产出物**：`tests/test_scanner.py`
- **验收标准**：
  - `pytest tests/test_scanner.py` 全绿
  - 覆盖 dev.md 对应行全部用例（至少 10 个断言场景）

---

## T009 — 安全守卫

- **Task ID**：T009
- **任务描述**：实现 `ensure_win32()`（`sys.platform != "win32"` → `PlatformError`）；`is_unc(path)`（`PureWindowsPath.drive` 以 `\\\\` 开头）；`protected_roots(extra)`（展开 `%SystemRoot%`/`%ProgramFiles%`/`%ProgramFiles(x86)%`/`%ProgramData%`/`%USERPROFILE%` 并 resolve，加 extra）；`drive_root(path)`；`assess_risk(target, recursive, extra)`（高危判定：盘符根 / 等于保护根 / 保护根后代 / recursive 下保护根祖先，返回 `RiskDecision`）；`validate_batch_size(n)`（`1 <= n <= 10` 否则 ConfigError）。
- **前置依赖 Task**：T002
- **预估工作量**：1 人天
- **交付产出物**：`src/efc/safety.py`
- **验收标准**：
  - `sys.platform = "linux"` → `ensure_win32()` 抛 `PlatformError`
  - `is_unc(Path("\\\\server\\share"))` → True
  - `assess_risk(Path("C:\\Windows\\System32"), False, [])` → high_risk=True, reason 含 "C:\\Windows"
  - `assess_risk(Path("C:\\"), False, [])` → high_risk=True（盘符根）
  - `assess_risk(Path("C:\\"), True, [])` → high_risk=True（祖先）
  - `assess_risk(Path("D:\\tmp"), False, [])` → high_risk=False
  - `validate_batch_size(11)` → ConfigError

---

## T010 — 备份模块

- **Task ID**：T010
- **任务描述**：实现 `BackupRun` 类（`__init__` 以 `base_dir/<YYYYmmdd-HHMMSS.fff>/` 建目录，`backup_file(src, relative)` 用 `shutil.copy2` 拷贝到 `root/relative`，`parents=True`，异常向上抛；`write_manifest(outcomes, meta)` 写 `manifest.json`：`{run_at, target_dir, patterns, recursive, results: [{original, backup, status, size, error}]}`，UTF-8、indent=2）。`new_run(base_dir)` 工厂函数。
- **前置依赖 Task**：T002
- **预估工作量**：1 人天
- **交付产出物**：`src/efc/backup.py`
- **验收标准**：
  - 备份保留相对目录结构（`a/b/x.tmp` → `<run>/a/b/x.tmp`）
  - `copy2` 保留 mtime
  - `manifest.json` 字段完整（original/backup/status/size/error）
  - 备份失败异常向上传播（由 cleaner 捕获）

---

## T011 — 安全与备份模块测试

- **Task ID**：T011
- **任务描述**：编写 `test_safety.py`（覆盖 dev.md §11.2 test_safety 行：高危矩阵、normcase、UNC、ensure_win32 monkeypatch、batch_size 边界）和 `test_backup.py`（覆盖 dev.md §11.2 test_backup 行：保留结构、copy2 mtime、manifest 字段、多批次）。
- **前置依赖 Task**：T009、T010
- **预估工作量**：1 人天
- **交付产出物**：`tests/test_safety.py`、`tests/test_backup.py`
- **验收标准**：
  - 两个测试文件均可独立执行全绿
  - 覆盖 dev.md 对应行全部用例

---

## T012 — UI 模块

- **Task ID**：T012
- **任务描述**：实现 `UI` Protocol（`confirm`/`confirm_high_risk`/`confirm_next_batch`/`show_matches`/`show_summary`/`error`）；`AutoUI`（confirm/confirm_next_batch → True，confirm_high_risk → False）；`ConsoleUI`（基于 typer.confirm/rich，confirm_high_risk 逐字符输入路径一次不匹配即 False，支持 `no_color`/`progress`/`interactive` 开关，`--format json` 时 `no_color=True`/`progress=False`/人读输出走 stderr）。
- **前置依赖 Task**：T002、T007（show_matches 需 ScanResult）
- **预估工作量**：1 人天
- **交付产出物**：`src/efc/ui.py`
- **验收标准**：
  - `AutoUI.confirm("msg")` → True
  - `AutoUI.confirm_high_risk(path, reason)` → False
  - `ConsoleUI(no_color=True, progress=False)` 不触发 rich 彩色输出
  - `ConsoleUI` 高危确认输入正确路径 → True，输入错误 → False

---

## T013 — 清理流水线

- **Task ID**：T013
- **任务描述**：实现 `Cleaner` 类（构造函数注入 `config`/`ui`/`trash`），`run()` 按 dev.md §6.6 流水线步骤 1-10 执行：记录开始时间 → ensure_win32 + UNC → 目标目录存在性 → assess_risk（高危 → confirm_high_risk） → compile_patterns + scan(排除 backup_dir/log_file) → 零命中 → show_matches + confirm → 备份空间预检（`disk_usage` 可用 < 总字节×1.05 → AbortError） → backup.new_run → 分批循环（批间 confirm_next_batch，批内逐文件备份→trash，失败跳过并记录） → write_manifest → 记录 duration_seconds/total_bytes → 返回 CleanOutcome。干运行（dry_run）跳过实际 trash 调用但正常走 scan/confirm 流程。
- **前置依赖 Task**：T002、T007、T009、T010、T012
- **预估工作量**：3 人天
- **交付产出物**：`src/efc/cleaner.py`
- **验收标准**：
  - 高危 + AutoUI → `AbortError`，零 trash 调用
  - 备份失败 → 文件 status=backup_failed 且**未送** trash
  - trash 异常 → status=trash_failed 且继续下一个
  - confirm 拒绝 → aborted=True，零 trash
  - 13 文件 max_batch=5 → 3 批（5/5/3）
  - 批间拒绝 → 停止后续批次
  - 备份空间不足 → `AbortError`（exit 3），零 trash 调用
  - `FileOutcome.size` 和 `FileOutcome.pattern` 正确携带
  - `outcome.duration_seconds > 0` 且 `outcome.total_bytes` 为 trashed 文件合计
  - scan 排除 backup_dir 和 log_file（二者位于目标内时）

---

## T014 — 输出模块：总结 / 日志 / 响应

- **Task ID**：T014
- **任务描述**：实现 `summary.py`（`build_summary` 按目标目录→模式聚合，只统计 status=="trashed"，同 dir 合并，pattern=None 归入 "(无模式)"，`format_bytes`/`format_duration`/`render_summary` 按 dev.md §7.7 格式渲染）；`journal.py`（`ExecutionLog` 类 `record` 方法追加一行 JSONL 到 `log_file`，写失败仅 stderr 警告不抛异常，`result` 判定优先级：aborted > partial > dry_run > completed）；`output.py`（`emit_success`/`emit_error` 输出单行 JSON 信封到 stdout，`exit_code_for` 映射 EfcError→exit_code / UsageError→2 / 其余→1）。
- **前置依赖 Task**：T002
- **预估工作量**：2 人天
- **交付产出物**：`src/efc/summary.py`、`src/efc/journal.py`、`src/efc/output.py`
- **验收标准**：
  - `build_summary` 两个同 dir 的 outcome → 合并为一个 TargetStats
  - `render_summary` 多目标含 "一、二、" 编号分节
  - `ExecutionLog.record` 追加一行 JSONL，UTF-8 ensure_ascii=False
  - 写失败时不抛异常，仅 stderr 警告
  - `emit_success({"key": "val"})` → stdout 一行 `{"data": {"key": "val"}}`
  - `emit_error(2, "msg")` → stdout 一行 `{"code": 2, "msg": "msg"}`
  - `exit_code_for(UsageError(...))` → 2

---

## T015 — 流水线及输出模块测试

- **Task ID**：T015
- **任务描述**：编写 `test_cleaner.py`（覆盖 dev.md §11.2 test_cleaner 行全部用例）、`test_summary.py`（覆盖 dev.md §11.2 test_summary 行）、`test_journal.py`（覆盖 dev.md §11.2 test_journal 行）、`test_output.py`（覆盖 dev.md §11.2 test_output 行）。所有测试用 fake trash + FakeUI 注入，不 mock input。
- **前置依赖 Task**：T013、T014
- **预估工作量**：2 人天
- **交付产出物**：`tests/test_cleaner.py`、`tests/test_summary.py`、`tests/test_journal.py`、`tests/test_output.py`
- **验收标准**：
  - 四个测试文件均可独立执行全绿
  - 覆盖 dev.md 对应行全部用例（合计至少 30 个断言场景）

---

## T016 — CLI 基础：全局回调与异常处理入口

- **Task ID**：T016
- **任务描述**：实现 `cli.py` 中的 `app = typer.Typer()`、`AgentState` dataclass、`@app.callback()` 全局回调（`--format`/`--non-interactive`/`--stdin` 存入 `ctx.obj`）；`_resolve_format()`（在 `app()` 前独立从 `sys.argv` 和 `EFC_FORMAT` 解析 format，不依赖 callback）；`main()`（三层异常捕获：UsageError→2 / EfcError→`type(e).exit_code` / 未知→1，text/json 双分支输出）。repl 的 agent 限制：`--format json`/`--non-interactive`/`--stdin` 任一带上 → ConfigError（exit 2）。
- **前置依赖 Task**：T002、T014
- **预估工作量**：1.5 人天
- **交付产出物**：`src/efc/cli.py`（骨架：app、callback、AgentState、_resolve_format、main、repl 骨架）
- **验收标准**：
  - `efc --format json --help` 输出人类文本（不进入 JSON 信封）
  - `efc --non-interactive --stdin repl` → exit 2
  - `UsageError` 在 json 模式 → `{"code":2,"msg":"..."}`（stdout）+ exit 2
  - 未知异常 → `{"code":1,"msg":"..."}`（stdout）+ exit 1
  - `_resolve_format()` 在 callback 未执行情况下也能正确返回 format

---

## T017 — scan 命令

- **Task ID**：T017
- **任务描述**：实现 `efc scan` 命令（`--target`/`--dir`/`--pattern`/`--recursive`/`--config`/`--json`/`-v`）。流程：加载配置 → 合并参数 → resolve_target → compile_patterns → scan → text 模式输出 rich 表格到 stderr（json 模式静默） → json 模式 `emit_success`（信封 `{"data": {root, recursive, scanned_dirs, count, matches}}`）。`--json` 是 `--format json` 简写。
- **前置依赖 Task**：T003、T004、T005、T007、T016
- **预估工作量**：1 人天
- **交付产出物**：`src/efc/cli.py`（新增 scan 命令）
- **验收标准**：
  - `efc scan --dir D:\tmp --pattern "\.tmp$"` → 人类可读表格
  - `efc --format json scan --dir D:\tmp --pattern "\.tmp$"` → stdout 单行 JSON 信封
  - 无 dir 且无 config → exit 2 + 提示

---

## T018 — clean 命令（text 模式，单目标）

- **Task ID**：T018
- **任务描述**：实现 `efc clean` 命令（`--target`/`--dir`/`--pattern`/`--recursive`/`--config`/`--yes`/`--no-backup`/`--max-batch`/`--dry-run`/`--no-log`/`-v`）。流程：加载配置 → 合并参数 → resolve_target → 构建 Cleaner → run → 输出总结 → 写日志。text 模式人类可读输出（进度、批次、总结）走 stderr。
- **前置依赖 Task**：T003、T004、T005、T013、T014、T016
- **预估工作量**：1.5 人天
- **交付产出物**：`src/efc/cli.py`（新增 clean 命令 text 模式）
- **验收标准**：
  - `efc clean --yes --dir /tmp/test --pattern "\.tmp$"` → 完成清理，输出总结
  - `--dry-run` → 零 trash 调用，result=dry_run
  - `--no-backup` → 不执行备份
  - `--no-log` → 不写日志文件
  - `--yes` 跳过确认但高危仍拒
  - 0 命中 → 输出提示后 exit 0

---

## T019 — repl 命令（text 模式骨架）

- **Task ID**：T019
- **任务描述**：实现 `efc repl` 命令（`--config`/`-v`）。检查 agent 标志（`--format json`/`--non-interactive`/`--stdin` 任一 → exit 2）。启动 `ReplSession` 并进入 `run()` 循环（详细 REPL 实现见 T025）。
- **前置依赖 Task**：T016
- **预估工作量**：0.5 人天
- **交付产出物**：`src/efc/cli.py`（新增 repl 命令入口 + agent 限制检查）
- **验收标准**：
  - `efc repl` → 进入交互提示符 `efc> `
  - `efc --format json repl` → exit 2 + 错误消息
  - `efc --non-interactive repl` → exit 2

---

## T020 — CLI 基础命令测试（scan/clean/repl text）

- **Task ID**：T020
- **任务描述**：编写 `test_cli.py` 中 TestScan/TestClean/TestRepl 类，用 `typer.testing.CliRunner` 测试 scan（--json 结构、无 dir 时 exit 2）、clean（--yes 删除成功、--dry-run 零副作用、--max-batch 11 exit 2、--no-log 无日志、总结输出）、repl（agent 标志拒绝）。使用 fake trash + FakeUI 注入。
- **前置依赖 Task**：T017、T018、T019
- **预估工作量**：1 人天
- **交付产出物**：`tests/test_cli.py`（TestScan/TestClean/TestRepl 类）
- **验收标准**：
  - `pytest tests/test_cli.py` 中所有 text 模式用例全绿
  - 覆盖 dev.md §11.2 test_cli 行中 scan/clean/repl text 相关用例

---

## T021 — config 命令与 patterns 命令

- **Task ID**：T021
- **任务描述**：实现 `efc config` 子命令组（`add`：`--dir`/`--pattern`/`--name`/`--recursive`/`--replace-patterns`/`--config`；`list`：`--config`/`--json`；`remove`：`--name`/`--dir`/`--config`）和 `efc patterns` 命令（`--target`/`--config`/`--json`）。所有命令支持 text/json 双输出（`--json` 为 `--format json` 简写）。config add 校验失败不写盘；config list 显示全部目标与设置；patterns 空结果 exit 0。
- **前置依赖 Task**：T004、T005、T016
- **预估工作量**：2 人天
- **交付产出物**：`src/efc/cli.py`（新增 config 子命令组 + patterns 命令）
- **验收标准**：
  - `efc config add --dir X --pattern A --pattern B` → 写盘，`efc scan` 可直接使用
  - `efc config add --name downloads --dir X --pattern A` → 追加到 targets
  - 重复 add 同名 → 追加去重
  - `--replace-patterns` → 整体替换
  - `efc config list` → 显示全部目标与设置
  - `efc config remove --name downloads` → 移除后 scan 不再使用
  - `efc patterns` → 列出当前模式，空配置 exit 0
  - `efc patterns --json` → 输出信封 `{"data": {...}}`
  - 坏正则/不存在目录 → exit 2 且不写盘

---

## T022 — 多目标 clean 与 Agent JSON 双输出

- **Task ID**：T022
- **任务描述**：扩展 `efc clean` 支持 `--target` 可重复 + `--all-targets`（与 `--dir` 互斥）。多目标时循环构造 Cleaner 逐目标执行，收集所有 CleanOutcome → `build_summary` → `render_summary`（一、二、编号分节）→ `ExecutionLog.record`（一条汇总日志）。所有命令（scan/clean/config add/list/remove/patterns）在 `--format json` 时统一调用 `emit_success`/`emit_error`，完整实现 JSON 双输出。
- **前置依赖 Task**：T017、T018、T021、T014
- **预估工作量**：2.5 人天
- **交付产出物**：`src/efc/cli.py`（clean 多目标扩展 + 全命令 JSON 输出集成）
- **验收标准**：
  - `efc clean --target A --target B` → 两个目标先后执行，聚合总结含一、二、分节
  - `efc clean --all-targets` → 清理 config 中全部目标
  - `--target` 与 `--all-targets` 同用 → exit 2
  - `--target` 与 `--dir` 同用 → exit 2
  - 任一目标失败 → exit 4；任一目标 abort → exit 3；否则 exit 0
  - 日志仅一条记录含全部目标的 files
  - `efc --format json config add --dir X --pattern A` → stdout 信封 `{"data": {"saved": true, ...}}`
  - `efc --format json config list` → stdout 信封含 targets 数组
  - `efc --format json clean --target A` → stdout 信封含完整 data（result/trashed/targets/files）

---

## T023 — Agent 交互集成

- **Task ID**：T023
- **任务描述**：在 clean 命令中整合 `--non-interactive` 行为：`ConsoleUI(interactive=False, no_color=True, progress=False)`，确认自动通过（高危仍拒→code 3）；`--stdin` 从 stdin 读取业务参数并 `merge_overrides`；`--format json` 强制 no_color/progress，人读输出走 stderr，stdout 仅一行 JSON 信封。确保 `--non-interactive` 全程无 `input()` 调用、无阻塞。
- **前置依赖 Task**：T012、T016、T018、T022
- **预估工作量**：1.5 人天
- **交付产出物**：`src/efc/cli.py`（clean 命令 Agent 交互集成）
- **验收标准**：
  - `efc --non-interactive clean --target X` → 无 input() 调用，执行完立即退出
  - `efc --format json --non-interactive clean --target X` → stdout 单行 JSON，stderr 无进度条
  - `echo '{"dir":"...","patterns":[...]}' | efc --non-interactive --stdin clean` → 使用 stdin 负载
  - 高危 + non-interactive → `{"code":3,"msg":"..."}` + exit 3
  - `--stdin` + TTY → exit 2

---

## T024 — CLI 高级功能测试（config/patterns/agent/多目标）

- **Task ID**：T024
- **任务描述**：编写 `test_cli.py` 中 TestConfig/TestPatterns/TestAgent/TestMultiTarget 类，用 CliRunner 测试 config add/list/remove（写盘/校验拒绝/原子性）、patterns（默认/--target/空/--json）、多目标 clean（聚合总结/退出码/互斥）、agent 模式（json 信封/--stdin/--non-interactive/高危 code 3/env 优先级）。
- **前置依赖 Task**：T021、T022、T023
- **预估工作量**：1.5 人天
- **交付产出物**：`tests/test_cli.py`（TestConfig/TestPatterns/TestAgent/TestMultiTarget 类）
- **验收标准**：
  - `pytest tests/test_cli.py` 全绿
  - 覆盖 dev.md §11.2 test_cli 行中 config/patterns/agent/多目标 相关用例
  - 测试不依赖真实 send2trash（fake trash 注入）

---

## T025 — REPL 完整实现

- **Task ID**：T025
- **任务描述**：实现 `ReplSession` 类（`handle` 方法用 `shlex.split` 解析命令，返回 bool 表示是否继续）。实现全部命令：`dir`（设置/显示目标，高危即时警告）、`pattern`（追加/列出/清空，即时编译校验）、`recursive`（on/off）、`list`（scan 预览）、`clean`（构造 Cleaner.run，结束后写日志并输出总结，与 CLI 一致）、`status`、`help`、`exit`/`quit`。`run` 循环读取 input，EOF/Ctrl+C 优雅退出。未知命令不退出。
- **前置依赖 Task**：T002、T003、T007、T013、T014
- **预估工作量**：2 人天
- **交付产出物**：`src/efc/repl.py`
- **验收标准**：
  - `dir D:\Downloads` → 设置目标并显示高危评估
  - `pattern ^~\$` → 追加成功，非法正则 → 提示错误不追加
  - `pattern clear` → 清空模式列表
  - `list` → 输出 scan 命中表格
  - `clean` → 走完整流水线，输出总结，写日志
  - `exit`/`quit`/EOF → 退出
  - 未知命令 → 打印帮助提示，不退出
  - Ctrl+C 连按或 EOF → 退出

---

## T026 — REPL 测试

- **Task ID**：T026
- **任务描述**：编写 `test_repl.py`（调用 `ReplSession.handle(line)` 直接测试，不 mock input）。覆盖：dir 设置/高危警告、pattern 追加/非法拒绝/清空、recursive 切换、list 输出、clean 执行（fake trash + FakeUI）、exit/quit 返回 False、未知命令、未设 dir 时 clean 提示。
- **前置依赖 Task**：T025
- **预估工作量**：1 人天
- **交付产出物**：`tests/test_repl.py`
- **验收标准**：
  - `pytest tests/test_repl.py` 全绿
  - 覆盖 dev.md §11.2 test_repl 行全部用例

---

## T027 — 文档与最终验证

- **Task ID**：T027
- **任务描述**：编写 `README.md`（含安装、快速上手、config.json 说明、安全模型、FAQ（如何从备份恢复）、`efc restore` Roadmap）；`config.example.json`（与最终实现一致）；执行收尾核对：全量 `pytest` 绿、grep 检查 `src/` 下无删除 API（`os.remove/os.unlink/.unlink(/shutil.rmtree/os.rmdir`）、真实回收站冒烟（`EFC_REAL_TRASH=1`）、逐条勾选 dev.md §13 验收清单。
- **前置依赖 Task**：T024、T026（全量测试就绪）
- **预估工作量**：1.5 人天
- **交付产出物**：`README.md`、`config.example.json`、验证通过的全量 pytest 输出
- **验收标准**：
  - `pytest -q` 全量通过（≥ 40 个断言场景）
  - `grep -rn -E "os\.remove|os\.unlink|\.unlink\(|shutil\.rmtree|os\.rmdir" src/` 零结果
  - `EFC_REAL_TRASH=1 pytest` 在 Windows 本地通过
  - 逐条勾选 dev.md §13 验收清单全部通过
  - README 包含安装/快速上手/config 说明/安全模型/FAQ
  - `pip install -e ".[dev]"` 后 `efc --version` 正常

---

## 依赖关系总览

```
T001 ──→ T002 ──→ T003 ──→ T004 ──→ T005 ──→ T006
                ↘ T007 ──→ T008
                ↘ T009 ──→ T010 ──→ T011
                ↘ T012 ──→ T013 ──→ T014 ──→ T015
                          ↑                       
T016 ──→ T017 ──→ T018 ──→ T020
   ↘ T019 ──→ T020
   ↘ T021 ──→ T022 ──→ T023 ──→ T024
   T025 ──→ T026
                         T024 + T026 ──→ T027
```

**关键路径**：T001 → T002 → T003 → T004 → T005 → T006 → T007 → T013 → T014 → T016 → T018 → T022 → T023 → T024 → T027（约 15 个任务串行）

**预估总工作量**：约 28.5 人天（≈ 6 周单人）