"""T1.7 —— 全链路编排：RTL → 74HC 电路板 → 嘉立创可上传文件。

一条直线，每一步的产物都落盘，出问题能单步复现：

    synth74.synthesize   RTL          → netlist.json（74 系列门级网表）
    pack.pack            门级网表      → 芯片清单 + 引脚级连接表
    peripheral.build_board 连接表      → 板级网表（电源/时钟/复位/LED/去耦）
    schematic.write_schematic         → <项目>.kicad_sch
    layout.write_pcb + fill_zones     → <项目>.kicad_pcb（摆件 + 布线 + 地平面）
    manufacture.check_drc             → drc.rpt（能不能送厂的权威判据）
    manufacture.export_fabrication    → gerber/ + bom.csv + cpl.csv + PDF + ZIP

`PcbResult` 把每一步的产物与结论收在一起，`ok` 只在「没有布不通的网络 + DRC 干净
+ 制造文件齐全」时为真。做不到的一步进 `skipped` 并说明原因，不假装成功。

顺序上有两处不能调换：铺铜必须在 DRC 之前填（空铺铜会报一堆 `starved_thermal`），
DRC 必须在导出之前跑（送厂的文件应当是检查过的那一版）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from hdc.pcb import kicad, layout, manufacture, peripheral, schematic, synth74
from hdc.pcb.manufacture import DrcReport, FabOptions, Fabrication
from hdc.pcb.pack import Assembly, pack
from hdc.pcb.peripheral import Board, BoardOptions
from hdc.pcb.synth74 import Netlist74
from hdc.toolchain import Toolchain, detect

#: 生成 .kicad_pro 里根图纸 uuid 用的名字空间。
_NS = uuid.UUID("5f2a9c81-7d34-4b6e-9a02-c1e8b47d3f65")


@dataclass(frozen=True)
class PcbResult:
    """一次完整的 RTL → 电路板构建。路径为 None 表示那一步没做（见 `skipped`）。"""

    project: str
    out_dir: Path
    netlist: Netlist74
    assembly: Assembly
    board: Board
    schematic_file: Path
    pcb_file: Path | None = None
    project_file: Path | None = None
    zone_area: float = 0.0
    #: 地平面的连通块个数。1 = 完整；大于 1 说明有 GND 焊盘被围在孤岛里。
    zone_islands: int = 0
    unrouted: tuple[str, ...] = ()
    drc: DrcReport | None = None
    fabrication: Fabrication | None = None
    skipped: tuple[str, ...] = ()

    @property
    def chips(self) -> dict[str, int]:
        """芯片清单：型号 → 数量。"""
        return self.assembly.bom

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self.assembly.warnings) + tuple(self.board.warnings)

    @property
    def ok(self) -> bool:
        """走完了全程，而且每一步都干净。跳过任何一步都算没走完。"""
        return (not self.skipped and not self.unrouted
                and self.zone_islands == 1
                and self.drc is not None and self.drc.ok
                and self.fabrication is not None)

    def errors(self) -> tuple[str, ...]:
        """所有拦路问题，按发现顺序。空表示这块板可以直接送厂。"""
        out = [f"网络 {net} 没布通" for net in self.unrouted]
        if self.pcb_file is not None and self.zone_islands != 1:
            out.append(f"地平面被切成 {self.zone_islands} 块，"
                       f"有 GND 焊盘落在孤岛里")
        if self.drc is not None and not self.drc.ok:
            out += [f"DRC：{line}" for line in self.drc.violations] or ["DRC 有违规"]
        out += [f"跳过：{item}" for item in self.skipped]
        return tuple(out)


def write_project(board_file: Path) -> Path:
    """写一个最小 `.kicad_pro`，让原理图与板图作为同一个工程被双击打开。

    只填 KiCad 必须看到的骨架，设计规则一项不写 —— 留空由 KiCad 填默认值，与
    `kicad-cli` 在没有工程文件时的行为一致，不会悄悄改变 DRC 的判据。
    """
    path = board_file.with_suffix(".kicad_pro")
    root = str(uuid.uuid5(_NS, board_file.stem))
    path.write_text(json.dumps({
        "board": {"design_settings": {}, "layer_presets": [], "viewports": []},
        "boards": [],
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": path.name, "version": 3},
        "net_settings": {},
        "pcbnew": {"page_layout_descr_file": ""},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [[root, "Root"]],
        "text_variables": {},
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _needs_kicad(what: str) -> str | None:
    """KiCad 在不在？不在就返回一句能照做的说明，供 `skipped` 记录。"""
    if kicad.find_cli():
        return None
    return (f"{what}：未找到 kicad-cli（封装库与导出都要用它）。安装 KiCad 或用 "
            f"{kicad.CLI_ENV} 指向可执行文件")


def build_pcb(rtl: Path, project: str, out_dir: Path, *,
              toolchain: Toolchain | None = None,
              board_options: BoardOptions | None = None,
              layout_options: layout.LayoutOptions | None = None,
              fab_options: FabOptions | None = None,
              run_layout: bool = True,
              run_manufacture: bool = True) -> PcbResult:
    """把 `rtl` 一路做到嘉立创可上传的文件，返回每一步的产物与结论。

    `run_layout=False` 只做到原理图（不需要 KiCad 的封装库）；
    `run_manufacture=False` 做到板图与 DRC 为止，不导出 Gerber。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tc = toolchain or detect()

    netlist = synth74.synthesize(tc, Path(rtl), project, out_dir / "synth")
    assembly = pack(netlist)
    board = peripheral.build_board(assembly, board_options)
    sch = schematic.write_schematic(board, out_dir)

    skipped: list[str] = []
    if not run_layout:
        skipped.append("布局布线：调用方要求只做到原理图")
    elif (why := _needs_kicad("布局布线")) is not None:
        skipped.append(why)
    if skipped:
        return PcbResult(project=board.project, out_dir=out_dir, netlist=netlist,
                         assembly=assembly, board=board, schematic_file=sch,
                         skipped=tuple(skipped))

    plan = layout.plan_layout(board, layout_options)
    pcb = out_dir / f"{board.project}.kicad_pcb"
    pcb.write_text(layout.render(plan), encoding="utf-8")
    fill = layout.fill_zones(pcb)            # 必须在 DRC 之前
    pro = write_project(pcb)
    drc = manufacture.check_drc(pcb)

    fab = None
    if not run_manufacture:
        skipped.append("制造文件导出：调用方要求做到板图为止")
    else:
        fab = manufacture.export_fabrication(
            board=pcb, schematic=sch, out_dir=out_dir, options=fab_options)
    return PcbResult(project=board.project, out_dir=out_dir, netlist=netlist,
                     assembly=assembly, board=board, schematic_file=sch,
                     pcb_file=pcb, project_file=pro, zone_area=fill.area,
                     zone_islands=fill.islands,
                     unrouted=plan.unrouted, drc=drc, fabrication=fab,
                     skipped=tuple(skipped))
