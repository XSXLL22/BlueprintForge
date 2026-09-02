"""hdc.pcb —— 数字逻辑网表 → 74HC 离散逻辑电路板 → 嘉立创可打样工程文件。

链路：`synth74`（RTL→74 系列网表）→ `pack`（装箱到芯片与引脚）→ `peripheral`
（板级外围）→ `schematic`（KiCad 原理图）→ `layout`（KiCad 板图 + 布线）→
`manufacture`（Gerber / 钻孔 / BOM / CPL / 嘉立创 ZIP）。

对外只暴露 `build_pcb()` 一个入口，其余模块是它的实现细节（也可单独用于测试）。
入口按需惰性导入，`import hdc.pcb.cells` 之类的轻量用法不会牵连整条链路。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from hdc.pcb.pipeline import PcbResult, build_pcb

__all__ = ["build_pcb", "PcbResult"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from hdc.pcb import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
