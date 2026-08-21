"""EphemeralFile Cleaner — 临时文件清理（回收站安全删除）。

Windows 专用 CLI：扫描目标目录，把文件名匹配任一正则的文件移入回收站；
删除前自动备份并写入 manifest，高危目录需二次确认，单次执行按小批量推进。
"""

__version__ = "1.0.0"
