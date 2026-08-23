# USAGE.md — EphemeralFile Cleaner

> 面向终端用户、自动化脚本与 Agent 调用方的使用文档。只讲「怎么用」，技术实现见 [docs/Spec.md](docs/Spec.md)，
> 业务需求见 [docs/PRD.md](docs/PRD.md)。

## 简介

EphemeralFile Cleaner（命令名 `efc`）是一个跨平台（Windows / macOS / Linux）的临时文件清理命令行工具：

- 以**任务**为单位组织清理：一个任务 = 目标目录 + 一组文件名匹配规则（正则）；
- 命中的文件在删除前**自动备份**（含对账清单），然后移入**系统回收站**——绝不物理删除；
- **高危目录**（系统目录、卷根、用户主目录等）需逐字符输入完整路径二次确认，任何自动方式无法绕过；
- 按**小批量**（每批 ≤ 10 个文件）分批推进，批间可随时停止；
- 每次执行追加**执行日志**（含每个文件的去向），全程可审计；
- 同时服务两类使用方式：**人类交互**（确认、预览、总结、REPL 会话）与**无头自动化**
  （单行 JSON 结果 + 严格退出码 + 管道传参，全程无阻塞）。

## 安装

要求 Python **>= 3.10**。支持 Windows 10+ / macOS 12+ / Linux 桌面发行版（Linux 需 freedesktop Trash
组件，如 gvfs）。不支持的平台上任何命令直接拒绝并提示。

```powershell
# Windows（PowerShell）
git clone <仓库地址> && cd EphemeralFile-Cleaner
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\efc --version
```

```bash
# macOS / Linux
git clone <仓库地址> && cd EphemeralFile-Cleaner
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/efc --version
# macOS 系统 python 的 venv 受损时可用 uv：
# uv venv .venv && uv pip install --python .venv/bin/python -e .
```

安装成功后执行 `efc --help` 可查看命令总览。

> 【待补充】PyPI / 安装包发布渠道：PRD 未定义，当前仅支持从源码仓库安装。

## 核心概念（任务、匹配规则、备份、回收站、审计日志）

### 任务（Task）

一个任务由五个属性构成：

| 属性 | 说明 |
|---|---|
| 名称 | 任务清单内唯一 |
| 目标目录 | 清理的根目录（保存任务时必须已存在） |
| 匹配规则组 | 一组文件名正则，**任一命中即匹配** |
| 递归开关 | 是否进入子目录扫描 |
| 默认执行标记 | 标记的任务构成**默认任务清单**，`scan`/`clean` 不带任务参数时自动执行 |

任务的两种使用形态：

- **长期任务清单**：`efc task add` 保存到配置文件，可反复执行；同名重复保存 = 更新该任务
  （规则默认追加去重，可选整体替换）；
- **一次性任务**：命令行直接给 `--dir` + `--pattern`，不落盘，当次生效。

每次执行必须明确本次任务，来源与优先级：

**指定任务名（可多个）> 全部任务（--all-tasks）> 一次性指定目录+规则 > 默认任务清单**

一次性指定（`--dir`）与指定任务名/全部任务**互斥**，同用报错。完全没有可用任务时明确报错并提示创建方式。

### 匹配规则（正则）

- 匹配对象是**文件名**（不是完整路径），做**子串正则匹配**——例如 `\.tmp$` 匹配所有以 `.tmp`
  结尾的文件名；
- 一个任务的多个规则之间是 **OR** 关系，命中任意一条即清理；
- 只匹配**文件**，目录永不参与匹配或删除；空目录保持原样；
- 不跟随符号链接 / junction；
- 大小写：默认忽略大小写（`ignore_case: true`）。注意 Linux 文件系统本身大小写敏感，如需精确
  匹配可在配置中关闭该开关；
- 非法正则在保存任务与执行前都会被立即拒绝（不会静默生效）。

### 备份

- 删除前把**全部命中文件**按原目录结构复制到备份目录：每次执行独立一个时间戳子目录
  `<备份目录>/<年月日-时分秒.毫秒>/`；
- 每个子目录内有 `manifest.json` 对账清单：记录每个文件的原路径、备份路径、状态、大小、错误信息，
  以及本次执行的目标目录、规则组、是否递归、执行时间；
- 删除开始前做**空间预检**：备份位置可用空间不足时整体中止（绝不先删后失败）；
- 备份默认开启；`--no-backup` 可显式关闭（放弃恢复手段，风险自负）。

### 回收站

- 删除的唯一去向是**系统回收站**：Windows 回收站 / macOS 废纸篓（Finder）/ Linux freedesktop Trash；
- 移入回收站失败（如 macOS 无图形会话、Linux 缺 trash 组件）时该文件计为失败并记录，
  **不会物理删除**；存在失败文件时本次执行退出码为 4；
- Windows 网络卷上的回收站行为不可靠，因此 **UNC 网络路径（`\\server\share`）入口直接拒绝**。

### 审计日志（执行日志）

- 每次清理（含干跑与中止）在日志文件追加**一条** JSON 行：含时间、命令、是否干跑、结果分类
  （completed / partial / aborted / dry_run）、总用时，以及**每个目标任务的每个文件**
  （路径、大小、命中规则、最终状态）；
- 日志写入失败只输出警告，不影响清理结果与退出码；
- 备份目录与日志文件若位于目标目录内，扫描时自动排除（不会清掉自己的备份与日志）。

## 通用参数说明

全局选项（必须放在子命令**之前**）：

| 选项 | 说明 |
|---|---|
| `--format text\|json` | 输出格式：`text` 人类可读（默认）；`json` 单行结构化信封（无头调用用） |
| `--non-interactive` | 无头模式：普通确认自动通过（高危仍拒绝并退出 3），全程无输入等待 |
| `--stdin` | 从标准输入读取 JSON 负载作为参数来源（仅允许管道环境使用，终端里用会报错防挂起） |
| `--version` | 显示版本号并退出 |

参数来源优先级（同一键后者不合并、整体覆盖）：

**命令行显式参数 > `--stdin` JSON 负载 > 环境变量（EFC_\*） > 配置文件 > 默认值**

配置文件查找链：`--config` > `EFC_CONFIG` > `./config.json` > `~/.efc/config.json` > 内置默认
（找不到不算错误）。相对路径按当前工作目录解析。

环境变量清单：

| 环境变量 | 对应参数 | 说明 |
|---|---|---|
| `EFC_CONFIG` | `--config` | 配置文件路径 |
| `EFC_FORMAT` | `--format` | `text` 或 `json` |
| `EFC_NON_INTERACTIVE` | `--non-interactive` | `1`/`true` 启用 |
| `EFC_TASK` | `--task` | 任务名列表，换行分隔（Windows cmd 无换行时用 `;`） |
| `EFC_DIR` | `--dir` | 目标目录 |
| `EFC_PATTERNS` | `--pattern` | 正则列表，换行或 `;` 分隔 |
| `EFC_RECURSIVE` | `--recursive` | `1`/`0` |
| `EFC_DRY_RUN` | `--dry-run` | `1`/`0` |
| `EFC_YES` | `--yes` | `1`/`0` |
| `EFC_MAX_BATCH` | `--max-batch` | 1..10 的整数，越界报错 |
| `EFC_BACKUP_DIR` | `backup_dir` | 备份根目录 |
| `EFC_LOG_FILE` | `log_file` | 执行日志路径 |

退出码（硬契约，Agent 判定成败的依据）：

| 码 | 含义 |
|---|---|
| 0 | 成功（含「0 命中，无事可做」） |
| 1 | 未预期内部错误 |
| 2 | 配置/用法/输入错误（不支持平台、UNC、坏正则、任务/目录不存在、参数互斥、非法输入等） |
| 3 | 用户中止 / 安全拦截（拒绝确认、高危未通过、非交互遇高危） |
| 4 | 执行期部分失败（存在备份失败或回收站失败的文件） |

多任务执行时：任一任务有失败文件 → 4；否则任一任务被中止 → 3；否则 0。

## 子命令完整说明

### `efc scan` — 只读预览

逐任务列出将命中的文件，**不删除任何东西**。输出为每任务的命中表格（人类模式）。

```
efc scan [--task NAME]... [--dir PATH] [--pattern REGEX]...
         [--recursive/--no-recursive] [--config PATH] [--json] [-v]
```

| 参数 | 说明 |
|---|---|
| `--task NAME` | 按任务名选取，可重复，按出现顺序执行；未知名报错（码 2） |
| `--dir PATH` | 一次性目标目录（与 `--task` 互斥） |
| `--pattern REGEX` | 文件名正则，可重复；对所选任务**整体替换**其规则 |
| `--recursive` / `--no-recursive` | 三态覆盖：指定则覆盖每任务的递归设置，不指定则用任务自身设置 |
| `--config PATH` | 配置文件路径 |
| `--json` | `--format json` 简写（与 `--format` 同时出现时以 `--format` 为准） |
| `-v` | 详细输出 |

不带任务参数时执行默认任务清单；默认清单为空则报错并提示创建任务（码 2）。

### `efc clean` — 执行清理

按任务循环执行：安全门 → 扫描 → 确认 → 备份 → 分批入回收站。结束后输出按
**任务 → 命中规则**两层的清理总结，并写一条执行日志。

```
efc clean [--task NAME]... [--all-tasks] [--dir PATH] [--pattern REGEX]...
          [--recursive/--no-recursive] [--config PATH] [--yes] [--no-backup]
          [--max-batch N] [--dry-run] [--no-log] [-v]
```

| 参数 | 说明 |
|---|---|
| `--all-tasks` | 执行任务清单中的全部任务（配置顺序） |
| `--yes` | 跳过普通确认；**不能**绕过高危二次确认 |
| `--no-backup` | 本次不备份（放弃恢复手段） |
| `--max-batch N` | 单批文件数上限，1..10，越界直接报错（不是截断） |
| `--dry-run` | 预演：走扫描与确认流程，不删除、不备份 |
| `--no-log` | 本次不写执行日志 |
| 其余 | 同 `scan` |

交互流程（未跳过确认时）：列出命中文件 → 询问「确认将 N 个文件移入回收站?」→ 每批开始前
（首批除外）询问「继续下一批?」，拒绝则停止（已删不回滚）。

### `efc repl` — 交互式会话

进入 `efc> ` 提示符的交互会话，详见下文「REPL 交互式会话用法」。不支持 Agent 模式标志。

### `efc task` — 维护长期任务清单（纯配置操作，不扫描不删除）

```
efc task add    --name NAME [--dir PATH] [--pattern REGEX]...
                [--recursive/--no-recursive] [--default/--no-default]
                [--replace-patterns] [--config PATH]
efc task list   [--config PATH] [--json]
efc task remove [--name NAME | --dir PATH] [--config PATH]
```

- `add`：新增（`--dir` 必填且目录必须存在）；同名再次执行 = 更新，**只覆盖显式给出的字段**。
  规则默认**追加去重**，`--replace-patterns` 整体替换；`--default/--no-default` 设置/取消默认标记，
  不给则保持原值。目录不存在、正则非法等校验失败时**不写盘**（码 2）；
- `list`：列出任务与全部持久化配置；`--json` 输出结构化信封；
- `remove`：按 `--name` 或 `--dir` 移除（二选一）。

### `efc patterns` — 查看任务规则（只读）

```
efc patterns [--task NAME] [--config PATH] [--json]
```

不带 `--task` 列出全部任务的规则；任务清单为空时输出空提示并正常退出（码 0）。

## REPL 交互式会话用法

`efc repl` 启动交互会话（先打印版本横幅，提示输入 `help` 查看命令）。会话内的设置
**只影响当前会话，不写回配置文件**——要持久化请用 `efc task add`。启动时若默认任务清单
**恰好一个**任务，会自动加载进会话。

| 命令 | 行为 |
|---|---|
| `task` | 列出任务清单（含 `[默认]` 标记与规则数） |
| `task NAME` | 加载命名任务为当前会话状态（不存在则提示，不退出） |
| `dir` | 显示当前目录与高危评估结果 |
| `dir PATH` | 设置目标目录：不存在/UNC 拒绝设置；指向高危目录当场警告（不中止会话） |
| `pattern REGEX` | 追加规则，**即时校验**，非法则拒绝追加 |
| `pattern list` | 列出当前规则 |
| `pattern clear` | 清空当前规则 |
| `recursive` / `recursive on\|off` | 查看 / 切换递归 |
| `list` | 用当前状态做只读扫描预览 |
| `clean` | 按当前状态执行清理——与命令行走**完全相同**的清理流程，结束后同样写日志、输出总结 |
| `status` | 汇总当前目录/规则/递归与全局开关 |
| `help` | 命令表 |
| `exit` / `quit` | 退出（EOF/Ctrl+C 两次同） |

会话示例：

```
$ efc repl
efc 1.0.0 — 输入 help 查看命令
efc> dir ~/Downloads
目录已设置: /Users/me/Downloads
efc> pattern '\.tmp$'
已追加: \.tmp$（共 1 条）
efc> pattern '^~\$'
已追加: ^~\$（共 2 条）
efc> recursive on
efc> list
扫描 /Users/me/Downloads（recursive=True）：命中 3 个文件 …
efc> clean
…（确认 → 备份 → 分批入回收站，输出总结）
efc> exit
再见
```

> 注意：会话输入按 shell 引号规则解析，正则中的反斜杠请用**引号包裹**，如 `pattern '\.tmp$'`。

## 无头自动化调用规范（Agent / 脚本管道调用）

面向脚本、CI、Agent 的调用约定。核心保证：**标准输出只有一行结果 JSON、退出码与结果严格一致、
全程无输入等待**。

### 结构化输出（JSON 信封）

`--format json`（或子命令的 `--json` 简写）时，stdout **只输出一行**：

- 成功：`{"data": {...}}`
- 失败：`{"code": <退出码>, "msg": "<中文错误信息>"}`

人类可读信息（表格、进度、总结）一律走 **stderr**，可直接丢弃或转日志。判定成败请以
stdout 的 JSON + 退出码为准。

主要命令的 `data` 内容：

- `scan`：`tasks` 数组，每任务 `{task, root, recursive, scanned_dirs, count, matches:[{path,
  relative, size, mtime}]}`（一次性任务 `task` 为 `null`，`mtime` 为 ISO 时间）；
- `clean`：`{command, result(completed|partial|aborted|dry_run), exit_code, duration_seconds,
  total_matched, trashed, failed, aborted, backup_dir, log_file, summary, tasks:[{name, dir,
  trashed, bytes, by_pattern:[{pattern, files, bytes}], files:[{path, size, pattern, status}]}]}`；
- `task add`：`{saved, task:{...}, config_file}`；`task list`：`{tasks:[...], <全部持久化配置>}`；
  `task remove`：`{removed}`；`patterns`：`{tasks:[{task, dir, default, patterns}]}`。

### 管道传入参数（`--stdin`）

`--stdin` 从标准输入读取一个 JSON 对象作为参数来源，适合参数较多或含特殊字符的场景。
负载只含业务参数，**未知字段或类型不符直接报错（码 2）**；终端（TTY）环境使用 `--stdin`
直接报错以防挂起。可用字段：

```json
{
  "command": "clean",
  "config": "path/to/config.json",
  "task": ["downloads"],
  "all_tasks": false,
  "dir": "D:\\Downloads",
  "patterns": ["^~\\$", "\\.tmp$"],
  "recursive": true,
  "yes": true,
  "max_batch": 5,
  "backup_enabled": true,
  "backup_dir": "C:\\backup",
  "dry_run": true,
  "no_backup": false,
  "no_log": false
}
```

`command` 可省略（默认即所调用的子命令）；若填写则必须与子命令一致，不一致报错（码 2）。
JSON 内反斜杠需双重转义（`\\.`）。传输级开关（`--format`/`--non-interactive`/`--stdin`）
只来自命令行与环境变量，不能写进 stdin 负载。

### 无头调用三要点

1. **`--non-interactive`**：普通确认与批间确认自动通过，全程无 `input()` 等待；**高危目录仍直接
   中止**（结果 `{"code":3}` + 退出码 3，零删除）——这是失败安全设计，请改用非高危目录；
2. `--format json` 的 `clean` 需要同时给 `--yes` 或 `--non-interactive` 之一，明确确认策略，
   否则报错（码 2）；
3. `repl` 不支持无头调用，带任一 Agent 开关（`--format json` / `--non-interactive` / `--stdin`）
   即报错（码 2）。

## 使用示例

### 一次性临时清理（人类交互）

```bash
mkdir /tmp/cleandemo && cd /tmp/cleandemo
touch a.tmp b.tmp keep.txt '~$report.docx'

# 先预览（只读）
efc scan --dir . --pattern '\.tmp$' --pattern '^~\$'

# 确认无误后清理（会列出命中文件并询问确认）
efc clean --dir . --pattern '\.tmp$' --pattern '^~\$'
```

### 持久任务的创建与日常运行

```bash
# 创建任务并标记为默认（目录必须已存在）
efc task add --name downloads --dir ~/Downloads \
    --pattern '^~\$' --pattern '\.tmp$' --pattern '\.bak$' \
    --recursive --default

# 再补一条规则（追加去重）
efc task add --name downloads --pattern '^Thumbs\.db$'

# 日常清理：一条命令执行默认任务清单
efc clean --yes

# 只跑指定任务 / 全部任务
efc clean --task downloads
efc clean --all-tasks --dry-run

# 查看、维护
efc patterns
efc patterns --task downloads
efc task list
efc task remove --name downloads
```

### 预览模式（不删除）

```bash
efc scan --dir /var/tmp --pattern '\.log$' --recursive   # 只读扫描
efc clean --dir /var/tmp --pattern '\.log$' --dry-run    # 走完整确认流程但不删除不备份
```

### 无头脚本调用

```bash
# 方式一：参数全在命令行；高危目录会以 {"code":3} + exit 3 拒绝
efc --format json clean --dir /tmp/cleandemo --pattern '\.tmp$' \
    --yes --no-log

# 方式二：参数走管道（bash/zsh）
echo '{"dir":"/tmp/cleandemo","patterns":["\\.tmp$"],"dry_run":true}' \
  | efc --format json --non-interactive --stdin clean

# 方式二（PowerShell，注意 JSON 反斜杠转义）
'{"dir":"D:\\Downloads","patterns":["\\\\.tmp$"],"dry_run":true}' \
  | efc --format json --non-interactive --stdin clean

# 方式三：环境变量驱动
EFC_DIR=/tmp/cleandemo EFC_PATTERNS='\.tmp$' EFC_YES=1 efc clean

# 脚本内判定成败（以退出码为准）
efc --format json --non-interactive clean --task downloads --yes
rc=$?
[ $rc -eq 0 ] && echo "清理完成"
[ $rc -eq 4 ] && echo "部分文件失败，查看 .efc.log 与备份 manifest"
```

### 常见正则匹配样例

| 目标 | 正则 | 说明 |
|---|---|---|
| Office 临时文件 | `^~\$` | 文件名以 `~$` 开头（如 `~$report.docx`） |
| .tmp / .bak 临时文件 | `\.tmp$`、`\.bak$` | 按扩展名结尾匹配 |
| 日志文件 | `\.log$` | |
| Windows 缩略图缓存 | `^Thumbs\.db$` | 整名精确匹配 |
| macOS 目录元数据 | `^\.DS_Store$` | |
| 下载中的残留分片 | `\.crdownload$`、`\.part$` | |
| 多种后缀一次写 | `\.(tmp|bak|old)$` | 分组 OR |
| 指定前缀的临时文件 | `^temp-` | 文件名以 `temp-` 开头 |

> 记住匹配对象是**文件名**不是路径：`^`/`$` 锚定的是文件名的首尾。

## 安全说明与注意事项

### 备份与恢复

- 默认备份目录 `./.efc-backup`（可用配置 `backup_dir` 修改，支持 `~`），日志默认 `./.efc.log`
  （配置 `log_file`）；
- 每次执行一个时间戳子目录，内含按原目录结构的全部副本 + `manifest.json` 对账清单；
- 恢复方式：从系统回收站还原，或按 `manifest.json` 中记录的「原路径 ↔ 备份路径」手工拷回
  （`efc restore` 自动恢复在 Roadmap 中，当前未提供【待补充】）；
- 备份会**复制全部待清理文件**——敏感数据场景请注意备份目录本身的磁盘占用与访问控制，
  建议放在加密卷；备份不会自动清理，确认不需要后可手工删除旧的时间戳子目录。

### 回收站与平台差异

| 平台 | 回收站 | 注意 |
|---|---|---|
| Windows | 系统回收站 | **UNC 网络路径直接拒绝**（码 2）；网络卷上回收站不可靠；PowerShell 中 `$` 是变量前缀，正则请用单引号 |
| macOS | Finder 废纸篓 | 纯 SSH / 无图形会话时移入失败 → 该文件计失败（码 4），不会物理删除 |
| Linux | freedesktop Trash | 容器/最小系统缺 gvfs 等组件时失败（码 4）；文件系统大小写敏感，`ignore_case` 语义与 Win/mac 有差异 |

### 高危目录二次确认

以下目标判定为高危：卷根/盘符根（`C:\`、`/`、各挂载点）；系统保护目录（Windows 的系统目录、
Program Files、ProgramData 等；macOS 的 `/System`、`/Library`、`/usr`、`/Applications` 等；
Linux 的 `/`、`/usr`、`/etc`、`/var`、`/boot` 等）及其内部；递归范围覆盖保护目录；用户主目录
（home 根）；配置文件 `high_risk_dirs` 中自定义的目录。home 的**子目录**是常规清理对象，不自动高危。

高危确认要求**逐字符输入完整目标路径**（大小写归一化比较），一次不匹配即中止；`--yes`、
`--non-interactive`、管道环境**均无法绕过**（非交互遇高危直接以码 3 中止，零删除）。

### 审计清单存放位置

| 产物 | 默认位置 | 内容 |
|---|---|---|
| 备份副本 + `manifest.json` | `<backup_dir>/<时间戳>/` | 每文件原路径/备份路径/状态/大小/错误 |
| 执行日志 | `<log_file>`（默认 `./.efc.log`） | 每次执行一行 JSON：结果分类、用时、每任务每文件明细 |

### 其他注意事项

- 单批上限 1..10（配置 `max_batch`），越界直接报错；批间确认拒绝后停止，**已删文件不回滚**；
- 备份空间不足时在删除开始前整体中止（码 3，零删除）；
- 单文件备份失败：跳过该文件（记录在案、不删除），其余继续，最终码 4；
- 所有命令只读写本地文件，不访问网络、不写系统库/注册表。

## 常见问题

**Q：删掉的文件能找回来吗？**
A：能。系统回收站里直接还原；或打开本次备份目录的 `manifest.json`，按「原路径 ↔ 备份路径」把文件拷回去。

**Q：为什么我的任务没有被执行？**
A：依次检查：`--pattern` 是整体替换不是追加；`--task` 的名字是否拼写正确（未知名码 2）；不带参数时
只执行 `default: true` 的任务，默认清单为空会提示 `efc task add --default`；`--dir` 与
`--task`/`--all-tasks` 不能同用。

**Q：高危目录确实是我要清理的，怎么办？**
A：交互模式下按提示**完整输入目标路径**即可继续（这是唯一的放行方式）；无头模式下无法放行
（码 3 中止），请改用非高危的更精确子目录作为目标。

**Q：无头调用 stdout 里出现了表格？**
A：那是 stderr 的人类可读输出被混在一起了。请只读 stdout（单行 JSON），stderr 丢弃或转日志。

**Q：退出码 4 是什么意思？**
A：本次执行中有文件备份失败或移入回收站失败（例如 macOS 无图形会话、Linux 缺 trash 组件）。
查看执行日志与备份 manifest 中该文件的状态字段定位原因；失败文件**没有被删除**。

**Q：`--stdin` 报「不能在交互终端（TTY）下使用」？**
A：`--stdin` 只接受管道输入。终端里直接测试请改用 `echo '...' | efc --stdin ...`。

**Q：配置里出现旧版顶层 `target_dir`/`filename_patterns`/`recursive` 就报错？**
A：设计如此：任务清单 `tasks[]` 是任务唯一持久化形式，旧键出现即明确报错（码 2）并提示改用任务清单，
不做静默迁移。

**Q：支持定时自动清理吗？**
A：本期不提供（PRD 列为待复核的非目标）。可用系统计划任务/定时器自行调用无头模式实现，
注意高危目录在无头模式下恒被拒绝【待补充：如后续版本提供，以 PRD 更新为准】。
