# Agent 指南

面向 LLM 编码的跨平台（Windows / macOS / Linux）临时文件清理 CLI 工具（EphemeralFile Cleaner）。入口 `efc = efc.cli:main`，包名 ephemeral-file-cleaner。

## 快速命令

```powershell
# Windows（PowerShell）
python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m compileall -q src\efc          # 编译检查
.\.venv\Scripts\python -m pytest -q                      # 单元测试

# 冒烟验证（在临时目录操作，避免误删真实文件）
mkdir tmp_smoke
.\.venv\Scripts\efc task add --name smoke --dir tmp_smoke --pattern "\.tmp$" --default   # 添加任务
.\.venv\Scripts\efc patterns                                          # 列出规则
.\.venv\Scripts\efc scan --dir tmp_smoke --pattern "\.tmp$"           # 人类预览（一次性任务）
.\.venv\Scripts\efc --format json scan --dir tmp_smoke --pattern "\.tmp$"   # JSON 信封
echo '{"dir":"tmp_smoke","patterns":["\\.tmp$"],"dry_run":true}' | .\.venv\Scripts\efc --format json --non-interactive --stdin clean
```

```bash
# macOS / Linux（bash/zsh）。注：本机 macOS 系统 python 的 venv 受损，用 uv 替代
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m compileall -q src/efc
.venv/bin/python -m pytest -q
mkdir -p tmp_smoke
.venv/bin/efc task add --name smoke --dir tmp_smoke --pattern '\.tmp$' --default
.venv/bin/efc patterns
.venv/bin/efc scan --dir tmp_smoke --pattern '\.tmp$'
.venv/bin/efc --format json scan --dir tmp_smoke --pattern '\.tmp$'
echo '{"dir":"tmp_smoke","patterns":["\\.tmp$"],"dry_run":true}' | .venv/bin/efc --format json --non-interactive --stdin clean
```

## 架构边界

```
cli.py / repl.py          入口层：typer 命令、REPL 交互会话，只做参数解析、结果分发与 JSON 信封输出
cleaner.py                业务层：清理流水线（安全门 → 扫描 → 确认 → 备份 → 分批入回收站），单任务执行
scanner.py                扫描层：正则编译 + 目录遍历 + 模式匹配
safety.py                 安全层：平台守卫（win32/darwin/linux）、Windows UNC 拒绝、卷根/保护根/home 高危判定、批量校验
backup.py                 备份层：shutil.copy2 保留结构 + manifest.json 审计记录
ui.py                     UI 协议：ConsoleUI（rich/typer 交互）/ AutoUI（自动确认，高危拒）
summary.py / journal.py / output.py
                          输出层：清理总结（按任务/模式聚合）、执行日志（JSONL 追加）、Agent JSON 信封
config.py                 配置层：AppConfig/Task + 长期任务清单（tasks）+ 加载/合并/校验/保存 + 环境变量/--stdin 输入解析
models.py / exceptions.py 数据层：FileMatch/ScanResult/CleanOutcome/RiskDecision + EfcError 体系
```

- 分层约束：入口层不写业务逻辑；业务层不碰 rich 渲染；JSON 模式必须 `output.emit_success(data)` / `output.emit_error(code, msg)` 输出到 stdout，**禁止** rich `console.print` 输出 JSON（非 tty 下 rich 会按 80 列折行破坏 JSON）
- 新增模块：在 `src/efc/` 下新建 `.py`，在 `__init__.py` 或对应入口层 import；测试文件同步在 `tests/` 下新建 `test_<module>.py`
- 唯一删除入口：`send2trash.send2trash()`，通过 `Cleaner` 构造函数注入（`trash` 参数），`src/` 全项目禁止 `os.remove/os.unlink/pathlib.Path.unlink/shutil.rmtree/os.rmdir`
- `cleaner.py` 不依赖 `summary.py`/`journal.py`/`output.py`——后者由 `cli.py`/`repl.py` 消费 `CleanOutcome` 后调用

## 配置分层

- 查找链：`--config` > `EFC_CONFIG` 环境变量 > `./config.json` > `~/.efc/config.json` > 内置默认；相对路径按 CWD 解析
- 输入优先级：**CLI 显式参数 > `--stdin` JSON > 环境变量（EFC_\*） > config.json > 内置默认值**；列表键整体替换，不做合并
- 传输级标志（`--format`/`--non-interactive`/`--stdin`）只来自 CLI 与环境变量，不来自 stdin 负载
- 任务清单 `tasks[]` 是任务唯一持久化形式（v1.1 取消顶层默认目标字段，旧键 `target_dir`/`filename_patterns`/`recursive` 出现即 ConfigError）；任务名全清单唯一；`--default` 标记默认执行；每次执行必须明确任务：`--task`（可重复）> `--all-tasks` > `--dir`（一次性）> 默认任务清单，`--dir` 与 `--task`/`--all-tasks` 互斥（同用 exit 2）
- 修改配置字段需同步：`config.py` 的 `AppConfig` dataclass 及 `to_dict`/`from_dict`（如存在）、`config.example.json`、`Spec.md` 的「配置系统」、`README.md`

## 工作流

- 代码任务必须先理解项目结构、依赖和现有风格，再做最小必要修改
- 非平凡修改按 Context → Plan → Apply → Verify 执行
- 修改前检查相关文件。公共接口变更必须同步测试（`tests/` 下对应文件）和 Spec.md
- 只做当前任务必要改动，禁止无关重构
- 安全红线（PRD.md「安全规则」/ Spec.md「安全红线的技术落点」）不可妥协：任何修改不得绕过 send2trash、高危确认、批量上限、平台守卫

## 命名规范

- Python：snake_case 函数/变量，PascalCase 类型；CLI 命令小写，`task add` 用空格分隔的子命令
- 配置文件：小写 + 连字符（`config.example.json`）；备份目录 `<YYYYmmdd-HHMMSS.fff>/`；日志 `.efc.log`
- 测试文件：`test_<module>.py`；fixtures 在 `tests/conftest.py`（`tree`、`fake_trash`、`fake_ui`）；CLI 测试用 `typer.testing.CliRunner`
- 异常类：`EfcError` 为基类，`exit_code` 为类属性（子类覆盖）；工厂函数 `new_run(base_dir)` 而非 `BackupRun.__init__` 直接暴露
- 内部文档/日志用中文

## 验证要求

- 常规改动：`compileall` + `pytest -q`（全部用例）+ 人类模式冒烟 + Agent 模式冒烟
- 单元测试：`tests/` 按模块划分，所有测试用 `fake_trash`（记录调用路径）+ `FakeUI`（可编程 confirm 序列）注入，不 monkeypatch 全局、不 mock input；REPL 测试直接调 `ReplSession.handle(line)`
- CLI 测试：用 `typer.testing.CliRunner` 且必须 `monkeypatch.chdir(tmp_path)` 隔离根目录 config.json；json 模式用 `python -c "import json,sys; json.load(sys.stdin)"` 验证 stdout 可解析；text 模式用 `result.stdout` 或 `result.stderr` 断言关键词
- 新增 CLI 命令：在 `cli.py` 注册；REPL 需同步在 `repl.py` 的命令表添加条目
- 修改 CLI 行为时必须同步更新 `Spec.md`、`README.md`、`AGENTS.md`
- 真实回收站测试：`EFC_REAL_TRASH=1 pytest` 仅在本地任一支持平台（Windows/macOS/Linux）手动执行，不上 CI

## 平台环境陷阱（Windows / macOS / Linux）

- 路径比较前必须 `Path.resolve()` + `os.path.normcase()`（Windows 大小写不敏感且 `/`、`\` 混用；posix 下 normcase 恒等，代码无需分支）
- 平台守卫：`sys.platform ∈ {win32, darwin, linux}` 放行，其余 `PlatformError`（exit 2）；Windows UNC 路径（`\\server\share`）入口拒绝；posix 无 UNC 概念，网络卷靠挂载点高危 + trash 失败兜底
- **Windows**：`send2trash` 在网络卷或回收站被组策略禁用的文件系统上可能直接物理删除或失败；管道传 stdout 给 python 时 GBK 重编码会损坏中文（JSON 用 `ensure_ascii=False` + UTF-8）；`$` 在 PowerShell 中是变量前缀，`--pattern "^~\$"` 需用单引号包裹，`\\` 在 JSON 中需双重转义
- **macOS**：`send2trash` 走 Finder——纯 SSH/无 GUI 会话不可用 → `trash_failed`（exit 4，不会物理删除）；SIP 目录（/System 等）无权限，内置保护根已覆盖
- **Linux**：`send2trash` 走 GIO（freedesktop Trash）——容器/最小系统无 gvfs 时不可用 → `trash_failed`；文件系统大小写敏感（`ignore_case` 语义与 Windows 不同）；tmpfs/NFS 挂载点按卷根高危
- `typer.Option(None, "--recursive/--no-recursive")` 用 `Optional[bool]` 三态（None=未指定，回落任务 recursive）；`CliRunner` 下 TTY 检测不可靠——确认分支一律由注入的 UI 决定
- `os.walk` 默认 `followlinks=False`，junction/符号链接不被跟随；目标目录无权限遍历的子目录跳过并计数

---

### 文档同步

修改命令、参数、输出、配置或打包方式时，同步更新 `README.md`（概述/安装/快速上手/安全模型/FAQ）、`PRD.md`（业务规则/边界场景）、`Spec.md`（模块/接口/契约）、`Plan.md`（任务验收）与 `AGENTS.md`，并保持三者一致。文档至少说明命令用途、参数、默认行为、退出码（错误时非零 0/1/2/3/4）、是否写库（全部不写库，仅读写文件）。

---

## Git 协作规范

遵循 Google Git 提交与代码评审（CL）最佳实践。是否提交、何时推送，一律等用户明确指令，Agent 不主动执行。

### Commit Message 规则

- 格式：`type(scope): subject`——`type` 取 `feat`/`fix`/`refactor`/`docs`/`test`/`chore`/`build`/`style`/`perf`；`scope` 填受影响模块/组件（如 `config`、`scanner`、`cleaner`、`cli`、`repl`、`agent`、`docs`）
- 标题：≤ 50 字符，祈使句（imperative），无句号结尾；例如 `feat(api): 新增查询接口限流控制`、`fix(cleaner): 修复非交互高危绕过`
- 正文（body）：标题后空一行，说明变更**原因（why）**，而非只罗列做了什么（what）
- Footer：正文后空一行，关联 `Task: Txxx`（Plan.md 任务编号）或 `Fix: #xxx`（issue 编号）
- 禁止提交信息中出现模板占位、无意义表述；一个逻辑变更一个提交，未完成/未验证的变更不提交

示例：

```
feat(scanner): 记录首个命中模式

按模式汇总清理总结时每个文件需要唯一归属，避免同一文件在
多模式统计中被重复计数。

Task: T007
```

### Git 操作约束

- 用 `git add <指定路径>` 精确暂存目标文件，**禁用** `git add -a` / `git add -A`，避免无关文件混入提交
- `main` 分支**禁止** force-push（`--force`）；仅 feature 分支可使用 `--force-with-lease`，且需确认远端无人共享该分支
- 工作区中的本地临时提交（`wip` 类）使用后必须清理（rebase / reset），不得把 wip 提交推到远端
- Agent 只做修改与本地验证；**是否 commit、push、init、merge 一律等用户指令**，不主动执行

### PR / CL（代码评审）

- 变更尽量小：一个 CL 聚焦一个目标（对应 Plan.md 单个 Task）；大任务拆分为多个小 CL 顺序提交
- CL 描述写清：**目的**（解决什么）、**改动**（文件/接口层面）、**风险**（破坏面与回归点）、**关联 Task**（Plan.md T 编号）
- 代码行为改动必须同步更新：对应 `tests/` 测试、`README.md`、`Spec.md`、`AGENTS.md`
- 评审重点关注：是否绕过安全红线（send2trash 唯一删除入口、高危二次确认、批量 ≤10、平台守卫）、JSON 信封契约与 stdout/stderr 分离、退出码 0/1/2/3/4 约定