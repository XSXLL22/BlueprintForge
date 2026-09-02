"""T1.3 —— 板级外围：把装箱结果补全成一块「插电即用」的电路板。

补的东西都是让逻辑真正跑起来的最小必需项：

- **电源**：2 脚排针 + 100µF 体电容，每片 IC 一颗 100nF 去耦（就近摆放，由
  `Component.near` 传给布局阶段）。
- **时钟**：74HC14 施密特反相器 + RC 组成张弛振荡器，第二级反相器整形输出；
  再经 1x3 跳线排针在「板载 RC 时钟」与「外部时钟」之间选择。
- **复位**：按键 + 10k 上拉/下拉，极性由综合网表里复位端的有效电平推断。
- **输出**：每个顶层输出位一颗 LED + 限流电阻（1k → 约 3mA，在 74HC 的安全区）。
- **其它输入**：排针接出，并各配 10k 下拉，避免 CMOS 输入悬空。

产物 `Board` 是一张扁平的元件表（`Component.pins` 为「引脚号 → 网络」），
原理图、布局、BOM 三个下游阶段都只依赖它。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from hdc.pcb.cells import SCHMITT_INVERTER
from hdc.pcb.pack import GND_NET, POWER_NETS, VCC_NET, Assembly, IoPort

#: 时钟端口名（liberty / 模型里的时钟输入）。
CLOCK_PORTS = frozenset({"CLK", "CP"})
#: 异步清零端口名 —— vendor Liberty 里 `clear: "C'"`，即**低电平有效**。
RESET_PORTS = frozenset({"C"})
#: 顶层端口名里可识别为复位的模式。
RESET_NAME_RE = re.compile(r"^(n?rst|reset|nreset)(_?n)?$", re.I)

#: KiCad 电气类型。电源引脚标 power_in / power_out，ERC 才能查出「电源没人驱动」。
PWR_IN, PWR_OUT, PASSIVE = "power_in", "power_out", "passive"

_F = {
    "header": "Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical",
    "cap": "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
    "cap_bulk": "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm",
    "res": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
    "led": "LED_THT:LED_D5.0mm",
    "switch": "Button_Switch_THT:SW_PUSH_6mm",
}


@dataclass(frozen=True)
class BoardOptions:
    """外围参数。改这里就能改时钟频率、LED 亮度等，不用动代码逻辑。"""

    clock_r_ohm: float = 100_000.0
    clock_c_uf: float = 1.0
    led_series_ohm: int = 1_000
    pull_ohm: int = 10_000
    decoupling_nf: int = 100
    bulk_uf: int = 100
    add_clock: bool = True
    add_reset: bool = True
    add_leds: bool = True
    add_input_header: bool = True

    @property
    def clock_hz(self) -> float:
        """74HC14 张弛振荡器近似频率 f ≈ 1 / (0.8·R·C)。"""
        return 1.0 / (0.8 * self.clock_r_ohm * self.clock_c_uf * 1e-6)


@dataclass(frozen=True)
class Component:
    """板上一个元件。`pins` 是「引脚号 → 网络名」，`near` 用于就近摆放。

    `ports` / `pin_types` 是给原理图阶段用的引脚元数据：功能名（VCC、CLK、D0……）
    与 KiCad 电气类型。放在这里是因为「哪个脚是输出、哪个脚是电源」这件事只有
    装箱阶段（手里有 `ChipSpec`）知道，下游不该再去猜。
    """

    ref: str
    value: str
    footprint: str
    kind: str
    pins: dict[int, str]
    description: str = ""
    near: str = ""
    ports: dict[int, str] = field(default_factory=dict)
    pin_types: dict[int, str] = field(default_factory=dict)


@dataclass
class Board:
    """完整板级网表：元件表 + 顶层 IO + 说明 + 告警。"""

    project: str
    components: list[Component] = field(default_factory=list)
    io: dict[str, IoPort] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def nets(self) -> list[str]:
        return sorted({n for c in self.components for n in c.pins.values()})

    def by_ref(self, ref: str) -> Component:
        return next(c for c in self.components if c.ref == ref)

    def pins_on(self, net: str) -> list[tuple[str, int]]:
        return [(c.ref, p) for c in self.components
                for p, n in c.pins.items() if n == net]
# --- 信号识别 ---------------------------------------------------------------

def _io_nets(asm: Assembly, direction: str) -> set[str]:
    return {n for p in asm.io.values() if p.direction == direction
            for n in p.nets if n}


def _dominant(asm: Assembly, ports: frozenset[str], allowed: set[str]) -> str:
    """在指定端口上出现最多的网络（限定在 `allowed` 内，平票时取名字最小的）。"""
    hits = Counter(c.net for c in asm.connections
                   if c.port in ports and c.net in allowed)
    if not hits:
        return ""
    return min(hits, key=lambda n: (-hits[n], n))


def _find_clock(asm: Assembly) -> str:
    """时钟网络：落在 CLK/CP 端口、且是顶层输入的那个网络。

    只认顶层输入 —— 内部产生的分频时钟不该再挂一个振荡器。
    """
    return _dominant(asm, CLOCK_PORTS, _io_nets(asm, "input"))


def _find_reset(asm: Assembly) -> tuple[str, bool]:
    """返回 (复位网络, 是否低电平有效)。

    先看综合结果：落在清零端口上的网络一定是低电平有效（Liberty 里写作
    `clear: "C'"`）。没有清零端口时退回按顶层端口名推断，名字带 `n` 的算低有效。
    """
    inputs = _io_nets(asm, "input")
    net = _dominant(asm, RESET_PORTS, inputs)
    if net:
        return net, True
    for port in sorted(asm.io.values(), key=lambda p: p.name):
        if port.direction == "input" and RESET_NAME_RE.match(port.name) and port.nets:
            active_low = bool(re.search(r"(^n|_n$)", port.name, re.I))
            return port.nets[0], active_low
    return "", True

# --- 位号分配 ---------------------------------------------------------------

class _Refs:
    """按前缀独立计数分配位号，并避开装箱阶段已经用掉的号（U1、U2……）。"""

    def __init__(self, taken: list[str]) -> None:
        self._next: dict[str, int] = {}
        for ref in taken:
            m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
            if m:
                prefix, num = m.group(1), int(m.group(2))
                self._next[prefix] = max(self._next.get(prefix, 1), num + 1)

    def take(self, prefix: str) -> str:
        num = self._next.get(prefix, 1)
        self._next[prefix] = num + 1
        return f"{prefix}{num}"


def _fmt_ohm(value: float) -> str:
    """1000 → "1k"，100000 → "100k"，470 → "470R"（BOM 里人眼可读）。"""
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if value >= 1_000:
        return f"{value / 1_000:g}k"
    return f"{value:g}R"


def _fmt_hz(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MHz"
    if value >= 1_000:
        return f"{value / 1_000:.2f} kHz"
    return f"{value:.1f} Hz"

# --- 各段外围 ---------------------------------------------------------------

def _add_chips(board: Board, asm: Assembly) -> None:
    """装箱结果原样搬到板上：引脚级连接表折成「引脚号 → 网络」。"""
    for chip in asm.chips:
        conns = asm.pins_of(chip.ref)
        board.components.append(Component(
            ref=chip.ref, value=chip.part, footprint=chip.spec.footprint,
            kind="ic", pins={c.pin: c.net for c in conns},
            description=f"{chip.spec.description}（用 {chip.used_slots}/"
                        f"{chip.spec.slot_count} 槽位）",
            ports={c.pin: c.port for c in conns},
            pin_types={c.pin: _pin_type(c.port, c.is_output) for c in conns},
        ))


def _pin_type(port: str, is_output: bool) -> str:
    if port in ("VCC", "GND", "TIE"):
        return PWR_IN
    return "output" if is_output else "input"


def _add_power(board: Board, refs: _Refs, opts: BoardOptions) -> None:
    header = refs.take("J")
    board.components.append(Component(
        ref=header, value="POWER", footprint=_F["header"].format(n=2),
        kind="header", pins={1: VCC_NET, 2: GND_NET},
        description="电源输入（1=VCC，2=GND）",
        ports={1: VCC_NET, 2: GND_NET},
        pin_types={1: PWR_OUT, 2: PWR_OUT},   # 整板电源的唯一来源
    ))
    board.components.append(Component(
        ref=refs.take("C"), value=f"{opts.bulk_uf}uF", footprint=_F["cap_bulk"],
        kind="cap_bulk", pins={1: VCC_NET, 2: GND_NET},
        description="电源体电容，吸收整板开关电流", near=header,
        ports={1: "+", 2: "-"},
    ))
    board.notes.append(
        f"电源：{header} 接 5V（1=VCC，2=GND）。74HC 系列 2~6V 均可工作，"
        f"板上另有 {opts.bulk_uf}µF 体电容与每片 {opts.decoupling_nf}nF 去耦。"
    )

def _add_clock(board: Board, refs: _Refs, opts: BoardOptions, clock_net: str) -> None:
    """74HC14 张弛振荡器 + 整形级 + 时钟源跳线。

    第一级反相器与 R/C 构成振荡（施密特回差提供正反馈门限），第二级把波形整成
    干净的方波再送到跳线；剩下四个反相器的输入接地，避免 CMOS 悬空。
    """
    spec = SCHMITT_INVERTER
    res, cap = refs.take("R"), refs.take("C")
    select, chip = refs.take("J"), refs.take("U")

    pins = {spec.vcc: VCC_NET, spec.gnd: GND_NET}
    ports = {spec.vcc: "VCC", spec.gnd: "GND"}
    types = {spec.vcc: PWR_IN, spec.gnd: PWR_IN}
    pins[spec.slots[0]["A"]], pins[spec.slots[0]["Y"]] = "CLK_RC", "CLK_OSC"
    pins[spec.slots[1]["A"]], pins[spec.slots[1]["Y"]] = "CLK_OSC", "CLK_SRC"
    for index, slot in enumerate(spec.slots, start=1):
        if index > 2:
            pins[slot["A"]] = GND_NET
        ports[slot["A"]], types[slot["A"]] = f"{index}A", "input"
        if slot["Y"] in pins:
            ports[slot["Y"]], types[slot["Y"]] = f"{index}Y", "output"

    board.components.extend([
        Component(ref=chip, value=spec.part, footprint=spec.footprint, kind="ic",
                  pins=pins, description=f"{spec.description}（振荡 + 整形）",
                  ports=ports, pin_types=types),
        Component(ref=res, value=_fmt_ohm(opts.clock_r_ohm), footprint=_F["res"],
                  kind="res", pins={1: "CLK_RC", 2: "CLK_OSC"},
                  description="振荡定时电阻", near=chip),
        Component(ref=cap, value=f"{opts.clock_c_uf:g}uF", footprint=_F["cap"],
                  kind="cap_timing", pins={1: "CLK_RC", 2: GND_NET},
                  description="振荡定时电容", near=chip),
        Component(ref=select, value="CLK_SEL", footprint=_F["header"].format(n=3),
                  kind="header",
                  pins={1: "CLK_SRC", 2: clock_net, 3: "CLK_EXT"},
                  ports={1: "RC", 2: "CLK", 3: "EXT"},
                  description="时钟源选择：1-2 板载 RC，2-3 外部输入"),
    ])
    board.notes.append(
        f"时钟：{chip}(74HC14) 与 {res}/{cap} 组成张弛振荡器，"
        f"f ≈ 1/(0.8·R·C) ≈ {_fmt_hz(opts.clock_hz)}；{select} 短接 1-2 用板载时钟，"
        f"短接 2-3 则由 {select}.3 输入外部时钟（驱动网络 {clock_net}）。"
    )

def _add_reset(board: Board, refs: _Refs, opts: BoardOptions,
               net: str, active_low: bool) -> None:
    """复位按键 + 电阻。极性决定电阻拉向哪条电源轨，按键拉向另一条。"""
    pull, button = refs.take("R"), refs.take("SW")
    rail = VCC_NET if active_low else GND_NET
    pressed = GND_NET if active_low else VCC_NET
    board.components.extend([
        Component(ref=pull, value=_fmt_ohm(opts.pull_ohm), footprint=_F["res"],
                  kind="res", pins={1: rail, 2: net},
                  description=f"{net} {'上拉' if active_low else '下拉'}", near=button),
        Component(ref=button, value="RESET", footprint=_F["switch"], kind="switch",
                  pins={1: net, 2: pressed}, description="复位按键",
                  ports={1: "A", 2: "B"}),
    ])
    polarity = "低电平复位" if active_low else "高电平复位"
    board.notes.append(
        f"复位：{polarity}。{pull}({_fmt_ohm(opts.pull_ohm)}) 把 {net} 常态保持在 "
        f"{rail}，按下 {button} 时拉到 {pressed} 触发复位。"
    )


def _add_leds(board: Board, refs: _Refs, opts: BoardOptions, asm: Assembly) -> None:
    """每个顶层输出位一颗 LED：网络 →限流电阻→ 阳极，阴极到地。"""
    index = 0
    for name in sorted(asm.io):
        port = asm.io[name]
        if port.direction != "output":
            continue
        for bit, net in enumerate(port.nets):
            if not net:
                board.warnings.append(f"输出 {name}[{bit}] 没有对应网络，未接 LED")
                continue
            index += 1
            anode = f"LED{index}_A"
            led, res = refs.take("D"), refs.take("R")
            board.components.extend([
                Component(ref=led, value="LED", footprint=_F["led"], kind="led",
                          pins={1: GND_NET, 2: anode}, ports={1: "K", 2: "A"},
                          description=f"{name}[{bit}] 状态指示（1=亮）"),
                Component(ref=res, value=_fmt_ohm(opts.led_series_ohm),
                          footprint=_F["res"], kind="res", pins={1: net, 2: anode},
                          description=f"{name}[{bit}] LED 限流电阻", near=led),
            ])
    if index:
        board.notes.append(
            f"输出：{index} 颗 LED 直接由 74HC 输出经 "
            f"{_fmt_ohm(opts.led_series_ohm)} 驱动，单脚电流约 "
            f"{(5 - 2.0) / opts.led_series_ohm * 1000:.1f}mA，远低于 74HC 的 25mA 上限。"
        )

def _add_inputs(board: Board, refs: _Refs, opts: BoardOptions,
                asm: Assembly, skip: set[str]) -> None:
    """时钟/复位之外的顶层输入：每个端口一根排针，每位配 10k 下拉。"""
    for name in sorted(asm.io):
        port = asm.io[name]
        if port.direction != "input":
            continue
        nets = [n for n in port.nets if n and n not in skip]
        if not nets:
            continue
        header = refs.take("J")
        board.components.append(Component(
            ref=header, value=name.upper(),
            footprint=_F["header"].format(n=len(nets)), kind="header",
            pins={i + 1: n for i, n in enumerate(nets)},
            ports={i + 1: f"{name}{i}" for i, _ in enumerate(nets)},
            description=f"输入 {name}（第 i 脚 = 第 i-1 位，LSB 在 1 脚）",
        ))
        for net in nets:
            board.components.append(Component(
                ref=refs.take("R"), value=_fmt_ohm(opts.pull_ohm),
                footprint=_F["res"], kind="res", pins={1: net, 2: GND_NET},
                description=f"{net} 下拉（悬空时读 0）", near=header,
            ))
        board.notes.append(
            f"输入 {name}：{header} 每脚一位，各配 {_fmt_ohm(opts.pull_ohm)} 下拉，"
            f"不接线时读到 0；接 VCC 即为 1。"
        )


def _add_decoupling(board: Board, refs: _Refs, opts: BoardOptions) -> None:
    """每片 IC 一颗去耦电容，`near` 让布局阶段把它贴在该片旁边。"""
    for ic in [c for c in board.components if c.kind == "ic"]:
        board.components.append(Component(
            ref=refs.take("C"), value=f"{opts.decoupling_nf}nF", footprint=_F["cap"],
            kind="cap_decoupling", pins={1: VCC_NET, 2: GND_NET},
            description=f"{ic.ref} 电源去耦（须紧贴 {ic.ref} 的 VCC/GND 引脚）",
            near=ic.ref, ports={1: "+", 2: "-"},
        ))

# --- 组装 -------------------------------------------------------------------

def _sanity(board: Board) -> None:
    """板级自检：位号唯一、没有只连一个引脚的孤立网络（排针除外）。"""
    dupes = [r for r, n in Counter(c.ref for c in board.components).items() if n > 1]
    for ref in sorted(dupes):
        board.warnings.append(f"位号 {ref} 重复，原理图/BOM 会对不上")

    on_header = {n for c in board.components if c.kind == "header"
                 for n in c.pins.values()}
    for net in board.nets:
        if net in POWER_NETS or net in on_header:
            continue          # 排针上的网络本来就通到板外
        if len(board.pins_on(net)) < 2:
            board.warnings.append(f"网络 {net} 只连了一个引脚，多半是没用到的输出")


def build_board(asm: Assembly, options: BoardOptions | None = None) -> Board:
    """把装箱结果补成一块可制造的完整板级网表。

    只读 `asm`，产出新的 `Board`；同样的输入永远得到同样的位号与网络（下游的
    原理图 / 布局 / BOM 都依赖这个确定性）。
    """
    opts = options or BoardOptions()
    board = Board(project=asm.project, io=dict(asm.io), warnings=list(asm.warnings))
    refs = _Refs([c.ref for c in asm.chips])

    _add_chips(board, asm)
    _add_power(board, refs, opts)

    clock_net = _find_clock(asm) if opts.add_clock else ""
    if clock_net:
        _add_clock(board, refs, opts, clock_net)

    reset_net, active_low = _find_reset(asm) if opts.add_reset else ("", True)
    if reset_net:
        _add_reset(board, refs, opts, reset_net, active_low)

    if opts.add_leds:
        _add_leds(board, refs, opts, asm)
    if opts.add_input_header:
        _add_inputs(board, refs, opts, asm, {clock_net, reset_net} - {""})

    _add_decoupling(board, refs, opts)
    _sanity(board)
    return board
