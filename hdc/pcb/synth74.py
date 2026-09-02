"""T1.1 —— RTL → 74 系列门级网表。

用 yosys 把可综合 Verilog 映射到 vendor `74xx-liberty` 的 Liberty 单元库与
Verilog 宏模型上，产出结构化网表（`netlist.json` + 解析后的数据模型）。

综合脚本基于 vendor 的 `synth_74.ys`，改动两处以适配本项目：
1. 全部库路径改为绝对路径（yosys 是原生 Windows 程序，不认 MSYS 风格路径）；
2. 去掉 `memory_bram` / `extract` 两步 —— 前者面向 FPGA 块存储，后者会把计数器
   抽成上游 benchmark 专用的宏，两者都会让离散 74HC 装箱失去意义。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from hdc.toolchain import Toolchain, env_for

#: vendored 的 74xx-liberty 库根目录。
VENDOR_74XX = Path(__file__).resolve().parents[2] / "vendor" / "74xx-liberty"


class Synth74Error(RuntimeError):
    """74 系列综合失败（工具缺失、顶层不存在、库映射失败等）。"""


@dataclass(frozen=True)
class Cell:
    """一个门级实例：`connections` 为「端口名 → 位标识列表」，位序 0 为 LSB。

    位标识统一为字符串：数字串是网络 id，`"0"`/`"1"` 是常量电平，`"x"`/`"z"` 是无关位。
    """

    name: str
    type: str
    connections: dict[str, list[str]]


@dataclass(frozen=True)
class Port:
    name: str
    direction: str
    bits: list[str]


@dataclass
class Netlist74:
    """一次 74 系列综合的完整产物。"""

    project: str
    cells: list[Cell]
    ports: list[Port]
    net_names: dict[str, str] = field(default_factory=dict)
    netlist_json: Path | None = None
    stat: str = ""
    log: str = ""

    def port(self, name: str) -> Port | None:
        return next((p for p in self.ports if p.name == name), None)


def _script(rtl: Path, project: str, netlist_json: Path) -> str:
    v = VENDOR_74XX.as_posix()
    return "\n".join([
        f"read_verilog {rtl.resolve().as_posix()}",
        f"hierarchy -check -top {project}",
        "proc", "flatten", "opt", "wreduce", "opt",
        f"read_verilog -lib {v}/74_models.v",
        f"read_liberty -lib {v}/74ac.lib",
        f"techmap -map {v}/74_adder.v -map {v}/74_cmp.v"
        f" -map {v}/74_eq.v -map {v}/74_counter.v",
        "synth -run :fine",
        "opt -full -mux_undef -mux_bool",
        f"techmap -map +/techmap.v -map {v}/74_dffe.v",
        "opt",
        "muxcover -mux4 -mux8",
        "opt_merge",
        f"techmap -map {v}/74_mux.v",
        f"dfflibmap -liberty {v}/74ac.lib",
        f"abc -liberty {v}/74ac.lib -D 100000",
        "opt -full",
        "opt_clean -purge",
        "stat",
        f"write_json {netlist_json.as_posix()}",
        "",
    ])


def _bits(raw: list) -> list[str]:
    return [str(b) for b in raw]


def _parse(data: dict, project: str) -> tuple[list[Cell], list[Port], dict[str, str]]:
    modules = data.get("modules", {})
    mod = modules.get(project) or modules.get(f"\\{project}")
    if mod is None:
        raise Synth74Error(
            f"网表里找不到顶层模块 {project}（实际有：{sorted(modules)}）"
        )

    cells = [
        Cell(name=name, type=c["type"].strip("\\"),
             connections={p: _bits(b) for p, b in c.get("connections", {}).items()})
        for name, c in mod.get("cells", {}).items()
    ]
    ports = [
        Port(name=name.strip("\\"), direction=p["direction"], bits=_bits(p["bits"]))
        for name, p in mod.get("ports", {}).items()
    ]

    # 位 → 可读网络名：优先 RTL 里显式命名的网络（hide_name == 0）
    net_names: dict[str, str] = {}
    for name, info in mod.get("netnames", {}).items():
        if info.get("hide_name"):
            continue
        bits = _bits(info.get("bits", []))
        for i, bit in enumerate(bits):
            if not bit.isdigit() or bit in net_names:
                continue
            net_names[bit] = name.strip("\\") + (f"[{i}]" if len(bits) > 1 else "")
    return cells, ports, net_names


def _extract_stat(log: str, project: str) -> str:
    """截取 yosys `stat` 的统计块（`=== <top> ===` 起，到下一个编号步骤为止）。

    不复用 `hdc.verify` 的版本：那里按 "Number of cells" 匹配，较新的 yosys
    改成了 "N cells" 的写法，会匹配不到。
    """
    head = re.search(rf"^=== *\\?{re.escape(project)} *===$", log, re.M)
    start = head.start() if head else None
    if start is None:
        marker = re.search(r"Printing statistics\.", log)
        if marker is None:
            return ""
        start = marker.end()
    tail = log[start:]
    end = re.search(r"^\d+\.\s", tail, re.M)
    return (tail[: end.start()] if end else tail).strip()


def synthesize(tc: Toolchain, rtl: Path, project: str, out_dir: Path) -> Netlist74:
    """把 `rtl` 综合到 74 系列单元，产出 `out_dir/netlist.json` 与结构化网表。"""
    if not tc.can_synthesize:
        raise Synth74Error(
            "未找到 yosys，无法做 74 系列综合；请安装 OSS CAD Suite 并设置 OSS_CAD_SUITE"
        )
    if not (VENDOR_74XX / "74ac.lib").is_file():
        raise Synth74Error(f"vendor 74xx-liberty 库缺失：{VENDOR_74XX}")

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    netlist_json = out_dir / "netlist.json"
    script = out_dir / "synth74.ys"
    script.write_text(_script(Path(rtl), project, netlist_json), encoding="utf-8")

    cp = subprocess.run(
        [tc.yosys, "-s", str(script)], capture_output=True, text=True,
        env=env_for(tc.yosys),
    )
    log = (cp.stdout or "") + "\n" + (cp.stderr or "")
    (out_dir / "synth74.log").write_text(log, encoding="utf-8")

    if cp.returncode != 0 or not netlist_json.is_file():
        errors = [l for l in log.splitlines() if "ERROR" in l] or log.splitlines()[-5:]
        raise Synth74Error("yosys 74 系列综合失败：\n" + "\n".join(errors))

    data = json.loads(netlist_json.read_text(encoding="utf-8"))
    cells, ports, net_names = _parse(data, project)
    stat = _extract_stat(log, project)
    (out_dir / "resource_report_74.txt").write_text(stat or log, encoding="utf-8")

    return Netlist74(
        project=project, cells=cells, ports=ports, net_names=net_names,
        netlist_json=netlist_json, stat=stat, log=log,
    )
