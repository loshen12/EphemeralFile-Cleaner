# EphemeralFile Cleaner

**跨平台（Windows / macOS / Linux）**的一次性/临时文件清理 CLI 工具：以**任务**为单位组织清理——每个任务 = 目标目录 + 一组文件名正则。任务可持久化为**长期任务清单**（`efc task add`，可反复执行，非一次性），其中标记 `default: true` 的任务构成**默认任务清单**（无参数执行时自动运行）；也可在每次执行时一次性明确（`--dir` + `--pattern`，不落盘）。执行时递归（可选）扫描，把文件名匹配任一正则的文件移入**系统回收站**（Windows 回收站 / macOS 废纸篓 / Linux Trash，仅允许 `send2trash`，硬删除 API 全项目禁用）。删除前自动备份到本地备份目录并写入 manifest，高危目录需二次确认，单次执行按小批量（≤10）分批推进。

> **开发进度**（2026-08-22，需求 v2.0）：项目处于开发初期。计划共 27 个任务（详见
> [docs/Plan.md](docs/Plan.md)），目前仅完成 T001 项目骨架
> （`efc --help` / `efc --version` 可用），尚无任何业务命令；下一任务为
> T002（异常类与数据模型）。
> 文档体系：[docs/PRD.md](docs/PRD.md)（业务需求）→ [docs/Spec.md](docs/Spec.md)（技术方案）→ [docs/Plan.md](docs/Plan.md)（开发计划）。
> README（三平台安装 / 快速上手 / config.json 说明 / 安全模型 / FAQ）将在 T027 最终交付时补齐。
