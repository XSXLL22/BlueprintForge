"""T1.2 —— 装箱：门级 cell → 74HC 芯片实例 + 引脚级连接表。

把 yosys 产出的门级网表折叠成实际要焊在板上的芯片：

1. **网络别名**：`$_BUF_` 不占芯片，合并其输入输出网络（并查集）。
2. **共用引脚约束**：74HC273/374/377 的时钟与复位、74HC153/257 的选择端是**整片
   共用**的，只有这些端口接同一网络的 cell 才能装进同一片。这是装箱的核心约束，
   否则会得到电气上错误的板子。
3. **槽位分配**：同一分组内按片内槽位数依次填充，不足则开新片。
4. **电源与悬空处理**：每片接 VCC/GND；`ChipSpec.tie` 指定的引脚接固定电平；
   未用槽位与 x/z 位的**输入**一律接地（CMOS 悬空输入会自激振荡、增大静态功耗），
   输出保持悬空。
5. **驱动检查**：多驱动、无驱动、扇出过大都记为告警。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hdc.pcb.cells import ChipSpec, is_net_alias, spec_for
from hdc.pcb.synth74 import Cell, Netlist74

#: 电源网络名（由常量位与每片的电源引脚产生）。
VCC_NET = "VCC"
GND_NET = "GND"
POWER_NETS = frozenset({VCC_NET, GND_NET})

#: 单个 74HC 输出驱动的输入数上限（超过则告警，需要加缓冲）。
MAX_FANOUT = 10


@dataclass(frozen=True)
class ChipInstance:
    """一颗实际焊在板上的芯片。"""

    ref: str
    spec: ChipSpec
    used_slots: int
    cell_names: tuple[str, ...] = ()

    @property
    def part(self) -> str:
        return self.spec.part


@dataclass(frozen=True)
class PinConn:
    """引脚级连接：某芯片的某引脚接到某网络。"""

    ref: str
    pin: int
    net: str
    port: str = ""
    is_output: bool = False


@dataclass(frozen=True)
class IoPort:
    """顶层端口 → 板上网络（`nets[i]` 为第 i 位，空串表示该位未接出）。"""

    name: str
    direction: str
    nets: list[str]


@dataclass
class Assembly:
    """装箱结果：芯片清单 + 引脚级连接表 + 顶层 IO + 告警。"""

    project: str
    chips: list[ChipInstance] = field(default_factory=list)
    connections: list[PinConn] = field(default_factory=list)
    io: dict[str, IoPort] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def bom(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.chips:
            out[c.part] = out.get(c.part, 0) + 1
        return out

    @property
    def nets(self) -> list[str]:
        return sorted({c.net for c in self.connections})

    def pins_of(self, ref: str) -> list[PinConn]:
        return [c for c in self.connections if c.ref == ref]

    def net_pins(self, net: str) -> list[PinConn]:
        return [c for c in self.connections if c.net == net]
# --- 网络解析 ---------------------------------------------------------------

class _Alias:
    """并查集：把缓冲器两端的位合并成同一个网络。"""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, bit: str) -> str:
        p = self._parent.setdefault(bit, bit)
        while p != bit:
            bit, p = p, self._parent.setdefault(p, p)
        return bit

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _natural_key(name: str) -> tuple:
    """自然序：让 `slice$88` 排在 `slice$100` 前面，保证槽位分配可重复。"""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name))


class _Nets:
    """位标识 → 网络名的解析器（含常量、缓冲器别名、可读名优选）。"""

    def __init__(self, netlist: Netlist74) -> None:
        self._alias = _Alias()
        for c in netlist.cells:
            if not is_net_alias(c.type):
                continue
            bits = [b for bs in c.connections.values() for b in bs if b.isdigit()]
            for other in bits[1:]:
                self._alias.union(bits[0], other)

        # 每个等价类挑一个名字：RTL 显式命名 > 自动生成的 N<bit>
        members: dict[str, list[str]] = {}
        seen = {b for c in netlist.cells for bs in c.connections.values() for b in bs}
        seen |= {b for p in netlist.ports for b in p.bits}
        seen |= set(netlist.net_names)
        for bit in seen:
            if bit.isdigit():
                members.setdefault(self._alias.find(bit), []).append(bit)

        self._name: dict[str, str] = {}
        for root, bits in members.items():
            named = sorted(
                (netlist.net_names[b] for b in bits if b in netlist.net_names),
                key=_natural_key,
            )
            self._name[root] = named[0] if named else f"N{root}"

    def of(self, bit: str) -> str | None:
        """位 → 网络名；常量 0/1 映射到电源网，x/z 返回 None（表示任意值）。"""
        if bit == "0":
            return GND_NET
        if bit == "1":
            return VCC_NET
        if not bit.isdigit():
            return None
        return self._name.get(self._alias.find(bit), f"N{self._alias.find(bit)}")
# --- 装箱 -------------------------------------------------------------------

def _expand(cell: Cell, slot_keys: set[str]) -> dict[str, str]:
    """把 cell 的 `{端口: [位...]}` 展开成 `{槽位端口键: 位}`。

    标量端口直接用端口名；总线端口按位展开成 `A0/A1/...`（与 `ChipSpec.slots` 的
    键一致，位序 0 为 LSB）。
    """
    out: dict[str, str] = {}
    for port, bits in cell.connections.items():
        if len(bits) == 1 and port in slot_keys:
            out[port] = bits[0]
        else:
            for i, bit in enumerate(bits):
                out[f"{port}{i}"] = bit
    return out


def _group_key(cell: Cell, spec: ChipSpec, nets: _Nets) -> tuple:
    """共用引脚约束：共用端口接同一网络的 cell 才能同片。"""
    expanded = _expand(cell, set(spec.slots[0]) | set(spec.shared))
    return tuple(
        (port, nets.of(expanded.get(port, "x")) or "")
        for port in sorted(spec.shared)
    )


def _place(netlist: Netlist74, nets: _Nets) -> list[tuple[ChipSpec, tuple, list[Cell]]]:
    """按 (cell 类型, 共用网络) 分组，再按片内槽位数切成每片一组。"""
    groups: dict[tuple[str, tuple], list[Cell]] = {}
    for cell in sorted(netlist.cells, key=lambda c: _natural_key(c.name)):
        if is_net_alias(cell.type):
            continue
        spec = spec_for(cell.type)
        groups.setdefault((cell.type, _group_key(cell, spec, nets)), []).append(cell)

    chips: list[tuple[ChipSpec, tuple, list[Cell]]] = []
    for (cell_type, shared), members in sorted(
        groups.items(), key=lambda kv: (spec_for(kv[0][0]).part, str(kv[0][1]))
    ):
        spec = spec_for(cell_type)
        for i in range(0, len(members), spec.slot_count):
            chips.append((spec, shared, members[i:i + spec.slot_count]))
    return chips
def _wire_chip(ref: str, spec: ChipSpec, shared: tuple, members: list[Cell],
               nets: _Nets) -> list[PinConn]:
    """产出一颗芯片的全部引脚连接（电源、固定电平、共用端口、各槽位）。"""
    conns = [
        PinConn(ref, spec.vcc, VCC_NET, "VCC"),
        PinConn(ref, spec.gnd, GND_NET, "GND"),
    ]
    for pin, level in spec.tie.items():
        conns.append(PinConn(ref, pin, level, "TIE"))

    shared_nets = dict(shared)
    for port, pin in spec.shared.items():
        net = shared_nets.get(port) or (GND_NET if not spec.is_output(port) else None)
        if net:
            conns.append(PinConn(ref, pin, net, port, spec.is_output(port)))

    slot_keys = set(spec.slots[0]) | set(spec.shared)
    for index, slot in enumerate(spec.slots):
        cell = members[index] if index < len(members) else None
        expanded = _expand(cell, slot_keys) if cell else {}
        for port, pin in slot.items():
            net = nets.of(expanded.get(port, "x"))
            if net is None:
                # 输入必须给确定电平，输出保持悬空
                if spec.is_output(port):
                    continue
                net = GND_NET
            conns.append(PinConn(ref, pin, net, port, spec.is_output(port)))
    return conns


def _check(asm: Assembly) -> list[str]:
    """驱动/扇出检查：多驱动是硬错误级告警，无驱动与扇出过大是提示。"""
    io_nets = {n for p in asm.io.values() for n in p.nets if n}
    input_nets = {n for p in asm.io.values() if p.direction == "input" for n in p.nets}
    out: list[str] = []
    for net in asm.nets:
        if net in POWER_NETS:
            continue
        pins = asm.net_pins(net)
        drivers = [p for p in pins if p.is_output]
        loads = [p for p in pins if not p.is_output]
        if len(drivers) > 1:
            where = ", ".join(f"{p.ref}.{p.pin}" for p in drivers)
            out.append(f"网络 {net} 多驱动（{where}）—— 会造成输出对冲，需检查综合结果")
        elif not drivers and net not in input_nets:
            out.append(f"网络 {net} 无驱动（仅 {len(loads)} 个负载），请确认是否应接输入")
        if len(loads) > MAX_FANOUT:
            out.append(f"网络 {net} 扇出 {len(loads)} 超过 {MAX_FANOUT}，建议加缓冲")
        if net in io_nets and not pins:
            out.append(f"顶层端口网络 {net} 没有落到任何引脚")
    return out


def pack(netlist: Netlist74) -> Assembly:
    """把门级网表装箱成芯片清单与引脚级连接表。"""
    nets = _Nets(netlist)
    asm = Assembly(project=netlist.project)

    for port in netlist.ports:
        asm.io[port.name] = IoPort(
            name=port.name, direction=port.direction,
            nets=[nets.of(b) or "" for b in port.bits],
        )

    for index, (spec, shared, members) in enumerate(_place(netlist, nets), start=1):
        ref = f"U{index}"
        asm.chips.append(ChipInstance(
            ref=ref, spec=spec, used_slots=len(members),
            cell_names=tuple(c.name for c in members),
        ))
        asm.connections.extend(_wire_chip(ref, spec, shared, members, nets))

    asm.warnings.extend(_check(asm))
    return asm
