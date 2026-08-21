# EphemeralFile Cleaner

Windows 专用的一次性/临时文件清理 CLI 工具：给定目标目录与一组文件名正则，
递归（可选）扫描，把文件名匹配任一正则的文件移入 **Windows 回收站**（仅允许
`send2trash`，硬删除 API 全项目禁用）。删除前自动备份到本地备份目录并写入
manifest，高危目录需二次确认，单次执行按小批量（≤10）分批推进。

> 本项目处于开发阶段，完整文档见 `docs/dev.md` 与 `docs/plan.md`。
> README（安装 / 快速上手 / config.json 说明 / 安全模型 / FAQ）将在最终交付时补齐。
