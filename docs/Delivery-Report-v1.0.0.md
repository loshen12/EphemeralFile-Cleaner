# 迭代交付验收报告 — EphemeralFile Cleaner v1.0.0

> 验收日期：2026-08-23｜验收范围：Plan T001-T027（全量，27/27 任务、5 个里程碑）
> 基准文档：docs/PRD.md v2.0、docs/Spec.md v2.0（含 §16 交付变更备注）、docs/Plan.md v2.0
> 验收结论：**有条件通过**——功能/技术/质量/制品四域均达标；遗留 5 项【待人工复核】与 8 项技术债务，无阻断 Bug。

## 1. 验收概述（迭代范围）

本轮交付临时文件清理 CLI 全部功能：数据与基础层（models/exceptions/config/任务清单/scanner/safety/backup/ui）、
清理引擎与输出（cleaner 十步流水线 + summary/journal/output）、CLI（scan/clean/repl/task/patterns，
text 与 json 双模式、Agent 无头调用）、REPL 完整命令表、交付文档（README/config.example.json）。
提交序列：`fc4dab8`（T001）→ `9ee8663`（T027），共 28 个提交，每个提交对应 Plan 单任务并附 `Task: Txxx` Footer。

## 2. PRD 需求对齐结果

### 2.1 符合项（已由测试/冒烟证实）

| PRD 条目 | 证据 |
|---|---|
| §4.1 五组命令 scan/clean/repl/task/patterns | test_cli.py（50 用例）、test_repl.py（14 用例）、端到端冒烟 |
| §4.2 任务清单：唯一名/默认清单/同名更新去重与整体替换/来源优先级/互斥/无任务报错 | test_config.py、test_cli.py TestScan/TestTask/TestMultiTask |
| §5.1-1 平台限制（其他平台拒绝） | test_safety.py 参数化 aix/sunos/os2 拒绝、test_cli 平台 exit 2 |
| §5.1-2 回收站唯一删除入口（禁硬删除 API） | 源码 grep `src/efc/` 命中 0；仅 `send2trash` 经构造注入 |
| §5.1-3 UNC 拒绝；posix 挂载点高危 | test_cleaner UNC 用例、test_safety 卷根矩阵（ismount 模拟） |
| §5.1-4/5 卷根一律高危；高危逐字符输入完整路径，`--yes`/非交互不可绕过 | test_cleaner（AutoUI 高危 AbortError 零删除）、test_ui normcase 用例 |
| §5.1-6 删前备份；单文件备份失败跳过并记录 | test_cleaner 备份失败未送 trash、manifest 对账（真实冒烟） |
| §5.1-7 批量 ≤10 越界报错；13 文件 5/批 3 批；批间确认 | test_cleaner、test_cli（`--max-batch 11` exit 2、批间拒绝 exit 3） |
| §5.1-8/9 只删文件不动目录；不跟随符号链接 | scanner `is_file()` 过滤 + `os.walk(followlinks=False)`（test_scanner） |
| §5.1-11 / §5.4 Agent 契约：单行 JSON、退出码一致、非交互无阻塞、高危 3 | test_cli TestAgent + AGENTS.md 双模式冒烟实测 |
| §5.2 确认规则（普通确认可跳过但不绕高危；高危一次不匹配即止） | test_cleaner confirm 拒绝 aborted、test_ui 高危错误路径 False |
| §5.3 退出码 0/1/2/3/4 与多任务聚合 | test_cli（4=任一失败、3=任一中止）、TestMainEnvelope（1=未预期） |
| §5.5 备份时间戳目录+manifest；空间预检整体中止；日志含文件明细；自我排除 | test_backup、test_cleaner（空间不足 AbortError 零删除、排除 bk/log） |
| §5.6 总结按任务→规则两层、只计 trashed、失败末行提示 | test_summary 12 用例 + §9 格式断言 |
| §6 边界场景 17 项 | 逐项有对应用例（0 命中/失败继续/批间拒绝/空间不足/高危非交互/UNC/未知名/坏正则不落盘/无任务/TTY stdin/未知字段/日志写失败仅警告/空清单码 0/repl 未知命令/同名更新/旧键报错） |
| §8 验收清单 | macOS 本地逐条实测通过（含真实回收站清理→备份+manifest 对账→日志）；三平台项见 §7 |

### 2.2 差异项（记录，不擅自修正）

| # | PRD 条目 | 实现现状 | 分类 |
|---|---|---|---|
| D-1 | §5.5「每次清理（含干跑与中止）追加一条执行日志」 | 干跑与 aborted（确认拒绝/批间拒绝）均落日志；但 **AbortError 路径**（高危未确认、备份空间不足）在命令层抛出，无 CleanOutcome 可记，**不落日志** | 轻微差异→技术债务 T-D1 |
| D-2 | §6「无权限子目录：跳过并计数」 | 跳过已实现；**计数未暴露**（Spec §3 ScanResult 无该字段，verbose 通道未实现） | 轻微差异→技术债务 T-D2 |
| D-3 | §5.1-10「非终端环境且未显式跳过确认时，需确认的场景直接中止」 | 未显式判断 isatty，依赖 click 确认在 EOF/无输入时抛 Abort→exit 3（行为等效「不静默执行」）；管道中显式输入 y/n 会被接受（测试即用此路径） | 解释口径【待人工复核】R-2 |

### 2.3 缺失项

无功能缺失。PRD §2 非目标（efc restore、定时清理、空目录删除、备份保留策略）均按约定未做。

## 3. Spec 技术方案对齐结果

### 3.1 符合项

- **§2 分层与依赖方向**：cli/repl → cleaner/summary/journal/output；cleaner → scanner/safety/backup/ui；
  cleaner 不依赖 summary/journal/output；业务层不碰 rich（渲染收敛在 ui/cli 层）。
- **§3 数据模型**：五个 dataclass 字段/类型/默认值与 Spec 逐一对齐（test_models 用 `dataclasses.fields`
  断言名称+类型）；异常族 exit_code 体系（AbortError=3）一致。
- **§4 配置系统**：查找链、v1.0 旧键拒绝、max_batch 1..10、任务清单增删查语义、env/stdin 解析与
  优先级链（test_config 36 用例 + test_input 27 用例）。
- **§5 核心接口**：全部公共函数/类按签名落地（含 compile_patterns/scan/assess_risk/BackupRun/UI 协议/
  AutoUI/ConsoleUI/ExecutionLog/emit_* /exit_code_for）。
- **§6 CLI 契约**：用法、输出路由（json stdout 单行信封、人读走 stderr）、§6.3 信封 data 结构
  （clean/scan/task/patterns 字段逐一核对）、退出码。
- **§7 流水线十步**、**§8 高危规则 a-d（含 home 子树豁免，已在 Spec §8 c 同步注明）**、
  **§9 总结格式**、**§10 REPL 命令表**、**§11 异常约定**、**§12 安全红线落点**、**§13 平台要点**。
- **§14 测试策略**：Spec 表列 11 个测试域全部落地且独立执行全绿；另超额提供 test_exceptions/
  test_models/test_ui 三个文件（合计 14 文件、259 用例）。

### 3.2 差异项及变更说明（已同步至 Spec §16 变更备注）

| # | Spec 条目 | 实现差异 | 理由与影响 |
|---|---|---|---|
| S-1 | §5 Cleaner 签名 | 新增 `dry_run`/`task_name` 关键字参数 | 传输级标志与会话任务名注入；`run()` 签名不变，调用兼容 |
| S-2 | §3 dry_run 语义 | results=[]，不新增 "dry_run" 状态 | 保持状态词表封闭；总量经 total_matched 传递 |
| S-3 | §5 ConsoleUI 开关 | 新增 `input_fn`/`console` 注入点 | 测试不 mock input；json/非交互 stderr 路由 |
| S-4 | §6.4 main() 错误出口 | 命令层 `_translate` + main() 兜底两层 | CliRunner 与真实入口退出码一致；信封行为不变 |
| S-5 | §6.1/6.4（附加） | json clean 无确认策略 → exit 2 | 防信封污染与隐式放行 |
| S-6 | §4.4（附加） | stdin 负载 command 与子命令不一致 → ConfigError | 防误路由 |
| S-7 | §4.2/4.4（附加） | 未知顶层配置键报错；EFC_* 空串视为未设置 | 严格输入取向 |
| S-8 | §10 REPL | 日志 command 记 "repl"；shlex 转义要求正则加引号 | 来源可审计；UX 已在 README/测试注明 |
| S-9 | §1 依赖 | 使用 `typer._click`（0.27+ 内置 click） | 环境约束；**下界不匹配见 R-1** |
| S-10 | §6.4 AgentState | 内部新增 `format_explicit` 字段 | 命令内格式裁定不依赖 sys.argv |
| S-11 | §14 grep 范围 | `src/` 全域 grep 会命中 egg-info/PKG-INFO 文本 1 次 | 构建产物（gitignore、可再生）；源码级验收限定 `src/efc/` 命中 0 |

## 4. 自测结果与缺陷清单

### 4.1 核心链路与覆盖

核心链路 6 条全部有自动化覆盖：①配置加载/合并/任务清单 → ②扫描（递归/排除/排序）→ ③安全门
（平台/UNC/高危矩阵/批量）→ ④流水线（确认/备份/分批/失败聚合）→ ⑤输出（总结/日志/信封/退出码）→
⑥CLI/REPL/Agent 入口。全量 `pytest -q`：**259 passed, 1 skipped**（skip 为 EFC_REAL_TRASH 真实回收站
用例，`EFC_REAL_TRASH=1` 本地实测通过）；mypy strict 0 错误；ruff 0 告警。

### 4.2 阻断 Bug

**无**。（本轮验收未发现阻断级缺陷。）

### 4.3 技术债务（可延后）

| # | 事项 | 建议 |
|---|---|---|
| T-D1 | AbortError（高危/空间不足）中止路径不落执行日志 | 命令层捕获 AbortError 后补记 result=aborted 的 JournalRecord |
| T-D2 | 无权限子目录跳过但计数未暴露；verbose 通道未实现 | 扩展 ScanResult 或 verbose 日志（需先改 Spec §3） |
| T-D3 | `--verbose/-v` 选项已注册但无实际行为 | 接入 traceback/跳过目录明细输出 |
| T-D4 | 备份目录无保留策略（PRD【待复核】项） | Roadmap：按时间戳目录的保留条数/天数清理（注意删除红线，仅限备份目录内） |
| T-D5 | `efc restore` 未实现（PRD 非目标，Roadmap） | 按 manifest 反向拷贝恢复 |
| T-D6 | 仓库无 CI 配置（.github/workflows 缺失） | 增加 lint+mypy+pytest 的 GitHub Actions（Win/Linux 矩阵可顺带覆盖 R-4） |
| T-D7 | `_translate` 报告格式依赖 `_resolve_format()` 扫描真实 argv，CliRunner 下错误信封走 text | 已由 run_main 双路径测试策略覆盖；如需统一可改为经 ctx 传递 |
| T-D8 | EFC_CONFIG 对 task/patterns 命令经 `_load_for_write` 生效，但这些命令不读 --stdin 负载 | 如需任务管理全 Agent 化再扩展（Spec 未要求） |

### 4.4 潜在运行风险扫描

空值：stdin 负载 null 键跳过（测试覆盖）；Task.dir 为 None 在 resolve/执行前均有守卫。
事务边界：save_config 原子写（临时文件+os.replace，失败时临时文件可能残留——为遵守「禁删 API」红线的
有意取舍，错误消息中注明路径）。资源泄漏：文件句柄均 with 上下文。SQL：无数据库。硬编码/密钥：无密钥、
无外呼；平台保护根为内置常量表（属设计）。

## 5. 构建、迁移、部署注意事项

- **构建**：`uv build` 产出 sdist+wheel 成功；wheel 含 14 个模块、entry point `efc = efc.cli:main` 正确；
  `pip install -e ".[dev]"` 可用（macOS+uv 实测）。
- **依赖**：运行依赖 `typer[all]>=0.12`、`send2trash>=1.8`；dev 含 pytest/mypy/ruff/types-Send2Trash。
  **注意 R-1：实现依赖 typer 0.27+ 的内置 click，声明下界 0.12 与实际不符。**
- **数据迁移**：无数据库、无迁移脚本。配置文件向后不兼容点：v1.0 顶层 `target_dir/filename_patterns/
  recursive` 旧键出现即报错（设计如此，报错信息引导改用 tasks[]）。
- **运维**：无启动参数/服务；环境变量清单见 README（EFC_CONFIG/EFC_FORMAT/EFC_NON_INTERACTIVE/
  EFC_TASK/EFC_DIR/EFC_PATTERNS/EFC_RECURSIVE/EFC_DRY_RUN/EFC_YES/EFC_MAX_BATCH/EFC_BACKUP_DIR/
  EFC_LOG_FILE）。运行产物：`<backup_dir>/<时间戳>/`、`.efc.log`（JSONL）——部署磁盘容量预估需计入。
- **CI/CD/回滚**：仓库无 CI（见 T-D6）；回滚即安装旧版本包，用户数据（备份/日志/任务清单）不受影响。

## 6. 文档变更汇总

| 文档 | 变更 |
|---|---|
| Spec.md | 新增 §16「变更备注（v1.0.0 交付）」记录 11 项实现差异（即本报告 §3.2） |
| Plan.md | 任务状态 27/27 ✅、里程碑 M0-M5 全标完成、顶部进度 100% 与遗留说明（T027 已含） |
| README.md | T027 全量重写（安装/快速上手/命令与退出码/Agent/config/安全模型/FAQ） |
| config.example.json | 新增，与 Spec §4.1 一致 |
| 本报告 | docs/Delivery-Report-v1.0.0.md（新增） |

涉及文件全景：`src/efc/` 14 模块 + `tests/` 14 文件 + `pyproject.toml`（dev 依赖、ruff bugbear 配置）。
新增逻辑入口：cli.py 的 `_gather/_resolve_targets/_translate/_command_fmt/_clean_payload`、
repl.py 会话命令、config.py 输入解析；无废弃公共接口。

## 7. 遗留风险与后续建议

**【待人工复核】清单**

| # | 事项 | 建议 |
|---|---|---|
| R-1 | pyproject 声明 `typer[all]>=0.12`，实现使用 `typer._click`（仅 0.27+ 存在） | 提升 pyproject 下界至 `typer[all]>=0.27`，或代码改为 `try: from typer._click... except ImportError: from click...` 双导入 |
| R-2 | PRD §5.1-10「非终端环境需确认场景直接中止」的解释口径 | 当前经 click EOF→Abort→exit 3 等效实现，管道显式 y/n 被接受；请业务方确认是否要求显式 isatty 判断 |
| R-3 | AbortError 中止路径不落执行日志（PRD §5.5 字面含「中止」） | 确认口径后实施 T-D1 |
| R-4 | Windows / Linux 真机冒烟未做（本机仅 macOS，含真实回收站已测） | 各找一台机器跑 `--help`+`scan`+`clean --dry-run`+`EFC_REAL_TRASH=1`；重点验证 win 盘符根/normcase、linux trash 后端 |
| R-5 | 无权限子目录「计数」的暴露方式（PRD §6） | 决定是否扩展 ScanResult（需先改 Spec §3） |

**后续迭代优先级建议**：P0＝R-1（依赖下界，影响安装可复现性）；P1＝R-4 三平台冒烟、T-D6 CI；
P2＝T-D1/T-D3（日志与 verbose 补齐）；P3＝T-D5 efc restore、T-D4 备份保留策略、T-D2。

## 8. 最终验收核对清单

- [x] Plan 27/27 任务完成并逐任务提交（Footer `Task: Txxx`）
- [x] `pytest -q` 全量通过（259 passed / 1 skipped[真实回收站用例，EFC_REAL_TRASH=1 时通过]）
- [x] mypy strict 零错误；ruff 零告警
- [x] 源码禁删 API grep（`src/efc/`）零结果；唯一删除入口 send2trash（构造注入）
- [x] 高危二次确认不可被 `--yes`/非交互绕过（AutoUI 恒拒 → exit 3 零删除）
- [x] 批量上限越界拒绝；13 文件 5/批 → 3 批
- [x] 退出码 0/1/2/3/4 契约与多任务聚合
- [x] Agent：stdout 单行 JSON 信封、非交互无阻塞、高危 {"code":3}
- [x] REPL 与 CLI 同一 Cleaner 流水线，行为一致
- [x] `uv build` sdist+wheel 构建成功、entry point 正确
- [x] `efc --version` 正常（1.0.0）
- [x] README / config.example.json / Spec §16 变更备注 / 本报告 齐备
- [ ] Windows 真机冒烟（R-4，待异地执行）
- [ ] Linux 真机冒烟（R-4，待异地执行）
- [ ] typer 依赖下界复核（R-1，待人工决策）
- [ ] PRD §5.1-10 / §5.5「中止」口径确认（R-2/R-3，待人工决策）
