"""T1.5（其二）—— 布局：`Board` → KiCad `.kicad_pcb`。

## 摆放：货架式装箱

任务清单原话是「网格摆件」。均匀网格的问题是格子必须按最大的 DIP-20 开，一颗
5mm 的瓷片电容也占一整格，板子会大出两三倍（嘉立创 100×100mm 以内最便宜，值得
省）。所以这里按**货架**摆：每个元件按自己封装的真实占地宽度往右排，排满一行
（默认 100mm）换行，行高取该行最高的元件。

三条硬约束写进了摆放顺序里：

1. `Component.near` 指出的附件（去耦电容、限流电阻、上拉电阻）紧跟在主件后面，
   于是「去耦电容贴着它那片 IC」是排序的自然结果，不需要事后搬。
2. 元件原点吸附到 1.27mm 格点 —— DIP 的引脚间距是 2.54，落在格点上布线器才好走。
   吸附方向统一朝右下（`ceil`），所以吸附**不会**吃掉元件之间的留白。
3. 卧式轴向元件（封装名带 `Horizontal` 的电阻）转 90° 立起来，横向省地方。

## 走线

焊盘坐标从真实封装库算出来，直接喂给 `router.route()`。GND 不走线，交给 B.Cu
整层铺铜（`skip_nets`），所以底层几乎是完整的地平面，顶层留给信号。

「几乎完整」要当成约束来守，不能靠运气：信号如果在底层长途奔袭，两条竖线加两
条横线就能把地平面圈出一块孤岛，落在里面的 GND 焊盘就浮了（实测过，DRC 报
`unconnected_items`）。所以底层的**每格走线代价**调成顶层的 6 倍 —— 穿越一下
照旧便宜，长途绕不过去。`fill_zones()` 顺手数出连通块个数，把这件事变成可验证
的数字而不是希望。

铺铜的**填充**必须由 KiCad 自己算（多边形布尔运算 + 热焊盘），这是全流程唯一
绕不开 pcbnew 的一步，隔离在 `fill_zones()` 里。`render()` 本身是纯函数。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from pathlib import Path

from hdc.pcb import footprints, kicad, router
from hdc.pcb.pack import GND_NET
from hdc.pcb.peripheral import Board, Component

#: KiCad 10 的板文件格式版本。
PCB_VERSION = "20260206"
GENERATOR = "hdc"
GENERATOR_VERSION = "10.0"

#: 2 层板的标准层表：(编号, 名字, 类型, 别名)。编号是 KiCad 固定的，不能自己编。
LAYERS = (
    (0, "F.Cu", "signal", ""), (2, "B.Cu", "signal", ""),
    (9, "F.Adhes", "user", "F.Adhesive"), (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user", ""), (15, "B.Paste", "user", ""),
    (5, "F.SilkS", "user", "F.Silkscreen"), (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user", ""), (3, "B.Mask", "user", ""),
    (17, "Dwgs.User", "user", "User.Drawings"),
    (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"), (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user", ""), (27, "Margin", "user", ""),
    (31, "F.CrtYd", "user", "F.Courtyard"), (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user", ""), (33, "B.Fab", "user", ""),
)

#: 铜层编号 → 层名。
LAYER_NAMES = {0: "F.Cu", 1: "B.Cu"}

#: 铺铜所在的层：底层整块 GND 地平面，顶层留给信号。
ZONE_LAYER = footprints.BACK

#: 生成 uuid 用的名字空间，保证同样输入得到同样文件。
_NS = uuid.UUID("9d1e5c73-4a28-4f16-b0d5-6e3c8a7f2b91")

#: 没接线的焊盘用这个前缀造一个独一无二的假网络名：它不该被布线，但必须当障碍。
_LOOSE = "\x01"

#: 摆放优先级：先芯片，再接口/按键/LED，最后其它。
_KIND_ORDER = {"ic": 0, "header": 1, "switch": 2, "led": 3}


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _n(value: float) -> str:
    return f"{round(value, 6):g}"


def _ref_key(ref: str) -> tuple[str, int]:
    """位号排序键：U2 排在 U10 前面。"""
    head = ref.rstrip("0123456789")
    tail = ref[len(head):]
    return (head, int(tail) if tail else 0)


@dataclass(frozen=True)
class LayoutOptions:
    """布局参数。默认值按嘉立创 2 层板工艺与「100×100mm 以内最便宜」来定。"""

    #: 一行排到多宽换行。
    shelf_width: float = 100.0
    #: 元件之间的横向留白。
    gap: float = 1.27
    #: 行与行之间的留白。
    shelf_gap: float = 2.54
    #: 元件占地到板框的留白。
    edge_margin: float = 2.54
    #: 铜（走线、铺铜）比板框内缩多少。KiCad 默认铜到板边至少 0.5mm。
    copper_inset: float = 1.0
    #: 左上角第一个元件的落点。
    origin: tuple[float, float] = (15.0, 15.0)
    #: 布线参数。GND 不走线，交给底层铺铜；底层每格贵 6 倍，长途留给顶层。
    route: router.RouteOptions = router.RouteOptions(
        skip_nets=frozenset({GND_NET}), via_cost=8.0,
        layer_cost=(1.0, 6.0))


@dataclass(frozen=True)
class Placement:
    """一个元件摆在哪。`pins` 是「焊盘号 → 网络名」，渲染时用来写 `(net ...)`。"""

    ref: str
    value: str
    footprint: str
    x: float
    y: float
    rotation: float
    pins: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Layout:
    """一块布好的板：摆放 + 板框 + 网络表 + 走线。渲染与验证都只看它。"""

    project: str
    placements: tuple[Placement, ...]
    #: 板框 (x1, y1, x2, y2)。
    outline: tuple[float, float, float, float]
    #: 网络名（升序）。网络号 = 下标 + 1，0 号留给「没有网络」。
    nets: tuple[str, ...]
    segments: tuple[router.Segment, ...]
    vias: tuple[router.Via, ...]
    #: 没布通的网络名，如实报出来交给 DRC。
    unrouted: tuple[str, ...]
    #: 底层铺铜多边形 (x1, y1, x2, y2)。
    zone: tuple[float, float, float, float]
    options: router.RouteOptions

    def code_of(self, net: str) -> int:
        """网络名 → KiCad 网络号。空名字是 0 号（未连接）。"""
        if not net:
            return 0
        return self.nets.index(net) + 1


# --- 摆放 -------------------------------------------------------------------

def _rotation(comp: Component) -> float:
    """卧式轴向元件立起来，横向省地方；其余保持 0°（DIP 长轴竖直）。"""
    return 90.0 if "Horizontal" in comp.footprint else 0.0


def _groups(board: Board) -> list[list[Component]]:
    """分组：`near` 指向的附件紧跟主件。组的顺序决定整板的摆放顺序。"""
    children: dict[str, list[Component]] = {}
    for comp in board.components:
        if comp.near:
            children.setdefault(comp.near, []).append(comp)
    attached = {c.ref for kids in children.values() for c in kids}
    heads = sorted((c for c in board.components if c.ref not in attached),
                   key=lambda c: (_KIND_ORDER.get(c.kind, 4), _ref_key(c.ref)))
    return [[head, *sorted(children.get(head.ref, []),
                           key=lambda c: _ref_key(c.ref))]
            for head in heads]


def _cell(comp: Component) -> tuple[Component, float, footprints.Footprint,
                                    tuple[float, float, float, float]]:
    rotation = _rotation(comp)
    found = footprints.load(comp.footprint)
    return comp, rotation, found, found.placed_bbox((0.0, 0.0), rotation)


def _place(board: Board, opts: LayoutOptions) -> list[Placement]:
    """货架式摆放。返回的坐标是元件原点（不是占地左上角）。

    每个元件的原点吸附到格点，且吸附方向朝右下 —— 于是占地左边界永远 ≥ 游标，
    留白不会被吸附吃掉，「占地互不重叠」由此成为构造性质而不是运气。
    """
    grid = opts.route.grid
    left, top = opts.origin
    out: list[Placement] = []
    cursor, shelf_top, shelf_bottom = left, top, top
    for group in _groups(board):
        cells = [_cell(comp) for comp in group]
        width = sum(box[2] - box[0] for *_, box in cells)
        width += (len(cells) + 1) * (opts.gap + grid)
        if out and cursor > left and cursor + width > left + opts.shelf_width:
            cursor, shelf_top = left, shelf_bottom + opts.shelf_gap
            shelf_bottom = shelf_top
        for comp, rotation, found, box in cells:
            x = math.ceil((cursor - box[0]) / grid) * grid
            y = math.ceil((shelf_top - box[1]) / grid) * grid
            out.append(Placement(
                ref=comp.ref, value=comp.value, footprint=comp.footprint,
                x=round(x, 6), y=round(y, 6), rotation=rotation,
                pins=tuple((pad.number, comp.pins.get(int(pad.number), ""))
                           if pad.number.isdigit() else (pad.number, "")
                           for pad in found.pads),
            ))
            cursor = x + box[2] + opts.gap
            shelf_bottom = max(shelf_bottom, y + box[3])
    return out


def _outline(placements: list[Placement],
             opts: LayoutOptions) -> tuple[float, float, float, float]:
    """板框：所有占地的包围盒加留白，再向外取整到整毫米（板尺寸好报价）。"""
    boxes = [footprints.load(p.footprint).placed_bbox((p.x, p.y), p.rotation)
             for p in placements]
    if not boxes:
        return (0.0, 0.0, 10.0, 10.0)
    margin = opts.edge_margin
    return (math.floor(min(b[0] for b in boxes) - margin),
            math.floor(min(b[1] for b in boxes) - margin),
            math.ceil(max(b[2] for b in boxes) + margin),
            math.ceil(max(b[3] for b in boxes) + margin))


# --- 走线 -------------------------------------------------------------------

def _router_pads(placements: list[Placement]) -> list[router.Pad]:
    """摆放结果 → 布线器要的焊盘表。没接线的脚给独一无二的假网络名。

    假网络名让它成为「只有自己能用」的禁区：布线器不会去连它（单焊盘网络会被
    跳过），别的网络也不会压上去。悬空脚也是铜，撞上去照样短路。
    """
    out = []
    for place in placements:
        found = footprints.load(place.footprint)
        spot = found.pad_positions((place.x, place.y), place.rotation)
        nets = dict(place.pins)
        for pad in found.pads:
            x, y = spot[pad.number]
            net = nets.get(pad.number) or f"{_LOOSE}{place.ref}.{pad.number}"
            out.append(router.Pad(net, x, y, pad.radius, pad.layers))
    return out


def _spoke_guards(pads: list[router.Pad],
                  opts: LayoutOptions) -> list[router.Pad]:
    """给铺铜网络的每个焊盘，在铺铜那一层的四个轴向邻格上先占个位。

    KiCad 的热焊盘只在 0/90/180/270 四个方向生成散热桥，而 DRC 要求每个焊盘至少
    2 条（少了就报 `starved_thermal`：焊接时热量全被铜皮吸走）。DIP 相邻引脚本身
    就挡掉了上下两条，只要再有一条底层走线贴着焊盘过，剩下的就不够 2 条。

    做法是把这四个邻格提前划给铺铜网络自己 —— 半径 0 的假焊盘只占它落在的那一格，
    于是布线器自然绕开，而铺铜（本来就不走线）不受影响。用现有的 `Pad` 接口表达，
    布线器不必知道「铺铜」「散热桥」是什么。
    """
    grid = opts.route.grid
    steps = ((grid, 0.0), (-grid, 0.0), (0.0, grid), (0.0, -grid))
    out = []
    for pad in pads:
        if pad.net not in opts.route.skip_nets or ZONE_LAYER not in pad.layers:
            continue
        out += [router.Pad(pad.net, pad.x + dx, pad.y + dy, 0.0, (ZONE_LAYER,))
                for dx, dy in steps]
    return out


def plan_layout(board: Board, options: LayoutOptions | None = None) -> Layout:
    """摆件 + 布线。纯计算（只读封装库），同样输入必得同样输出。"""
    opts = options or LayoutOptions()
    placements = _place(board, opts)
    outline = _outline(placements, opts)
    inset = opts.copper_inset
    copper = (outline[0] + inset, outline[1] + inset,
              outline[2] - inset, outline[3] - inset)
    pads = _router_pads(placements)
    result = router.route(pads + _spoke_guards(pads, opts), opts.route,
                          bounds=copper)
    return Layout(
        project=board.project, placements=tuple(placements), outline=outline,
        nets=tuple(sorted(board.nets)), segments=tuple(result.segments),
        vias=tuple(result.vias), unrouted=tuple(result.unrouted),
        zone=copper, options=opts.route,
    )


# --- s-expression 输出 -------------------------------------------------------

def _header(plan: Layout) -> list[str]:
    out = ["(kicad_pcb", f"\t(version {PCB_VERSION})",
           f"\t(generator {_q(GENERATOR)})",
           f"\t(generator_version {_q(GENERATOR_VERSION)})",
           "\t(general", "\t\t(thickness 1.6)", "\t\t(legacy_teardrops no)", "\t)",
           f"\t(paper {_q('A4')})", "\t(layers"]
    for index, name, kind, alias in LAYERS:
        tail = f" {_q(alias)}" if alias else ""
        out.append(f"\t\t({index} {_q(name)} {kind}{tail})")
    out += ["\t)", "\t(setup", "\t\t(pad_to_mask_clearance 0.05)",
            "\t\t(allow_soldermask_bridges_in_footprints no)", "\t)",
            '\t(net 0 "")']
    for net in plan.nets:
        out.append(f"\t(net {plan.code_of(net)} {_q(net)})")
    return out


def _edges(plan: Layout) -> list[str]:
    x1, y1, x2, y2 = plan.outline
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    out = []
    for index, (a, b) in enumerate(zip(corners, corners[1:] + corners[:1])):
        out += [f"\t(gr_line (start {_n(a[0])} {_n(a[1])})"
                f" (end {_n(b[0])} {_n(b[1])})",
                "\t\t(stroke (width 0.1) (type solid))",
                '\t\t(layer "Edge.Cuts")',
                f"\t\t(uuid {_q(_uid('edge', str(index)))})", "\t)"]
    return out


def _tracks(plan: Layout) -> list[str]:
    out = []
    width = _n(plan.options.track_width)
    for index, seg in enumerate(plan.segments):
        out += ["\t(segment",
                f"\t\t(start {_n(seg.x1)} {_n(seg.y1)})",
                f"\t\t(end {_n(seg.x2)} {_n(seg.y2)})",
                f"\t\t(width {width})",
                f"\t\t(layer {_q(LAYER_NAMES[seg.layer])})",
                f"\t\t(net {plan.code_of(seg.net)})",
                f"\t\t(uuid {_q(_uid('seg', str(index)))})", "\t)"]
    for index, via in enumerate(plan.vias):
        out += ["\t(via", f"\t\t(at {_n(via.x)} {_n(via.y)})",
                f"\t\t(size {_n(plan.options.via_diameter)})",
                f"\t\t(drill {_n(plan.options.via_drill)})",
                '\t\t(layers "F.Cu" "B.Cu")',
                f"\t\t(net {plan.code_of(via.net)})",
                f"\t\t(uuid {_q(_uid('via', str(index)))})", "\t)"]
    return out


def _zone(plan: Layout) -> list[str]:
    """底层 GND 铺铜。

    热焊盘参数是量出来的，不是拍的：DIP 引脚间距 2.54，焊盘半径 0.8，热隔离取
    0.3 时两颗相邻焊盘的隔离圈之间还剩 2.54 − 2×(0.8+0.3) = 0.34mm 的铜带 ——
    大于 `min_thickness`，于是竖直方向的散热桥能成形。取 KiCad 默认的 0.5 会把
    这条铜带挤没，DRC 立刻报 `starved_thermal`（散热桥少于 2 条）。
    """
    x1, y1, x2, y2 = plan.zone
    return ["\t(zone", f"\t\t(net {plan.code_of(GND_NET)})",
            f"\t\t(net_name {_q(GND_NET)})", '\t\t(layers "B.Cu")',
            f"\t\t(uuid {_q(_uid('zone', GND_NET))})",
            f"\t\t(name {_q(GND_NET)})", "\t\t(hatch edge 0.5)",
            "\t\t(connect_pads", "\t\t\t(clearance 0.3)", "\t\t)",
            "\t\t(min_thickness 0.2)", "\t\t(filled_areas_thickness no)",
            "\t\t(fill yes", "\t\t\t(thermal_gap 0.3)",
            "\t\t\t(thermal_bridge_width 0.4)", "\t\t)",
            "\t\t(polygon", "\t\t\t(pts",
            f"\t\t\t\t(xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y1)})"
            f" (xy {_n(x2)} {_n(y2)}) (xy {_n(x1)} {_n(y2)})",
            "\t\t\t)", "\t\t)", "\t)"]


def render(plan: Layout) -> str:
    """把 `Layout` 渲染成 `.kicad_pcb` 文本。纯函数，只额外读封装库。"""
    out = _header(plan)
    for place in plan.placements:
        found = footprints.load(place.footprint)
        nets = {number: (plan.code_of(net), net) for number, net in place.pins}
        out += found.render(ref=place.ref, value=place.value,
                           at=(place.x, place.y), rotation=place.rotation,
                           nets=nets, uid=_uid("fp", place.ref))
    out += _edges(plan) + _tracks(plan) + _zone(plan)
    out += ["\t(embedded_fonts no)", ")"]
    return "\n".join(out) + "\n"


# --- 对外接口 ---------------------------------------------------------------

def write_pcb(board: Board, out_dir: Path,
              options: LayoutOptions | None = None) -> Path:
    """把板子写到 `<out_dir>/<project>.kicad_pcb`，返回该路径。铺铜尚未填充。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{board.project}.kicad_pcb"
    path.write_text(render(plan_layout(board, options)), encoding="utf-8")
    return path


@dataclass(frozen=True)
class ZoneFill:
    """铺铜填充的结果：面积（mm²）与**连通块个数**。

    `islands` 是地平面完整性的判据。KiCad 默认把「没有任何焊盘落在上面」的碎铜
    删掉，所以数出来的每一块都挂着焊盘 —— 于是 `islands == 1` 正好等价于「没有
    哪个 GND 焊盘被围在孤岛里」。大于 1 就是真缺陷：那些焊盘的地是浮的，DRC 会
    报 `unconnected_items`。
    """

    area: float
    islands: int


def fill_zones(path: Path) -> ZoneFill:
    """就地填充铺铜，返回面积与连通块数。这是唯一必须用 pcbnew 的一步。

    多边形布尔运算 + 热焊盘连接自己写一遍不现实，而且必须与 KiCad 的 DRC 用同一
    套算法才有意义 —— 所以这里老老实实调 KiCad 自带的 Python。
    """
    script = path.with_name("_fill_zones.py")
    script.write_text(
        "import pcbnew\n"
        f"board = pcbnew.LoadBoard({str(path)!r})\n"
        "pcbnew.ZONE_FILLER(board).Fill(board.Zones())\n"
        "area = sum(z.GetFilledArea() for z in board.Zones())\n"
        "parts = sum(z.GetFilledPolysList(layer).OutlineCount()\n"
        "            for z in board.Zones() for layer in z.GetLayerSet().Seq())\n"
        "pcbnew.SaveBoard(%r, board)\n"
        # GetFilledArea 是内部单位的平方，换两次才到 mm²
        "print('FILLED_AREA', pcbnew.ToMM(pcbnew.ToMM(area)), parts)\n" % str(path),
        encoding="utf-8")
    try:
        proc = kicad.run_python([script], check=True)
    finally:
        script.unlink(missing_ok=True)
    for line in proc.stdout.splitlines():
        if line.startswith("FILLED_AREA"):
            _, area, parts = line.split()
            return ZoneFill(area=float(area), islands=int(parts))
    raise kicad.KicadError(f"铺铜脚本没有报出面积：\n{proc.stdout}\n{proc.stderr}")
