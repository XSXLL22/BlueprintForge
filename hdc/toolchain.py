"""定位外部 HDL 工具链（iverilog/vvp/yosys）。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass
class Toolchain:
    iverilog: str | None = None
    vvp: str | None = None
    yosys: str | None = None

    @property
    def can_simulate(self) -> bool:
        return self.iverilog is not None and self.vvp is not None

    @property
    def can_synthesize(self) -> bool:
        return self.yosys is not None


def detect() -> Toolchain:
    return Toolchain(
        iverilog=shutil.which("iverilog"),
        vvp=shutil.which("vvp"),
        yosys=shutil.which("yosys"),
    )
