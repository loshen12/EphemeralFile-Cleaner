# EphemeralFile Cleaner

**跨平台（Windows / macOS / Linux）**的临时文件清理 CLI 工具：以**任务**为单位组织清理——每个任务 = 目标目录 + 一组文件名正则。任务可持久化为**长期任务清单**（`efc task add`，可反复执行），其中标记 `default: true` 的任务构成**默认任务清单**（无参数执行时自动运行）；也可每次一次性明确（`--dir` + `--pattern`，不落盘）。执行时递归（可选）扫描，把文件名匹配任一正则的文件移入**系统回收站**（仅允许 `send2trash`，硬删除 API 全项目禁用）。删除前自动备份并写入 manifest，高危目录需二次确认，单次执行按小批量（≤10 个/批）分批推进。

> 文档体系：[USAGE.md](USAGE.md)（使用文档）｜[docs/PRD.md](docs/PRD.md)（业务需求）→ [docs/Spec.md](docs/Spec.md)（技术方案）→ [docs/Plan.md](docs/Plan.md)（开发计划）。

## 安装

要求 Python **>= 3.10**，支持 Windows 10+ / macOS 12+ / Linux 桌面发行版（需 freedesktop Trash，如 gvfs）。

```powershell
# Windows（PowerShell）
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

```bash
# macOS / Linux
python -m venv .venv
.venv/bin/python -m pip install -e .
# 本机 macOS 系统 python 的 venv 受损时可用 uv：
# uv venv .venv && uv pip install --python .venv/bin/python -e .
```

不支持的平台上任何命令直接拒绝（exit 2，提示当前平台名）。

## 快速上手

```bash
mkdir tmp_smoke && touch tmp_smoke/a.tmp

# 1. 建任务（写盘，可反复执行；--default 加入默认清单）
efc task add --name smoke --dir tmp_smoke --pattern '\.tmp$' --default

# 2. 预览（只读，不删除）
efc patterns                        # 查看任务规则
efc scan                            # 执行默认清单；也可 --task smoke / --dir + --pattern
efc scan --dir tmp_smoke --pattern '\.tmp$' --recursive

# 3. 清理（移入回收站，删前自动备份 + 写执行日志）
efc clean --yes                     # --yes 跳过普通确认；高危仍需输入完整路径
efc clean --dry-run                 # 预演：不删除不备份

# 4. 交互会话
efc repl                            # efc> 提示符，输入 help 查看命令表

# 5. 任务管理
efc task list [--json]              # 清单 + 全部持久化配置
efc task remove --name smoke
```

> Windows PowerShell 注意：`$` 是变量前缀，含 `$` 的正则（如 `^~\$`）请用**单引号**包裹；
> JSON 里的 `\\` 需双重转义。

## 命令与参数

```
efc [--format text|json] [--non-interactive] [--stdin] <command> ...
efc --version
efc scan  [--task NAME]... [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive] [--config PATH] [--json]
efc clean [--task NAME]... [--all-tasks] [--dir PATH] [--pattern REGEX]...
          [--recursive/--no-recursive] [--config PATH] [--yes] [--no-backup]
          [--max-batch N] [--dry-run] [--no-log]
efc repl  [--config PATH]
efc task add    --name NAME [--dir PATH] [--pattern REGEX]... [--recursive/--no-recursive]
                [--default/--no-default] [--replace-patterns] [--config PATH]
efc task list   [--config PATH] [--json]
efc task remove [--name NAME | --dir PATH] [--config PATH]
efc patterns [--task NAME] [--config PATH] [--json]
```

- 全局选项在子命令前；`--json` 是部分命令的 `--format json` 简写，同时出现以 `--format` 为准；
- 任务解析优先级：`--task`（可重复，按出现顺序）> `--all-tasks` > `--dir`（一次性，须配 `--pattern`）> 默认清单；`--dir` 与 `--task`/`--all-tasks` 互斥（exit 2）；
- `--pattern` 对每任务**整体替换**规则；`--recursive` 三态覆盖（未指定则用任务自身设置）；
- `--yes` 只跳过普通确认，**不能**绕过高危二次确认；`--no-backup` 是显式弃权备份（风险自负）；`--no-log` 本次不写日志。

### 退出码（硬契约）

| 码 | 含义 |
|---|---|
| 0 | 成功（含 0 命中，无事可做） |
| 1 | 未预期内部错误 |
| 2 | 配置/用法/输入错误（不支持平台、UNC、坏正则、任务/目录不存在、互斥参数等） |
| 3 | 用户中止 / 安全拦截（拒绝确认、高危未通过、非交互遇高危） |
| 4 | 执行期部分失败（存在备份或回收站失败的文件） |

多任务执行：任一任务有失败文件 → 4；否则任一中止 → 3；否则 0。**全部命令只读写本地文件，不访问网络、不写注册表/系统库。**

## Agent（无头）模式

```bash
# 单行 JSON 信封：成功 {"data":...} / 失败 {"code":N,"msg":...}，code 与退出码一致
efc --format json scan --dir tmp_smoke --pattern '\.tmp$'

# 参数经管道传入（--stdin 读 JSON 负载；优先级 CLI > stdin > 环境变量 EFC_* > config.json）
echo '{"dir":"tmp_smoke","patterns":["\\.tmp$"],"dry_run":true}' \
  | efc --format json --non-interactive --stdin clean

# 高危 + 非交互：恒拒 → {"code":3} + exit 3，零删除
```

- `--non-interactive` 全程无 `input()`：普通确认自动通过、高危仍拒（code 3）；
- json 模式 stdout **只有一行信封**，人读输出（表格/总结）一律走 stderr；
- 环境变量：`EFC_CONFIG / EFC_FORMAT / EFC_NON_INTERACTIVE / EFC_TASK / EFC_DIR / EFC_PATTERNS / EFC_RECURSIVE / EFC_DRY_RUN / EFC_YES / EFC_MAX_BATCH / EFC_BACKUP_DIR / EFC_LOG_FILE`（列表键换行或 `;` 分隔）。

## 任务清单与 config.json

配置查找链：`--config` > `EFC_CONFIG` > `./config.json` > `~/.efc/config.json` > 内置默认（缺失不算错误）。`tasks[]` 是任务唯一持久化形式；v1.0 顶层 `target_dir`/`filename_patterns`/`recursive` 旧键出现即报错（明确失败优于静默忽略）。完整字段见 [config.example.json](config.example.json)：

```json
{
  "tasks": [
    {"name": "downloads", "dir": "D:\\Downloads",
     "patterns": ["^~\\$", "\\.tmp$"], "recursive": true, "default": true}
  ],
  "max_batch": 5, "backup_enabled": true, "backup_dir": "~/.efc/backup",
  "ignore_case": true, "high_risk_dirs": [], "log_enabled": true, "log_file": ".efc.log"
}
```

- 任务名全清单唯一；`task add` 时目录必须存在、正则必须可编译，校验失败不写盘；
- 同名 `task add` 即更新（仅覆盖显式字段）：规则默认追加去重，`--replace-patterns` 整体替换；
- `max_batch` 限定 1..10（越界即错，不钳制）；`ignore_case` 默认 true（Linux 文件系统大小写敏感，可按任务需要关闭）。

## 安全模型

- **唯一删除入口**：全项目只允许 `send2trash`（移入系统回收站），`os.remove` / `shutil.rmtree` 等硬删除 API 被 lint 与评审共同禁止；
- **删前备份**：每次执行在 `backup_dir` 下建 `<YYYYmmdd-HHMMSS.fff>/` 时间戳目录，`shutil.copy2` 保留相对结构与修改时间，并写 `manifest.json`（原路径/备份路径/状态/大小/错误）供对账；
- **高危二次确认**：卷根/盘符根、系统保护目录（Windows：`%SystemRoot%`、Program Files 等；macOS：`/System`、`/usr` 等；Linux：`/`、`/usr`、`/home` 等）及其内部、递归覆盖保护目录、home 根，均判高危——必须**逐字符输入 normcase 归一后的完整路径**，一次不匹配即中止；用户配置的 `high_risk_dirs` 同样参与判定；home 子目录是常规清理对象，不自动高危；
- **批量上限**：单批 1..10，批间确认可随时停止（已删不回滚）；
- **不跟随符号链接**；扫描自动排除备份目录与日志文件自身；执行日志（JSONL，含每个文件）落 `.efc.log`。

### 平台差异（务必阅读）

| 平台 | 回收站后端 | 已知限制 |
|---|---|---|
| Windows | SHFileOperation | 网络卷/回收站被组策略禁用的卷上可能直接物理删除或失败 → **UNC 路径（`\\server\share`）入口即拒**；PowerShell 中 `$` 需单引号 |
| macOS | Finder | 纯 SSH / 无 GUI 会话不可用 → 该文件计 `trash_failed`（exit 4，**不会物理删除**） |
| Linux | GIO / freedesktop Trash | 容器/最小系统无 gvfs 时不可用 → 同上计 `trash_failed`；文件系统大小写敏感，`ignore_case` 语义与 Win/mac 不同 |

## FAQ

**删除的文件还能找回来吗？**
能，两条路：(1) 系统回收站里直接还原；(2) 备份目录按时间戳存全套副本，`manifest.json` 记录每文件的原路径与备份路径，手工 `copy` 回去即可（`efc restore` 命令在 Roadmap 中）。

**备份目录越来越大怎么办？**
备份不会自动清理（保留是安全默认）。确认不需要后可手工删除旧的时间戳子目录；建议放在加密卷上（备份含被删文件副本）。

**为什么我配的任务没有被执行？**
`--pattern` 是整体替换不是追加；`--task` 未知名会 exit 2；无参数时只执行 `default: true` 的任务，默认清单为空会提示 `efc task add --default`。

**Agent 调用时 stdout 出现了表格？**
那是 stderr 的人读输出被混在一起了——请只读 stdout（单行 JSON），stderr 可直接丢弃或转日志。

## 开发

```bash
.venv/bin/python -m pytest -q          # 全量测试（fake 注入，不触真实回收站）
.venv/bin/python -m mypy src/          # strict
.venv/bin/python -m ruff check src/ tests/
EFC_REAL_TRASH=1 .venv/bin/python -m pytest tests/test_cleaner.py   # 真实回收站（本地手动）
```

贡献规范见 [AGENTS.md](AGENTS.md)。
