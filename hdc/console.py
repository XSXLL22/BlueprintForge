"""命令行输出的编码兜底 —— 让 UTF-8 摘要在任何终端上都打得出来。

为什么需要它：Windows 上 `sys.stdout` 只在直连控制台时是 UTF-8；一旦重定向到管道
或文件，Python 就改用本地代码页（简中机器上是 GBK）。摘要里有 `mm²`（U+00B2），
GBK 编不出来，于是 `python -m hdc counter.v --pcb > log.txt` 会在**全链路已经跑完
之后**抛 `UnicodeEncodeError`：板子其实做好了，用户只看到一段 traceback。

对策就是把输出流按 UTF-8 重新配置，与 Linux/macOS 上的行为对齐。`errors="replace"`
是最后一道兜底：任何编不出的字符宁可显示成 `?`，也不能让一次成功的构建以异常收场。
"""
from __future__ import annotations

from typing import Any

#: 已经是这些编码就不动手（`utf8` / `UTF-8` / `utf_8` 都算）。
_UTF8_ALIASES = {"utf-8", "utf8"}


def _is_utf8(encoding: Any) -> bool:
    if not isinstance(encoding, str):
        return False
    return encoding.lower().replace("_", "-").replace("-", "") == "utf8"


def use_utf8(stream: Any) -> bool:
    """把 `stream` 切成 UTF-8 输出。返回是否真的改动了它。

    对不支持 `reconfigure()` 的流（`StringIO`、测试替身）和 `None`
    （pythonw 下 `sys.stdout` 就是 None）静默跳过 —— 这是兜底，不是校验。
    """
    if stream is None:
        return False
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return False
    if _is_utf8(getattr(stream, "encoding", None)) and \
            getattr(stream, "errors", None) == "replace":
        return False
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError, AttributeError):
        return False       # 流已关闭 / 不可重配 —— 让调用方照原样打印
    return True
