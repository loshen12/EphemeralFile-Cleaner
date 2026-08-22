"""REPL 交互会话（Spec §10）。

T019：基础会话循环（横幅、efc> 提示符、exit/quit/EOF 优雅退出、
未知命令不退出、单条命令异常回到提示符）；T025 补齐完整命令表
（task/dir/pattern/recursive/list/clean/status）。
"""

from collections.abc import Callable

from send2trash import send2trash

from efc import __version__
from efc.config import AppConfig
from efc.safety import ensure_supported_platform
from efc.ui import UI

PROMPT = "efc> "


class ReplSession:
    """交互会话：会话内状态与 CLI 共用同一套业务模块。"""

    def __init__(self, config: AppConfig, ui: UI,
                 trash: Callable[[str], None] = send2trash) -> None:
        self._cfg = config
        self._ui = ui
        self._trash = trash

    @property
    def config(self) -> AppConfig:
        return self._cfg

    def handle(self, line: str) -> bool:
        """处理一行命令；返回 False 表示退出会话。"""
        line = line.strip()
        if not line:
            return True
        command = line.split()[0].lower()
        if command in ("exit", "quit"):
            return False
        if command in ("help", "?"):
            self._print_help()
            return True
        print(f"未知命令: {command}（输入 help 查看命令表）")
        return True

    def run(self) -> None:
        """主循环：input 提示符；EOF 退出，Ctrl+C 清行、连按退出。"""
        ensure_supported_platform()
        print(f"efc {__version__} — 输入 help 查看命令")
        consecutive_interrupts = 0
        while True:
            try:
                line = input(PROMPT)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                consecutive_interrupts += 1
                if consecutive_interrupts >= 2:
                    print()
                    break
                print("^C（再按一次 Ctrl+C 退出）")
                continue
            consecutive_interrupts = 0
            try:
                if not self.handle(line):
                    break
            except Exception as e:  # 单条命令出错不崩会话
                print(f"错误: {e}")
        print("再见")

    def _print_help(self) -> None:
        print("命令表：task / dir / pattern / recursive / list / clean / status / help / exit")
        print("（完整命令能力随版本补齐，当前为基础会话）")
