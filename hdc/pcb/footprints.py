"""T1.5（其一）—— 读 KiCad 封装库，并把封装重新写进 `.kicad_pcb`。

## 为什么要自己读 `.kicad_mod`

符号可以自己画（见 `schematic.py`），封装不行 —— DIP-20 的焊盘孔径、阻焊开窗、
丝印外框都是有工艺含义的尺寸，照抄 KiCad 官方库最省事也最可靠。KiCad 的
`.kicad_mod` 就是 s-expression 文本，直接读进来就行，不必启动 KiCad。

`.kicad_pcb` 里的封装是**整段内嵌**的（不像原理图那样引用外部库），所以这里的
做法是：把库文件正文原样搬过去，只改四处 ——

1. 名字换成 `"库:名字"`；
2. 补 `(at x y 旋转)` 与 `(uuid ...)`；
3. `Reference` / `Value` 两个属性改成实际位号与型号；
4. 每个焊盘加上 `(net 编号 "网络名")`。

其余几何（丝印、外框、阻焊）一个字节都不动。好处是产出的板子文件自带全部封装，
换台机器打开不缺库；坏处是文件大一些 —— 无所谓。

## 旋转方向

`(at x y θ)` 对焊盘本地坐标的变换是（图纸 Y 轴朝下，θ 为屏幕上的逆时针角）：

    px = x + lx·cos θ + ly·sin θ
    py = y − lx·sin θ + ly·cos θ

这不是推出来的，是用 KiCad 自带 Python 摆了一片 DIP-14 到 (50,50)、四个角度各
读一遍焊盘绝对坐标量出来的；`tests/test_pcb_footprints.py` 把量出来的数字当
期望值锁住。
"""
from __future__ import annotations

import math
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from hdc.pcb import kicad

#: 覆盖封装库根目录用的环境变量。
FOOTPRINT_ENV = "HDC_KICAD_FOOTPRINTS"

#: 铜层编号：0 = 顶层（F.Cu），1 = 底层（B.Cu）。与 `router` 一致。
FRONT, BACK = 0, 1

#: 外框所在层（用来算元件占地）。
_COURTYARD = {"F.CrtYd", "B.CrtYd"}

#: 生成 uuid 用的名字空间，保证同样输入得到同样文件。
_NS = uuid.UUID("2c7d1b40-8f6a-4e51-9c33-7a5e0d9b1f22")

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


class FootprintError(RuntimeError):
    """封装找不到或读不动。"""


# --- 极小 s-expression 读写（原子保留原文，数值不会掉精度） -------------------

def _parse(text: str) -> list:
    stack: list[list] = []
    current: list = []
    for token in _TOKEN.findall(text):
        if token == "(":
            stack.append(current)
            current = []
        elif token == ")":
            done, current = current, stack.pop()
            current.append(done)
        else:
            current.append(token)
    if not current:
        raise FootprintError("空的 s-expression")
    return current[0]


def _dump(form, indent: str = "\t") -> list[str]:
    """回写成文本。纯原子的表写成一行，含子表的表换行缩进。

    子表统一排到原子后面。KiCad 的解析器对关键字子表不挑顺序，位置相关的原子
    （焊盘号、类型、形状）本来就在最前面，所以这样重排是安全的。
    """
    subs = [child for child in form if isinstance(child, list)]
    if not subs:
        return [indent + "(" + " ".join(form) + ")"]
    atoms = [child for child in form if not isinstance(child, list)]
    out = [indent + "(" + " ".join(atoms)]
    for sub in subs:
        out += _dump(sub, indent + "\t")
    out.append(indent + ")")
    return out


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(atom: str) -> str:
    if atom.startswith('"'):
        return atom[1:-1].encode().decode("unicode_escape")
    return atom


def _kids(form, name: str) -> list[list]:
    return [f for f in form if isinstance(f, list) and f and f[0] == name]


def _one(form, name: str) -> list | None:
    found = _kids(form, name)
    return found[0] if found else None


def _num(atom: str) -> float:
    return float(atom)


def _fmt(value: float) -> str:
    return f"{round(value, 6):g}"


# --- 数据模型 ---------------------------------------------------------------

def place(local: tuple[float, float], at: tuple[float, float],
          rotation: float) -> tuple[float, float]:
    """本地坐标 → 图纸绝对坐标。变换方向由 KiCad 自带 Python 实测锁定。"""
    angle = math.radians(rotation)
    cos, sin = math.cos(angle), math.sin(angle)
    lx, ly = local
    return (at[0] + lx * cos + ly * sin, at[1] - lx * sin + ly * cos)


@dataclass(frozen=True)
class Pad:
    """一个焊盘的本地几何。`radius` 取长短边里的大者，布线避让宁可保守。"""

    number: str
    x: float
    y: float
    radius: float
    drill: float
    layers: tuple[int, ...]


@dataclass(frozen=True)
class Footprint:
    """一个封装：焊盘表 + 占地外框 + 用于重新输出的原始语法树。"""

    lib: str
    name: str
    pads: tuple[Pad, ...]
    bbox: tuple[float, float, float, float]
    tree: list

    @property
    def id(self) -> str:
        return f"{self.lib}:{self.name}"

    def pad_positions(self, at: tuple[float, float],
                      rotation: float) -> dict[str, tuple[float, float]]:
        """摆到 `at` / 转 `rotation` 度之后，每个焊盘的绝对坐标。"""
        return {pad.number: place((pad.x, pad.y), at, rotation)
                for pad in self.pads}

    def placed_bbox(self, at: tuple[float, float],
                    rotation: float) -> tuple[float, float, float, float]:
        """摆放后的占地外框（把四个角转过去再取包围盒）。"""
        x1, y1, x2, y2 = self.bbox
        corners = [place(pt, at, rotation)
                   for pt in ((x1, y1), (x2, y1), (x1, y2), (x2, y2))]
        for pad in self.pads:                 # 焊盘可能探出外框，一并算进去
            px, py = place((pad.x, pad.y), at, rotation)
            corners += [(px - pad.radius, py - pad.radius),
                        (px + pad.radius, py + pad.radius)]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return (min(xs), min(ys), max(xs), max(ys))

    def render(self, *, ref: str, value: str, at: tuple[float, float],
               rotation: float, nets: Mapping[str, tuple[int, str]],
               uid: str, layer: str = "F.Cu") -> list[str]:
        """输出可直接嵌进 `.kicad_pcb` 的 `(footprint ...)` 段。"""
        tree = deepcopy(self.tree)
        tree[1] = _quote(self.id)
        body = [child for child in tree[2:]
                if not (isinstance(child, list)
                        and child[0] in ("version", "generator",
                                         "generator_version", "layer", "at",
                                         "uuid"))]
        head = [["layer", _quote(layer)], ["uuid", _quote(uid)],
                ["at", _fmt(at[0]), _fmt(at[1]), _fmt(rotation)]]
        for prop in [f for f in body if isinstance(f, list) and f[0] == "property"]:
            key = _unquote(prop[1])
            if key == "Reference":
                prop[2] = _quote(ref)
            elif key == "Value":
                prop[2] = _quote(value)
        for pad in [f for f in body if isinstance(f, list) and f[0] == "pad"]:
            number = _unquote(pad[1])
            found = nets.get(number)
            if found and found[1]:
                pad.append(["net", str(found[0]), _quote(found[1])])
            pad.append(["uuid", _quote(str(uuid.uuid5(_NS, f"{uid}|pad|{number}")))])
        return _dump([tree[0], tree[1], *head, *body])


# --- 从语法树里抠出焊盘与占地 -------------------------------------------------

def _copper(layers: list[str], kind: str) -> tuple[int, ...]:
    front = any(name in ("*.Cu", "F.Cu") for name in layers)
    back = any(name in ("*.Cu", "B.Cu") for name in layers)
    if front and back:
        return (FRONT, BACK)
    if back:
        return (BACK,)
    if front:
        return (FRONT,)
    return (FRONT, BACK) if "thru_hole" in kind else (FRONT,)


def _read_pads(tree) -> tuple[Pad, ...]:
    pads = []
    for form in _kids(tree, "pad"):
        at, size, drill = _one(form, "at"), _one(form, "size"), _one(form, "drill")
        layers = [_unquote(a) for a in (_one(form, "layers") or [""])[1:]
                  if not isinstance(a, list)]
        width, height = (_num(size[1]), _num(size[2])) if size else (0.0, 0.0)
        holes = [_num(a) for a in (drill or [])[1:]
                 if not isinstance(a, list) and re.fullmatch(r"-?[\d.]+", a)]
        pads.append(Pad(number=_unquote(form[1]), x=_num(at[1]), y=_num(at[2]),
                        radius=max(width, height) / 2,
                        drill=max(holes) if holes else 0.0,
                        layers=_copper(layers, form[2])))
    return tuple(pads)


def _points(form):
    """递归抓出几何点：(start/end/center/mid/xy x y)。"""
    for child in form:
        if not isinstance(child, list):
            continue
        if child[0] in ("start", "end", "center", "mid", "xy") and len(child) >= 3:
            try:
                yield (_num(child[1]), _num(child[2]))
            except ValueError:
                continue
        else:
            yield from _points(child)


def _read_bbox(tree, pads: tuple[Pad, ...]) -> tuple[float, float, float, float]:
    """占地外框：优先用 courtyard 层的图形，没有就拿焊盘外沿凑。"""
    xs: list[float] = []
    ys: list[float] = []
    for form in tree:
        if not isinstance(form, list):
            continue
        layer = _one(form, "layer")
        if not layer or _unquote(layer[1]) not in _COURTYARD:
            continue
        for x, y in _points(form):
            xs.append(x)
            ys.append(y)
    if not xs:
        for pad in pads:
            xs += [pad.x - pad.radius, pad.x + pad.radius]
            ys += [pad.y - pad.radius, pad.y + pad.radius]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


# --- 库定位与读取 -----------------------------------------------------------

def library_root(env: Mapping[str, str] | None = None) -> Path:
    """封装库根目录（里面是一堆 `*.pretty`）。找不到时报错讲清楚怎么办。"""
    table = os.environ if env is None else env
    override = table.get(FOOTPRINT_ENV)
    if override and Path(override).is_dir():
        return Path(override)
    for key in ("KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR",
                "KICAD8_FOOTPRINT_DIR"):
        value = table.get(key)
        if value and Path(value).is_dir():
            return Path(value)
    cli = kicad.find_cli(env)
    guesses = [Path("/usr/share/kicad/footprints"),
               Path("/usr/local/share/kicad/footprints")]
    if cli:
        guesses.insert(0, cli.parent.parent / "share" / "kicad" / "footprints")
    for candidate in guesses:
        if candidate.is_dir():
            return candidate
    raise FootprintError(
        "找不到 KiCad 封装库。装了 KiCad 但放在别处的话，"
        f"把 {FOOTPRINT_ENV} 指向那个装着 *.pretty 的目录。"
    )


_CACHE: dict[tuple[str, str], Footprint] = {}


def load(footprint_id: str, *, root: Path | None = None) -> Footprint:
    """按 `"库:名字"` 读一个封装。同一个封装只读一次（缓存）。"""
    if ":" not in footprint_id:
        raise FootprintError(f"封装名要写成「库:名字」，收到 {footprint_id!r}")
    lib, name = footprint_id.split(":", 1)
    base = Path(root) if root is not None else library_root()
    key = (footprint_id, str(base))
    if key in _CACHE:
        return _CACHE[key]
    path = base / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not path.is_file():
        raise FootprintError(f"找不到封装 {footprint_id}：{path} 不存在")
    tree = _parse(path.read_text(encoding="utf-8"))
    if tree[0] != "footprint":
        raise FootprintError(f"{path} 不是封装文件（顶层是 {tree[0]}）")
    pads = _read_pads(tree)
    found = Footprint(lib=lib, name=name, pads=pads,
                      bbox=_read_bbox(tree, pads), tree=tree)
    _CACHE[key] = found
    return found
