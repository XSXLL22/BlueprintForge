"""74 系列芯片知识库：Liberty / Verilog 模型的 cell → 实际芯片型号、封装、引脚表。

本模块是整条 PCB 链路的**单一事实来源**：装箱（`pack`）、原理图（`schematic`）、
布局（`layout`）、BOM（`manufacture`）都从这里取芯片信息，不各自硬编码。

命名与约定
----------
- key 为 vendor `74xx-liberty` 的 cell / 模块名（如 `74AC00_4x1NAND2`），名字里
  `_<N>x<M>` 的 N = 片内槽位数。
- `slots[i]`：第 i 个槽位的「逻辑端口名 → DIP 引脚号」。总线端口按位展开为
  `A0/A1/...`，位序与 yosys 网表 `{"A": [bit0, bit1, ...]}` 一致（0 为 LSB）。
- `shared`：整片共用端口（如 74HC273 的公共 CLK / 复位）。同一片内所有槽位在这些
  端口上**必须接同一网络**，装箱时据此分组。
- `tie`：必须固定接电平的引脚（如 74HC161 的 ~MR / CEP 接 VCC，74HC374 的 ~OE 接 GND）。

引脚号取自各芯片数据手册的标准 DIP 引出线。`tests/test_pcb_cells.py` 用 vendor 的
Liberty 库与 Verilog 模型**交叉校验**端口名与覆盖度，并检查同片引脚不冲突。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

#: 综合可能产出、但不占用实际芯片的 cell —— 装箱阶段退化为网络别名（输入输出网络合并）。
NET_ALIAS_CELLS = frozenset({"$_BUF_"})


class UnmappedCellError(KeyError):
    """综合出的 cell 不在芯片知识库里：需扩充本模块或调整综合脚本。"""

    def __init__(self, cell_type: str) -> None:
        super().__init__(cell_type)
        self.cell_type = cell_type

    def __str__(self) -> str:
        return (
            f"cell {self.cell_type} 没有对应的 74 系列芯片规格；"
            f"请在 hdc/pcb/cells.py 的 CELLS 表中补充，或调整综合脚本使其映射到已有单元"
        )


@dataclass(frozen=True)
class ChipSpec:
    """一颗实际芯片的规格：型号、封装、电源引脚、片内槽位与共用引脚。"""

    part: str
    description: str
    pin_count: int
    vcc: int
    gnd: int
    slots: tuple[Mapping[str, int], ...]
    #: 输出端口的**基名**（去掉总线位下标），用于判断引脚方向：
    #: 未用槽位的输入要接地，输出必须悬空；驱动/扇出检查也依赖它。
    outputs: frozenset[str] = frozenset({"Y"})
    shared: Mapping[str, int] = field(default_factory=dict)
    tie: Mapping[int, str] = field(default_factory=dict)

    @property
    def footprint(self) -> str:
        return f"Package_DIP:DIP-{self.pin_count}_W7.62mm"

    @property
    def slot_count(self) -> int:
        return len(self.slots)

    def is_output(self, port: str) -> bool:
        """端口名（可带总线位下标，如 `S2`）是否为输出。"""
        return port.rstrip("0123456789") in self.outputs or port in self.outputs


def _dip14_quad_aby() -> tuple[Mapping[str, int], ...]:
    """DIP-14 四门「A,B,Y」标准排布（7400 / 7408 / 7432 / 7486）。"""
    return (
        {"A": 1, "B": 2, "Y": 3},
        {"A": 4, "B": 5, "Y": 6},
        {"A": 9, "B": 10, "Y": 8},
        {"A": 12, "B": 13, "Y": 11},
    )


def _dip20_octal_dq() -> tuple[Mapping[str, int], ...]:
    """DIP-20 八位寄存器的 D/Q 排布（74HC273 / 74HC374 / 74HC377 共用）。"""
    return (
        {"D": 3, "Q": 2},
        {"D": 4, "Q": 5},
        {"D": 7, "Q": 6},
        {"D": 8, "Q": 9},
        {"D": 13, "Q": 12},
        {"D": 14, "Q": 15},
        {"D": 17, "Q": 16},
        {"D": 18, "Q": 19},
    )


# --- 组合逻辑门 -------------------------------------------------------------
_GATES: dict[str, ChipSpec] = {
    "74AC00_4x1NAND2": ChipSpec(
        part="74HC00", description="四 2 输入 NAND", pin_count=14, vcc=14, gnd=7,
        slots=_dip14_quad_aby(),
    ),
    "74AC08_4x1AND2": ChipSpec(
        part="74HC08", description="四 2 输入 AND", pin_count=14, vcc=14, gnd=7,
        slots=_dip14_quad_aby(),
    ),
    "74AC32_4x1OR2": ChipSpec(
        part="74HC32", description="四 2 输入 OR", pin_count=14, vcc=14, gnd=7,
        slots=_dip14_quad_aby(),
    ),
    "74AC86_4x1XOR2": ChipSpec(
        part="74HC86", description="四 2 输入 XOR", pin_count=14, vcc=14, gnd=7,
        slots=_dip14_quad_aby(),
    ),
    # 74HC02 的引脚顺序是 Y,A,B（输出在前），与上面四款不同
    "74AC02_4x1NOR2": ChipSpec(
        part="74HC02", description="四 2 输入 NOR", pin_count=14, vcc=14, gnd=7,
        slots=(
            {"Y": 1, "A": 2, "B": 3},
            {"Y": 4, "A": 5, "B": 6},
            {"Y": 8, "A": 9, "B": 10},
            {"Y": 11, "A": 12, "B": 13},
        ),
    ),
    "74AC04_6x1NOT": ChipSpec(
        part="74HC04", description="六反相器", pin_count=14, vcc=14, gnd=7,
        slots=(
            {"A": 1, "Y": 2}, {"A": 3, "Y": 4}, {"A": 5, "Y": 6},
            {"A": 9, "Y": 8}, {"A": 11, "Y": 10}, {"A": 13, "Y": 12},
        ),
    ),
    "74AC10_3x1NAND3": ChipSpec(
        part="74HC10", description="三 3 输入 NAND", pin_count=14, vcc=14, gnd=7,
        slots=(
            {"A": 1, "B": 2, "C": 13, "Y": 12},
            {"A": 3, "B": 4, "C": 5, "Y": 6},
            {"A": 9, "B": 10, "C": 11, "Y": 8},
        ),
    ),
    "74AC20_2x1NAND4": ChipSpec(
        part="74HC20", description="双 4 输入 NAND", pin_count=14, vcc=14, gnd=7,
        slots=(
            {"A": 1, "B": 2, "C": 4, "D": 5, "Y": 6},
            {"A": 9, "B": 10, "C": 12, "D": 13, "Y": 8},
        ),
    ),
}
# --- 时序单元（触发器 / 寄存器）---------------------------------------------
_SEQUENTIAL: dict[str, ChipSpec] = {
    # 双 D 触发器，每个触发器自带独立异步置位/复位，故无共用引脚
    "74AC74_2x1DFFSR": ChipSpec(
        part="74HC74", description="双 D 触发器（带异步置位/复位）",
        pin_count=14, vcc=14, gnd=7,
        slots=(
            {"C": 1, "D": 2, "CLK": 3, "P": 4, "Q": 5},
            {"C": 13, "D": 12, "CLK": 11, "P": 10, "Q": 9},
        ),
        outputs=frozenset({"Q"}),
    ),
    # 八 D 触发器，公共时钟 + 公共异步清零
    "74AC273_8x1DFFR": ChipSpec(
        part="74HC273", description="八 D 触发器（公共异步清零）",
        pin_count=20, vcc=20, gnd=10,
        slots=_dip20_octal_dq(), outputs=frozenset({"Q"}), shared={"CLK": 11, "C": 1},
    ),
    # 八 D 触发器，公共时钟；~OE 固定接地保持输出常开
    "74AC374_8x1DFF": ChipSpec(
        part="74HC374", description="八 D 触发器（三态输出，~OE 接地常开）",
        pin_count=20, vcc=20, gnd=10,
        slots=_dip20_octal_dq(), outputs=frozenset({"Q"}),
        shared={"CLK": 11}, tie={1: "GND"},
    ),
    # 八 D 触发器带时钟使能，~CE 低有效
    "74AC377_8x1DFFE": ChipSpec(
        part="74HC377", description="八 D 触发器（带时钟使能 ~CE）",
        pin_count=20, vcc=20, gnd=10,
        slots=_dip20_octal_dq(), outputs=frozenset({"Q"}), shared={"CE": 1, "CP": 11},
    ),
}

# --- 多路选择器 -------------------------------------------------------------
_MUX: dict[str, ChipSpec] = {
    "74AC257_4x1MUX2": ChipSpec(
        part="74HC257", description="四 2:1 多路选择器", pin_count=16, vcc=16, gnd=8,
        slots=(
            {"A": 2, "B": 3, "Y": 4},
            {"A": 5, "B": 6, "Y": 7},
            {"A": 11, "B": 10, "Y": 9},
            {"A": 14, "B": 13, "Y": 12},
        ),
        shared={"S": 1}, tie={15: "GND"},
    ),
    "74AC153_2x1MUX4": ChipSpec(
        part="74HC153", description="双 4:1 多路选择器", pin_count=16, vcc=16, gnd=8,
        slots=(
            {"A": 6, "B": 5, "C": 4, "D": 3, "Y": 7},
            {"A": 10, "B": 11, "C": 12, "D": 13, "Y": 9},
        ),
        shared={"S": 15, "T": 2}, tie={1: "GND", 14: "GND"},
    ),
    # 8:1 选择器：同一颗 74HC151 的 Y(5) 为同相输出、W(6) 为反相输出，
    # 两种 cell 各占一整片（片内只有一个选择器）。
    "74AC151_1x1MUX8": ChipSpec(
        part="74HC151", description="8:1 多路选择器（同相输出）",
        pin_count=16, vcc=16, gnd=8,
        slots=({
            "A": 4, "B": 3, "C": 2, "D": 1, "E": 15, "F": 14, "G": 13, "H": 12,
            "S": 11, "T": 10, "U": 9, "Y": 5,
        },),
        tie={7: "GND"},
    ),
    "74AC151_1x1MUXI8": ChipSpec(
        part="74HC151", description="8:1 多路选择器（反相输出 W）",
        pin_count=16, vcc=16, gnd=8,
        slots=({
            "A": 4, "B": 3, "C": 2, "D": 1, "E": 15, "F": 14, "G": 13, "H": 12,
            "S": 11, "T": 10, "U": 9, "Y": 6,
        },),
        tie={7: "GND"},
    ),
}
# --- 算术 / 宏单元（来自 vendor/74_models.v 的 techmap 目标）-----------------
_MACRO: dict[str, ChipSpec] = {
    "74AC283_1x1ADD4": ChipSpec(
        part="74HC283", description="4 位全加器", pin_count=16, vcc=16, gnd=8,
        slots=({
            "A0": 5, "A1": 3, "A2": 14, "A3": 12,
            "B0": 6, "B1": 2, "B2": 15, "B3": 11,
            "S0": 4, "S1": 1, "S2": 13, "S3": 10,
            "CI": 7, "CO": 9,
        },),
        outputs=frozenset({"S", "CO"}),
    ),
    "74HC85_1x1CMP4": ChipSpec(
        part="74HC85", description="4 位数值比较器", pin_count=16, vcc=16, gnd=8,
        slots=({
            "A0": 10, "A1": 12, "A2": 13, "A3": 15,
            "B0": 9, "B1": 11, "B2": 14, "B3": 1,
            "Li": 2, "Ei": 3, "Gi": 4,
            "Go": 5, "Eo": 6, "Lo": 7,
        },),
        outputs=frozenset({"Go", "Eo", "Lo"}),
    ),
    "74HC688_1x1EQ8": ChipSpec(
        part="74HC688", description="8 位相等比较器（~P=Q 输出）",
        pin_count=20, vcc=20, gnd=10,
        slots=({
            "A0": 2, "A1": 4, "A2": 6, "A3": 8, "A4": 12, "A5": 14, "A6": 16, "A7": 18,
            "B0": 3, "B1": 5, "B2": 7, "B3": 9, "B4": 11, "B5": 13, "B6": 15, "B7": 17,
            "E": 1, "Q": 19,
        },),
        outputs=frozenset({"Q"}),
    ),
    # 同步 4 位计数器：~MR 与 CEP 固定接 VCC（复位不用、计数使能常开）
    "74AC161_1x1COUNT4": ChipSpec(
        part="74HC161", description="同步 4 位二进制计数器", pin_count=16, vcc=16, gnd=8,
        slots=({
            "A0": 3, "A1": 4, "A2": 5, "A3": 6,
            "Q0": 14, "Q1": 13, "Q2": 12, "Q3": 11,
            "CLK": 2, "LOAD": 9, "ENT": 10, "RCO": 15,
        },),
        outputs=frozenset({"Q", "RCO"}),
        tie={1: "VCC", 7: "VCC"},
    ),
}

#: 全部可综合目标：cell 名 → 芯片规格。
CELLS: dict[str, ChipSpec] = {**_GATES, **_SEQUENTIAL, **_MUX, **_MACRO}

#: 板级外围用的施密特反相器（RC 振荡器时钟源 + 缓冲），不作为综合目标。
SCHMITT_INVERTER = ChipSpec(
    part="74HC14", description="六施密特反相器（RC 振荡器时钟源）",
    pin_count=14, vcc=14, gnd=7,
    slots=(
        {"A": 1, "Y": 2}, {"A": 3, "Y": 4}, {"A": 5, "Y": 6},
        {"A": 9, "Y": 8}, {"A": 11, "Y": 10}, {"A": 13, "Y": 12},
    ),
)


def is_net_alias(cell_type: str) -> bool:
    """该 cell 是否只是网络别名（缓冲器），装箱时合并网络而不占芯片。"""
    return cell_type.strip("\\") in NET_ALIAS_CELLS


def spec_for(cell_type: str) -> ChipSpec:
    """按 cell 名取芯片规格；未收录时抛 `UnmappedCellError`（含可执行的补救提示）。"""
    key = cell_type.strip("\\")
    try:
        return CELLS[key]
    except KeyError:
        raise UnmappedCellError(key) from None
