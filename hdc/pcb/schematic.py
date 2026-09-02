"""T1.4 —— 原理图：`Board` → KiCad `.kicad_sch`。

## 为什么自己生成符号

KiCad 官方 74xx 库里的芯片是**多单元**符号（74HC00 分成 4 个门 + 1 个电源单元），
而我们装箱后放的是「一整片 DIP」，两者对不上。所以这里按 `cells.py` 的引脚表
现场生成单单元的方框符号：左右两列画出整片引脚，标上功能名与引脚号。副作用是
原理图长得像数据手册的引脚图 —— 对「照着焊板子」这个用途反而更合适。

生成的符号直接内嵌在 `lib_symbols` 里（KiCad 本来就把它当缓存读），所以产出的
文件**不依赖任何外部符号库**，换台机器也能打开。

## 连接方式

不画走线，改用「每个引脚伸出一小段线 + 一个网络名标签」。KiCad 的连线引擎按标签
同名合并，得到的网表与 `Board` 完全一致（`tests/test_pcb_schematic.py` 用
`kicad-cli sch export netlist` 反向验证过），而且再复杂的网表也不会画成一团乱麻。

没接线的引脚会放一个 `no_connect` 标记 —— 那是人手画图时也会做的事，同时让 ERC
不再把「未用的 Q 输出」当成错误。
"""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from hdc.pcb.peripheral import Board, Component

#: KiCad 文件格式版本（KiCad 9/10 均为该日期戳）。
SCH_VERSION = "20250114"
GENERATOR = "hdc"
GENERATOR_VERSION = "9.0"

#: 网格 1.27mm —— 引脚、导线、标签必须落在同一网格上才能连上。
GRID = 1.27
PITCH = 2 * GRID          # 引脚行距
PIN_LEN = 2 * GRID        # 引脚线长
STUB = 3 * GRID           # 引脚到标签的导线
MARGIN = 10 * GRID        # 图纸边距
FONT = 1.27               # 文字高度
CHAR_W = 0.85             # 估算文字宽度用（KiCad 默认字体约 0.85·高）

#: 图纸尺寸（横向，mm）。从小到大试，取第一个装得下的。
PAPERS = (("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0),
          ("A1", 841.0, 594.0), ("A0", 1189.0, 841.0))

#: 名字空间，用来把「元件位号 / 符号名」映射成稳定的 UUID（保证输出可重复）。
_NS = uuid.UUID("6f9f8e2a-1d3b-4c5e-9a70-2b1c4d5e6f70")

#: 无功能名引脚的占位符（KiCad 约定）。
NO_NAME = "~"


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _q(text: str) -> str:
    """按 s-expression 规则转义字符串（换行写成字面 \\n，KiCad 就是这么存的）。"""
    out = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + out.replace("\n", "\\n") + '"'


def _num(value: float) -> str:
    return f"{round(value, 4):g}"
# --- 符号几何 ---------------------------------------------------------------

def _pin_count(comp: Component) -> int:
    """元件的物理引脚数。封装名是独立事实来源，比 `pins` 里出现过的号更可靠。"""
    m = re.search(r"DIP-(\d+)", comp.footprint) or re.search(r"1x(\d+)",
                                                             comp.footprint)
    if m:
        return int(m.group(1))
    return max(comp.pins) if comp.pins else 2


@dataclass(frozen=True)
class _Sym:
    """一个生成出来的符号：方框 + 两列（或一列）引脚。"""

    name: str
    value: str
    footprint: str
    description: str
    count: int
    single_column: bool
    #: 引脚号 → (功能名, 电气类型)
    pins: tuple[tuple[int, str, str], ...]
    half_width: float

    @property
    def rows(self) -> int:
        return self.count if self.single_column else math.ceil(self.count / 2)

    @property
    def top(self) -> float:
        return (self.rows - 1) * PITCH / 2 + GRID

    def row_of(self, pin: int) -> tuple[int, int]:
        """引脚 → (行号, 方向)，方向 -1 表示画在左侧、+1 表示右侧。"""
        if self.single_column or pin <= math.ceil(self.count / 2):
            return pin - 1, -1
        return self.count - pin, +1

    def xy(self, pin: int) -> tuple[float, float]:
        """引脚连接点的**库坐标**（Y 轴朝上）。"""
        row, side = self.row_of(pin)
        x = side * (self.half_width + PIN_LEN)
        return x, self.top - GRID - row * PITCH

    @property
    def height(self) -> float:
        return 2 * self.top

def _ref_prefix(ref: str) -> str:
    m = re.match(r"([A-Za-z]+)", ref)
    return m.group(1) if m else "X"


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+\-]", "_", name) or "SYM"


def _half_width(names: list[str]) -> float:
    longest = max((len(n) for n in names), default=1)
    needed = 2 * longest * CHAR_W + PITCH
    return max(5 * GRID, math.ceil(needed / 2 / GRID) * GRID)


def _pin_rows(comp: Component, count: int) -> tuple[tuple[int, str, str], ...]:
    """整片引脚的 (号, 功能名, 电气类型)。没接线的脚记作 NC。"""
    rows = []
    for pin in range(1, count + 1):
        name = comp.ports.get(pin) or (NO_NAME if pin in comp.pins else "NC")
        rows.append((pin, name, comp.pin_types.get(pin, "passive")))
    return tuple(rows)


class _Symbols:
    """按「引脚签名」去重的符号池：同型号只生成一个定义。"""

    def __init__(self) -> None:
        self._by_key: dict[tuple, _Sym] = {}
        self._used: set[str] = set()

    def of(self, comp: Component) -> _Sym:
        count = _pin_count(comp)
        pins = _pin_rows(comp, count)
        single = comp.kind == "header"
        key = (comp.value, comp.footprint, count, single, pins,
               _ref_prefix(comp.ref))
        if key not in self._by_key:
            base = f"{_sanitize(comp.value)}_{count}P"
            name = base
            suffix = 2
            while name in self._used:
                name, suffix = f"{base}_{suffix}", suffix + 1
            self._used.add(name)
            self._by_key[key] = _Sym(
                name=name, value=comp.value, footprint=comp.footprint,
                description=f"{comp.value}（{count} 脚）", count=count,
                single_column=single, pins=pins,
                half_width=_half_width([n for _, n, _ in pins]),
            )
        return self._by_key[key]

    @property
    def all(self) -> list[_Sym]:
        return list(self._by_key.values())

# --- s-expression 输出 -------------------------------------------------------

def _effects(indent: str, *, hide: bool = False, justify: str = "") -> list[str]:
    out = [f"{indent}(effects", f"{indent}\t(font (size {_num(FONT)} {_num(FONT)}))"]
    if justify:
        out.append(f"{indent}\t(justify {justify})")
    if hide:
        out.append(f"{indent}\t(hide yes)")
    out.append(f"{indent})")
    return out


def _property(indent: str, key: str, value: str, x: float, y: float,
              *, hide: bool = False) -> list[str]:
    out = [f"{indent}(property {_q(key)} {_q(value)}",
           f"{indent}\t(at {_num(x)} {_num(y)} 0)"]
    out += _effects(indent + "\t", hide=hide)
    out.append(f"{indent})")
    return out


def _lib_symbol(sym: _Sym, prefix: str) -> list[str]:
    top = sym.top
    out = [f"\t\t(symbol {_q('hdc:' + sym.name)}",
           "\t\t\t(pin_names (offset 0.508))",
           "\t\t\t(exclude_from_sim no)", "\t\t\t(in_bom yes)",
           "\t\t\t(on_board yes)"]
    out += _property("\t\t\t", "Reference", prefix, 0, top + PITCH)
    out += _property("\t\t\t", "Value", sym.value, 0, -top - PITCH)
    out += _property("\t\t\t", "Footprint", sym.footprint, 0, 0, hide=True)
    out += _property("\t\t\t", "Datasheet", "", 0, 0, hide=True)
    out += _property("\t\t\t", "Description", sym.description, 0, 0, hide=True)
    out.append(f"\t\t\t(symbol {_q(sym.name + '_1_1')}")
    out.append(f"\t\t\t\t(rectangle (start {_num(-sym.half_width)} {_num(top)})"
               f" (end {_num(sym.half_width)} {_num(-top)})")
    out.append("\t\t\t\t\t(stroke (width 0.254) (type default))")
    out.append("\t\t\t\t\t(fill (type background)))")
    for pin, name, kind in sym.pins:
        x, y = sym.xy(pin)
        angle = 0 if x < 0 else 180
        out.append(f"\t\t\t\t(pin {kind} line (at {_num(x)} {_num(y)} {angle})"
                   f" (length {_num(PIN_LEN)})")
        out.append(f"\t\t\t\t\t(name {_q(name)}")
        out += _effects("\t\t\t\t\t\t")
        out.append("\t\t\t\t\t)")
        out.append(f"\t\t\t\t\t(number {_q(str(pin))}")
        out += _effects("\t\t\t\t\t\t")
        out.append("\t\t\t\t\t)")
        out.append("\t\t\t\t)")
    out.append("\t\t\t)")
    out.append("\t\t\t(embedded_fonts no)")
    out.append("\t\t)")
    return out

# --- 布图 -------------------------------------------------------------------

@dataclass(frozen=True)
class _Placed:
    comp: Component
    sym: _Sym
    x: float
    y: float


def _label_room(board: Board) -> float:
    longest = max((len(n) for n in board.nets), default=4)
    return math.ceil(longest * CHAR_W / GRID) * GRID + PITCH


def _columns(items: list[tuple[Component, _Sym]], usable_h: float) -> list[list[int]]:
    """按高度把元件切进若干竖列。"""
    cols: list[list[int]] = [[]]
    used = 0.0
    for index, (_, sym) in enumerate(items):
        step = sym.height + PITCH
        if cols[-1] and used + step > usable_h:
            cols.append([])
            used = 0.0
        cols[-1].append(index)
        used += step
    return cols


def _plan(board: Board, items: list[tuple[Component, _Sym]]
          ) -> tuple[str, float, float, list[_Placed], float]:
    """挑一张装得下的图纸，并算出每个元件的坐标。"""
    room = _label_room(board)
    half = max((s.half_width for _, s in items), default=5 * GRID)
    reach = half + PIN_LEN + STUB
    col_w = 2 * reach + 2 * room + PITCH
    band = PITCH * (len(board.notes) + 2)

    paper, width, height = PAPERS[-1]
    cols = [[i for i in range(len(items))]]
    for name, w, h in PAPERS:
        cols = _columns(items, h - 2 * MARGIN - band)
        if len(cols) * col_w <= w - 2 * MARGIN:
            paper, width, height = name, w, h
            break

    placed: list[_Placed] = []
    for col_index, col in enumerate(cols):
        cursor = MARGIN + band
        cx = MARGIN + room + reach + col_index * col_w
        for index in col:
            comp, sym = items[index]
            placed.append(_Placed(comp, sym,
                                  round(cx / GRID) * GRID,
                                  round((cursor + sym.top) / GRID) * GRID))
            cursor += sym.height + PITCH
    return paper, width, height, placed, band

# --- 元件实例、导线、标签 ----------------------------------------------------

def _instance(place: _Placed, root: str, project: str) -> list[str]:
    comp, sym = place.comp, place.sym
    out = ["\t(symbol",
           f"\t\t(lib_id {_q('hdc:' + sym.name)})",
           f"\t\t(at {_num(place.x)} {_num(place.y)} 0)",
           "\t\t(unit 1)",
           "\t\t(exclude_from_sim no)", "\t\t(in_bom yes)", "\t\t(on_board yes)",
           "\t\t(dnp no)",
           f"\t\t(uuid {_q(_uid('sym', comp.ref))})"]
    out += _property("\t\t", "Reference", comp.ref,
                     place.x, place.y - sym.top - PITCH)
    out += _property("\t\t", "Value", comp.value,
                     place.x, place.y + sym.top + PITCH)
    out += _property("\t\t", "Footprint", comp.footprint, place.x, place.y,
                     hide=True)
    out += _property("\t\t", "Datasheet", "", place.x, place.y, hide=True)
    out += _property("\t\t", "Description", comp.description, place.x, place.y,
                     hide=True)
    for pin, _, _ in sym.pins:
        out.append(f"\t\t(pin {_q(str(pin))} (uuid {_q(_uid('pin', comp.ref, str(pin)))}))")
    out += ["\t\t(instances",
            f"\t\t\t(project {_q(project)}",
            f"\t\t\t\t(path {_q('/' + root)}",
            f"\t\t\t\t\t(reference {_q(comp.ref)})", "\t\t\t\t\t(unit 1)",
            "\t\t\t\t)", "\t\t\t)", "\t\t)", "\t)"]
    return out


def _connections(place: _Placed) -> list[str]:
    """每个引脚：接线的伸一段导线 + 网络名标签，没接线的放 no_connect。"""
    comp, sym = place.comp, place.sym
    out: list[str] = []
    for pin, _, _ in sym.pins:
        lx, ly = sym.xy(pin)
        px, py = place.x + lx, place.y - ly
        net = comp.pins.get(pin)
        tag = _uid("wire", comp.ref, str(pin))
        if net is None:
            out += [f"\t(no_connect (at {_num(px)} {_num(py)})"
                    f" (uuid {_q(tag)}))"]
            continue
        side = -1 if lx < 0 else 1
        ex = px + side * STUB
        out += [f"\t(wire (pts (xy {_num(px)} {_num(py)})"
                f" (xy {_num(ex)} {_num(py)}))",
                "\t\t(stroke (width 0) (type default))",
                f"\t\t(uuid {_q(tag)}))"]
        angle = 0 if side > 0 else 180
        just = "left bottom" if side > 0 else "right bottom"
        out.append(f"\t(label {_q(net)}")
        out.append(f"\t\t(at {_num(ex)} {_num(py)} {angle})")
        out += _effects("\t\t", justify=just)
        out.append(f"\t\t(uuid {_q(_uid('label', comp.ref, str(pin)))})")
        out.append("\t)")
    return out

# --- 对外接口 ---------------------------------------------------------------

def render(board: Board) -> str:
    """把 `Board` 渲染成 `.kicad_sch` 文本。纯函数，同样输入必得同样输出。"""
    symbols = _Symbols()
    items = [(c, symbols.of(c)) for c in board.components]
    paper, _, _, placed, band = _plan(board, items)
    root = _uid("sheet", board.project)

    out = ["(kicad_sch", f"\t(version {SCH_VERSION})",
           f"\t(generator {_q(GENERATOR)})",
           f"\t(generator_version {_q(GENERATOR_VERSION)})",
           f"\t(uuid {_q(root)})", f"\t(paper {_q(paper)})",
           "\t(title_block", f"\t\t(title {_q(board.project + ' —— 74HC 离散逻辑板')})",
           "\t\t(rev \"1\")", f"\t\t(company {_q('hdc 自动生成')})"]
    for index, note in enumerate(board.notes[:9], start=1):
        out.append(f"\t\t(comment {index} {_q(note)})")
    out.append("\t)")

    prefixes = {}
    for comp, sym in items:
        prefixes.setdefault(sym.name, _ref_prefix(comp.ref))
    out.append("\t(lib_symbols")
    for sym in sorted(symbols.all, key=lambda s: s.name):
        out += _lib_symbol(sym, prefixes[sym.name])
    out.append("\t)")

    if board.notes:
        text = "\n".join(f"※ {n}" for n in board.notes)
        out.append(f"\t(text {_q(text)}")
        out.append(f"\t\t(at {_num(MARGIN)} {_num(MARGIN + PITCH)} 0)")
        out += _effects("\t\t", justify="left top")
        out.append(f"\t\t(uuid {_q(_uid('notes', board.project))})")
        out.append("\t)")

    for place in placed:
        out += _connections(place)
    for place in placed:
        out += _instance(place, root, board.project)

    out += ["\t(sheet_instances", "\t\t(path \"/\"", "\t\t\t(page \"1\")",
            "\t\t)", "\t)", "\t(embedded_fonts no)", ")"]
    return "\n".join(out) + "\n"


def write_schematic(board: Board, out_dir: Path) -> Path:
    """把原理图写到 `<out_dir>/<project>.kicad_sch`，返回该路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{board.project}.kicad_sch"
    path.write_text(render(board), encoding="utf-8")
    return path
