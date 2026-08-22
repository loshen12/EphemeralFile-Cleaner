# Project Plan — EphemeralFile Cleaner 开发计划

> 版本：v2.0（2026-08-22）｜基于 PRD.md + Spec.md，替代原 plan.md（v1.1 重写版）
> 任务编号 T001-T027 保持不变，提交 Footer 沿用 `Task: Txxx`。
> 当前状态：**T001-T005 已完成**，进度 5/27（5.5 / 37 人天 ≈ 14.9%）；下一任务 **T006**。

## 1. 里程碑

| 里程碑 | 范围 | 出口条件 |
|---|---|---|
| M0 骨架（✅ 完成） | T001 | 可安装、`efc --help/--version` 可用、mypy 零错误 |
| M1 数据与基础层 | T002-T006、T007-T011 | models/exceptions/config(任务清单)/scanner/safety(三平台)/backup 及各自测试全绿 |
| M2 清理引擎与输出 | T012-T015 | Cleaner 流水线 + summary/journal/output 及测试全绿（fake 注入） |
| M3 CLI 基础（首个可用版本） | T016-T020 | `efc scan/clean/repl`（text 模式）可用，CLI 测试全绿 |
| M4 任务管理与 Agent 模式 | T021-T024 | `efc task */patterns`、多任务 clean、json/--stdin/--non-interactive 全量可用 |
| M5 REPL 完整与收尾 | T025-T027 | REPL 全命令、全量测试、三平台冒烟、README/config.example.json 交付 |

M3 后即获得可日常使用的 CLI；M4 完成后 Agent 可接手自动化调用。

## 2. 任务明细

状态：✅ 完成 · 🔶 进行中 · ⬜ 未开始

### T001 — 项目骨架搭建 ✅（2026-08-21，提交 fc4dab8）

- **依赖**：无｜**工作量**：0.5 人天｜**产出**：`pyproject.toml`、`src/efc/__init__.py`、目录骨架
- **验收**：`pip install -e ".[dev]"` 无报错；`efc --help` 可运行；`mypy src/` 零错误（已于 2026-08-22 在 macOS+uv 环境复核通过）

### T002 — 异常类与数据模型 ✅（2026-08-22）

- **依赖**：无｜**工作量**：0.5 人天｜**产出**：`src/efc/exceptions.py`、`src/efc/models.py`
- **验收**：dataclass 字段与 Spec §3 完全一致；`AbortError.exit_code == 3`；`CleanOutcome.trashed/failed` 语义正确；`task_name` 为 `str | None`

### T003 — 配置核心：Task 模型 / 加载 / 合并 / 校验 / 保存 ✅（2026-08-23）

- **依赖**：T002｜**工作量**：1.5 人天｜**产出**：`config.py`（AppConfig/Task/load_config/merged/save_config/validate）
- **验收**：合法 JSON → 正确 AppConfig（含 tasks 与 `~` 展开）；`max_batch=11` → ConfigError；缺配置文件 → 默认值不报错；指定路径不存在 → ConfigError；save 原子写且只含持久化字段；任务名重复、v1.0 旧顶层键 → ConfigError

### T004 — 任务清单持久化：任务的增删查 ✅（2026-08-23）

- **依赖**：T003｜**工作量**：1.5 人天｜**产出**：`config.py` 新增 add_task/remove_task/list_tasks/resolve_task/default_tasks
- **验收**：新建（dir 必填）与同名更新（仅覆盖显式字段）；追加去重 / replace_patterns 整体替换；default 标记设置与保持；remove 后 list 不含；resolve 未知名 → ConfigError；default_tasks 只含 default=True 且按序；校验失败不写盘

### T005 — Agent 输入：环境变量与 --stdin 解析 ✅（2026-08-23）

- **依赖**：T003｜**工作量**：1.5 人天｜**产出**：`config.py` 新增 read_env_overrides/read_stdin_payload/merge_overrides
- **验收**：`EFC_PATTERNS` 换行/分号分隔均可解析；`EFC_TASK="a\nb"` → `{"task":["a","b"]}`；`EFC_DRY_RUN=1` → `{"dry_run":True}`；stdin 未知键/类型不符 → ConfigError；TTY + `--stdin` → ConfigError；merge 优先级 CLI > stdin > env > config

### T006 — 配置与输入测试

- **依赖**：T003、T004、T005｜**工作量**：1 人天｜**产出**：`tests/test_config.py`、`tests/test_input.py`
- **验收**：两文件独立执行全绿；覆盖 Spec §14 对应行全部用例（≥20 断言场景）

### T007 — 扫描器：正则编译与目录扫描

- **依赖**：T002｜**工作量**：1 人天｜**产出**：`src/efc/scanner.py`
- **验收**：首命中模式写入 FileMatch.pattern；递归开关行为正确；exclude 整棵跳过；目录不存在 → ScanError；非法正则 → PatternError（含原文）；输出按 str(path) 排序

### T008 — 扫描器测试

- **依赖**：T007｜**工作量**：0.5 人天｜**产出**：`tests/test_scanner.py`
- **验收**：独立执行全绿；覆盖 Spec §14 对应行（≥10 断言场景）

### T009 — 平台与安全守卫（三平台）

- **依赖**：T002｜**工作量**：1.5 人天｜**产出**：`src/efc/safety.py`
- **验收**：参数化 `sys.platform`：win32/darwin/linux 通过、aix → PlatformError；`is_unc` win True / posix False；`assess_risk` 覆盖 Spec §8 全矩阵（win+posix 保护根及后代、卷根 `C:\` 与 `/`、recursive 祖先、home 根高危但子目录不高危）；`validate_batch_size(11)` → ConfigError

### T010 — 备份模块

- **依赖**：T002｜**工作量**：1 人天｜**产出**：`src/efc/backup.py`
- **验收**：保留相对结构；copy2 保留 mtime；manifest 字段完整（original/backup/status/size/error）；备份异常向上抛（cleaner 捕获）

### T011 — 安全与备份测试（三平台参数化）

- **依赖**：T009、T010｜**工作量**：1 人天｜**产出**：`tests/test_safety.py`、`tests/test_backup.py`
- **验收**：独立执行全绿；覆盖 Spec §14 对应行（含 monkeypatch ismount 模拟挂载点）

### T012 — UI 模块

- **依赖**：T002、T007｜**工作量**：1 人天｜**产出**：`src/efc/ui.py`
- **验收**：AutoUI confirm→True、confirm_high_risk→False；ConsoleUI(no_color,progress) 不触发彩色；高危确认输入正确 normcase 路径→True、错误→False

### T013 — 清理流水线

- **依赖**：T002、T007、T009、T010、T012｜**工作量**：3 人天｜**产出**：`src/efc/cleaner.py`
- **验收**：Spec §7 十步全覆盖——高危+AutoUI→AbortError 零调用；备份失败未送 trash；trash 失败继续；confirm 拒绝 aborted；13 文件 max_batch=5 → 3 批（5/5/3）；批间拒绝停止；空间不足→AbortError 零调用；FileOutcome 携带 size/pattern、CleanOutcome 携带 task_name；scan 排除 backup_dir/log_file；dry_run 零 trash

### T014 — 输出模块：总结 / 日志 / 响应

- **依赖**：T002｜**工作量**：2 人天｜**产出**：`src/efc/summary.py`、`journal.py`、`output.py`
- **验收**：build_summary 同 dir 合并、None 归"(无模式)"、只计 trashed；render 含一、二、分节；ExecutionLog 追加单行 JSONL（tasks 含具体文件）、写失败仅警告；emit_success/emit_error 单行信封；exit_code_for 三分支

### T015 — 流水线及输出测试

- **依赖**：T013、T014｜**工作量**：2 人天｜**产出**：`tests/test_cleaner.py`、`test_summary.py`、`test_journal.py`、`test_output.py`
- **验收**：四文件独立执行全绿；覆盖 Spec §14 对应行（合计 ≥30 断言场景）

### T016 — CLI 基础：全局回调与异常处理入口

- **依赖**：T002、T014｜**工作量**：1.5 人天｜**产出**：`cli.py` 骨架（app/callback/AgentState/_resolve_format/main/repl 入口）
- **验收**：`--format json --help` 输出人类文本；`--non-interactive --stdin repl` → exit 2；UsageError json → `{"code":2}` + exit 2；未知异常 → code 1；不支持平台 → exit 2；`_resolve_format` 不依赖 callback

### T017 — scan 命令（任务解析 + tasks 数组输出）

- **依赖**：T003、T004、T005、T007、T016｜**工作量**：1 人天｜**产出**：`cli.py` scan 命令
- **验收**：text 表格（stderr）；json 单行信封 tasks 数组（一次性 task=null）；`--task` 选取清单任务；无任务来源且默认清单空 → exit 2 提示；`--dir`+`--task` 互斥 exit 2

### T018 — clean 命令（text 模式，任务解析）

- **依赖**：T003、T004、T005、T013、T014、T016｜**工作量**：1.5 人天｜**产出**：`cli.py` clean 命令 text 模式
- **验收**：`--yes` 完成清理输出总结；`--dry-run` 零 trash 且 result=dry_run；`--no-backup`/`--no-log` 生效；0 命中 exit 0；无任务参数且有默认清单 → 逐默认任务执行

### T019 — repl 命令（text 模式骨架）

- **依赖**：T016｜**工作量**：0.5 人天｜**产出**：`cli.py` repl 入口 + agent 限制检查
- **验收**：`efc repl` 进入 `efc> `；`--format json`/`--non-interactive` repl → exit 2

### T020 — CLI 基础命令测试（scan/clean/repl text）

- **依赖**：T017、T018、T019｜**工作量**：1 人天｜**产出**：`tests/test_cli.py`（TestScan/TestClean/TestRepl）
- **验收**：text 模式用例全绿；覆盖 Spec §14 test_cli 对应域

### T021 — task 子命令与 patterns 命令

- **依赖**：T004、T005、T016｜**工作量**：2 人天｜**产出**：`cli.py` task 子命令组 + patterns 命令
- **验收**：`task add --name/--dir/--pattern.../--default` 写盘；同名更新去重/`--replace-patterns` 替换；`task list` 显示 default 标记与设置；`task remove` 生效；`patterns` 全部/`--task`/空清单 exit 0/`--json` 信封；坏正则/目录不存在 → exit 2 不写盘

### T022 — 多任务 clean 与 Agent JSON 双输出

- **依赖**：T017、T018、T021、T014｜**工作量**：2.5 人天｜**产出**：`cli.py` clean 多任务扩展 + 全命令 JSON 集成
- **验收**：`--task A --task B` 顺序执行、聚合总结分节；`--all-tasks` 全部；无参数走默认清单（空 → exit 2）；`--dir` 与 `--task`/`--all-tasks` 互斥 exit 2；任一失败→4、任一 abort→3、否则 0；日志仅一条含全部任务；json 信封各命令结构符合 Spec §6.3

### T023 — Agent 交互集成

- **依赖**：T012、T016、T018、T022｜**工作量**：1.5 人天｜**产出**：`cli.py` clean 的 --non-interactive/--stdin/--format json 集成
- **验收**：`--non-interactive` 全程无 input() 立即退出；json 单行 stdout、stderr 无进度；stdin 负载驱动；高危+非交互 → `{"code":3}` + exit 3；TTY+`--stdin` → exit 2

### T024 — CLI 高级功能测试（task/patterns/agent/多任务）

- **依赖**：T021、T022、T023｜**工作量**：1.5 人天｜**产出**：`tests/test_cli.py`（TestTask/TestPatterns/TestAgent/TestMultiTask）
- **验收**：`pytest tests/test_cli.py` 全绿；不依赖真实 send2trash

### T025 — REPL 完整实现（含 task 命令）

- **依赖**：T002、T003、T007、T013、T014｜**工作量**：2 人天｜**产出**：`src/efc/repl.py`
- **验收**：Spec §10 命令表全实现——task 列/加载；dir 高危即时警告；pattern 非法拒绝；list 预览；clean 走同一流水线并写日志输出总结；exit/quit/EOF 退出；未知命令不退出；默认清单恰一个任务时自动加载

### T026 — REPL 测试

- **依赖**：T025｜**工作量**：1 人天｜**产出**：`tests/test_repl.py`
- **验收**：独立执行全绿；覆盖 Spec §14 test_repl 行全部用例

### T027 — 文档与最终验证（三平台冒烟）

- **依赖**：T024、T026｜**工作量**：1.5 人天｜**产出**：`README.md`（三平台安装/快速上手/任务清单/config 说明/安全模型含平台差异/FAQ 含备份恢复与 `efc restore` Roadmap）、`config.example.json`
- **验收**：`pytest -q` 全量通过（≥40 断言场景）；`grep -rn -E "os\.remove|os\.unlink|\.unlink\(|shutil\.rmtree|os\.rmdir" src/` 零结果；`EFC_REAL_TRASH=1 pytest` 本地支持平台通过；三平台（至少各一台）`--help`+`scan`+`clean --dry-run` 冒烟；PRD §8 验收清单逐条通过；`efc --version` 正常

## 3. 依赖关系

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

**关键路径**（约 15 任务串行）：T001 → T002 → T003 → T004 → T005 → T007 → T013 → T014 → T016 → T018 → T022 → T023 → T024 → T027

**总量**：约 37 人天（≈7.5 周单人）。

## 4. 风险与应对

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | 三平台 trash 后端行为差异（mac 无 GUI、linux 无 gvfs → trash_failed；win 网络卷可能物理删除） | 高危：数据丢失或功能不可用 | UNC 入口拒绝（win）；posix 失败按 trash_failed 计不物理删除；T027 三平台真实冒烟 + `EFC_REAL_TRASH` 用例；README 明示限制 |
| R2 | Linux 大小写敏感导致 ignore_case 语义与 win/mac 不一致 | 中：清理结果与预期不符 | 按任务可关闭 ignore_case；测试覆盖大小写分支；README 说明 |
| R3 | CliRunner 下 TTY 检测不可靠 | 中：确认分支测试不稳定 | 确认分支一律由注入 UI 决定（Spec §14 已固化） |
| R4 | Windows 管道 GBK 重编码损坏中文 JSON | 中：Agent 解析失败 | JSON 显式 UTF-8 + ensure_ascii=False；json 用例做管道解析验证 |
| R5 | 备份目录膨胀与敏感数据驻留 | 中：磁盘占用/隐私 | README 提示手工清理与加密卷建议；自动保留策略【待复核】 |
| R6 | 单人开发周期长（37 人天） | 低：进度风险 | 里程碑独立可交付（M3 即可用）；任务粒度小、可随时暂停续接 |
| R7 | 需求再变更 | 低 | 文档三件套（PRD/Spec/Plan）同步机制见 AGENTS.md「文档同步」 |

## 5. 维护约定

- 完成/开始任务时更新本文任务状态与顶部进度行；
- 提交 Footer 关联 `Task: Txxx`（本文编号）；
- 需求/接口变更走 PRD → Spec → Plan 顺序同步，禁止只改代码不改文档。
