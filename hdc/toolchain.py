"""定位外部 HDL 工具链（iverilog/vvp/yosys）。"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

# PATH 之外的回退查找目录（便携工具链，如 OSS CAD Suite 解压后的 bin/）。
# 优先用环境变量 OSS_CAD_SUITE 指定套件根目录，避免硬编码机器路径。
_FALLBACK_DIRS = [
    "E:/oss-cad-suite/bin",
    "C:/oss-cad-suite/bin",
    "C:/Program Files/oss-cad-suite/bin",
]


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


def _find(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found

    dirs: list[str] = []
    suite = os.environ.get("OSS_CAD_SUITE")
    if suite:
        dirs.append(f"{suite.rstrip('/\\')}/bin")
    dirs.extend(_FALLBACK_DIRS)

    for d in dirs:
        exe = os.path.join(d, name + ".exe")
        if os.path.isfile(exe):
            return exe
    return None


def detect() -> Toolchain:
    return Toolchain(
        iverilog=_find("iverilog"),
        vvp=_find("vvp"),
        yosys=_find("yosys"),
    )


def env_for(tool_path: str | None) -> dict:
    """构造子进程环境，把工具所在目录及其相邻 lib/ 加入 PATH。

    便携工具链（如 OSS CAD Suite）的可执行文件依赖位于 lib/ 的 DLL，只有把它加进
    PATH 才能直接调用 yosys.exe 等；否则会报 "cannot open shared object file"。
    """
    env = dict(os.environ)
    if tool_path:
        bin_dir = os.path.dirname(os.path.abspath(tool_path))
        lib_dir = os.path.join(os.path.dirname(bin_dir), "lib")
        env["PATH"] = os.pathsep.join([bin_dir, lib_dir, env.get("PATH", "")])
    return env
