"""T1.5（其一）—— 网格迷宫布线器。纯几何，不认识 KiCad。

任务清单原话是「曼哈顿走线」。真做下来发现不够用：74HC 板上一条时钟网络要
扇出到五六片芯片，直接拉 L 形折线必然穿过别的焊盘，DRC 一片红。所以这里换成
**格点 A\\* 迷宫布线**，代价不高（板子只有几千个格点），换来的是「不撞焊盘、
不撞别人的线」这个硬保证。

## 模型

* 格点 1.27mm（DIP 引脚间距 2.54 的一半）。相邻两个 DIP 引脚之间正好有一个
  格点，线从中间穿过时到两侧焊盘各余 0.345mm —— 够宽，嘉立创 2 层板最小间距
  是 0.127mm。
* 只走横竖，不走斜线。斜线的间距校验麻烦得多，而且横竖走线看起来就像手工板。
* 每个焊盘按「焊盘半径 + 间距 + 半线宽」膨胀成一片**禁区**，禁区内的格点只有
  该焊盘自己的网络能用。两个网络同时想要一个格点 → 谁都不能用。
* 过孔比走线粗，所以另算一张按过孔半径膨胀的禁区图，只在换层时查。
* 引脚间距不是 1.27 整数倍的元件（电容 5.0mm、按键），焊盘吸附到最近格点，
  再补一小段引出线；吸附距离会加进禁区半径，所以这段引出线也不会撞到别人。

## 策略

网络按外框周长从小到大依次布（小网络先占，经典顺序），多引脚网络用「多源
A\\*」逐个把剩下的焊盘接到已成形的铜箔上 —— 分支天然复用主干，不会拉出平行线。
布不通的网络**如实记进 `unrouted`**，不悄悄丢掉：下游会把它交给 DRC 报出来。
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

#: 禁区冲突标记：两个网络都想要这个格点，于是谁都不能用。
_CONFLICT = "\x00"

#: 四个走线方向（dx, dy）。
_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Pad:
    """一个焊盘。`layers` 是它出现在哪几层（插件式贯穿两层，贴片只有一层）。"""

    net: str
    x: float
    y: float
    radius: float = 0.8
    layers: tuple[int, ...] = (0, 1)


@dataclass(frozen=True)
class RouteOptions:
    """布线参数。默认值按嘉立创 2 层板工艺留了很大余量。"""

    grid: float = 1.27
    track_width: float = 0.25
    clearance: float = 0.2
    via_diameter: float = 0.8
    via_drill: float = 0.4
    layers: int = 2
    #: 换层代价（单位：格）。太小会到处打孔，把底层地平面切碎。
    via_cost: float = 12.0
    #: 拐弯代价（单位：格）。只影响美观，不影响正确性。
    bend_cost: float = 1.0
    #: 由铺铜覆盖、不必走线的网络（一般是 GND）。
    skip_nets: frozenset[str] = frozenset()
    #: 搜索范围在焊盘外框之外再放宽多少格。
    margin_cells: int = 10


@dataclass(frozen=True)
class Segment:
    """一段走线。端点是绝对坐标（mm）。"""

    net: str
    layer: int
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class Via:
    net: str
    x: float
    y: float


@dataclass
class RouteResult:
    segments: list[Segment] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    #: 没能布通的网络名（升序）。
    unrouted: list[str] = field(default_factory=list)


# --- 格点与禁区 -------------------------------------------------------------

class _Grid:
    """格点坐标换算 + 两张禁区图（走线用 / 过孔用）+ 已占用格点。"""

    def __init__(self, pads: list[Pad], opts: RouteOptions,
                 bounds: tuple[float, float, float, float] | None) -> None:
        self.opts = opts
        self.track: dict[tuple[int, int, int], str] = {}
        self.via: dict[tuple[int, int, int], str] = {}
        self.used: dict[tuple[int, int, int], str] = {}
        self.access: dict[int, tuple[int, int]] = {}
        self.stub: dict[int, float] = {}

        for index, pad in enumerate(pads):
            cell = (self.snap(pad.x), self.snap(pad.y))
            self.access[index] = cell
            self.stub[index] = math.dist((pad.x, pad.y),
                                         (self.mm(cell[0]), self.mm(cell[1])))
        for index, pad in enumerate(pads):
            slack = self.stub[index] + opts.clearance
            self._claim(self.track, pad, pad.radius + slack + opts.track_width / 2)
            self._claim(self.via, pad, pad.radius + slack + opts.via_diameter / 2)

        xs = [p.x for p in pads] or [0.0]
        ys = [p.y for p in pads] or [0.0]
        if bounds:
            x1, y1, x2, y2 = bounds
        else:
            pad_margin = max(p.radius for p in pads) if pads else 0.0
            reach = opts.margin_cells * opts.grid + pad_margin
            x1, y1 = min(xs) - reach, min(ys) - reach
            x2, y2 = max(xs) + reach, max(ys) + reach
        self.lo = (math.ceil(x1 / opts.grid), math.ceil(y1 / opts.grid))
        self.hi = (math.floor(x2 / opts.grid), math.floor(y2 / opts.grid))

    def snap(self, value: float) -> int:
        return int(round(value / self.opts.grid))

    def mm(self, cell: int) -> float:
        return round(cell * self.opts.grid, 6)

    def _claim(self, table, pad: Pad, keep: float) -> None:
        span = int(keep / self.opts.grid) + 1
        cx, cy = self.snap(pad.x), self.snap(pad.y)
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                point = (self.mm(cx + dx), self.mm(cy + dy))
                if math.dist(point, (pad.x, pad.y)) > keep + 1e-9:
                    continue
                for layer in pad.layers:
                    key = (layer, cx + dx, cy + dy)
                    owner = table.get(key)
                    if owner is None:
                        table[key] = pad.net
                    elif owner != pad.net:
                        table[key] = _CONFLICT

    def inside(self, ix: int, iy: int) -> bool:
        return self.lo[0] <= ix <= self.hi[0] and self.lo[1] <= iy <= self.hi[1]

    def open_for(self, net: str, layer: int, ix: int, iy: int,
                 *, as_via: bool = False) -> bool:
        if not self.inside(ix, iy):
            return False
        key = (layer, ix, iy)
        if self.used.get(key, net) != net:
            return False
        if self.track.get(key, net) != net:
            return False
        return not (as_via and self.via.get(key, net) != net)

    def take(self, net: str, layer: int, ix: int, iy: int) -> None:
        self.used[(layer, ix, iy)] = net


# --- A\* 搜索 ---------------------------------------------------------------

def _pad_layers(pad: Pad, opts: RouteOptions) -> tuple[int, ...]:
    return tuple(layer for layer in pad.layers if layer < opts.layers)


def _estimate(node: tuple[int, int, int], targets, grid: float) -> float:
    _, ix, iy = node
    return grid * min(abs(ix - tx) + abs(iy - ty) for _, tx, ty in targets)


def _neighbours(grid: _Grid, net: str, state, opts: RouteOptions):
    layer, ix, iy, heading = state
    out = []
    for index, (dx, dy) in enumerate(_STEPS):
        if grid.open_for(net, layer, ix + dx, iy + dy):
            cost = opts.grid
            if heading != len(_STEPS) and index != heading:
                cost += opts.bend_cost * opts.grid
            out.append(((layer, ix + dx, iy + dy, index), cost))
    if opts.layers > 1 and grid.open_for(net, layer, ix, iy, as_via=True):
        for other in range(opts.layers):
            if other != layer and grid.open_for(net, other, ix, iy, as_via=True):
                out.append(((other, ix, iy, len(_STEPS)), opts.via_cost * opts.grid))
    return out


def _astar(grid: _Grid, net: str, sources, targets, opts: RouteOptions):
    """多源多目标 A\\*。返回 [(层, ix, iy), ...]，走不通返回 None。"""
    if not targets:
        return None
    goals = set(targets)
    heap, best, came, closed, tick = [], {}, {}, set(), 0
    for source in sorted(sources):
        state = (*source, len(_STEPS))
        best[state] = 0.0
        heapq.heappush(heap, (_estimate(source, targets, opts.grid), 0.0,
                              tick, state, None))
        tick += 1
    while heap:
        _, cost, _, state, parent = heapq.heappop(heap)
        if state in closed:
            continue
        closed.add(state)
        came[state] = parent
        if state[:3] in goals:
            path = []
            while state is not None:
                path.append(state[:3])
                state = came[state]
            return path[::-1]
        for nxt, step in _neighbours(grid, net, state, opts):
            if nxt in closed:
                continue
            walked = cost + step
            if walked < best.get(nxt, math.inf) - 1e-12:
                best[nxt] = walked
                heapq.heappush(heap, (walked + _estimate(nxt[:3], targets, opts.grid),
                                      walked, tick, nxt, state))
                tick += 1
    return None


# --- 把路径变成线段与过孔 -----------------------------------------------------

def _commit(grid: _Grid, net: str, path, result: RouteResult) -> None:
    """路径 → 线段 + 过孔。同层同方向的连续格点合并成一段。"""
    for layer, ix, iy in path:
        grid.take(net, layer, ix, iy)

    runs = [[path[0]]]
    for prev, node in zip(path, path[1:]):
        if node[0] != prev[0]:
            result.vias.append(Via(net, grid.mm(prev[1]), grid.mm(prev[2])))
            runs.append([node])
        else:
            runs[-1].append(node)

    def emit(a, b) -> None:
        if a[1:] != b[1:]:
            result.segments.append(Segment(net, a[0], grid.mm(a[1]), grid.mm(a[2]),
                                           grid.mm(b[1]), grid.mm(b[2])))

    for run in runs:
        start = 0
        for i in range(1, len(run) - 1):
            before = (run[i][1] - run[i - 1][1], run[i][2] - run[i - 1][2])
            after = (run[i + 1][1] - run[i][1], run[i + 1][2] - run[i][2])
            if before != after:
                emit(run[start], run[i])
                start = i
        if len(run) > 1:
            emit(run[start], run[-1])


def _route_net(grid: _Grid, net: str, ids: list[int], pads: list[Pad],
               opts: RouteOptions, result: RouteResult) -> bool:
    """把一个网络的所有焊盘接成一棵树。有焊盘接不上就返回 False。"""
    order = sorted(ids, key=lambda i: (pads[i].x, pads[i].y, i))
    first = order[0]
    seeds = [layer for layer in _pad_layers(pads[first], opts)
             if grid.open_for(net, layer, *grid.access[first])]
    if not seeds:
        return False
    tree = {(seeds[0], *grid.access[first])}
    grid.take(net, seeds[0], *grid.access[first])

    def touched(index: int) -> bool:
        return any((layer, *grid.access[index]) in tree
                   for layer in _pad_layers(pads[index], opts))

    remaining = [i for i in order[1:] if not touched(i)]
    while remaining:
        targets = [(layer, *grid.access[i]) for i in remaining
                   for layer in _pad_layers(pads[i], opts)
                   if grid.open_for(net, layer, *grid.access[i])]
        path = _astar(grid, net, tree, targets, opts) if targets else None
        if path is None:
            return False
        _commit(grid, net, path, result)
        tree.update(path)
        remaining = [i for i in remaining if not touched(i)]
    return True


def _add_stubs(grid: _Grid, pads: list[Pad], opts: RouteOptions,
               result: RouteResult) -> None:
    """引脚间距非 1.27 整数倍的焊盘：补一段从焊盘中心到格点的引出线。"""
    for index, pad in enumerate(pads):
        if grid.stub[index] < 1e-9:
            continue
        cell = grid.access[index]
        for layer in _pad_layers(pad, opts):
            if grid.used.get((layer, *cell)) == pad.net:
                result.segments.append(Segment(
                    pad.net, layer, pad.x, pad.y, grid.mm(cell[0]), grid.mm(cell[1])))


# --- 对外接口 ---------------------------------------------------------------

def route(pads, options: RouteOptions | None = None, *,
          bounds: tuple[float, float, float, float] | None = None) -> RouteResult:
    """给一堆焊盘布线。同样输入必得同样输出。

    `bounds` 限定搜索范围（一般传板框），不给就按焊盘外框放宽 `margin_cells` 格。
    """
    opts = options or RouteOptions()
    pads = list(pads)
    result = RouteResult()
    if not pads:
        return result

    grid = _Grid(pads, opts, bounds)
    by_net: dict[str, list[int]] = {}
    for index, pad in enumerate(pads):
        by_net.setdefault(pad.net, []).append(index)

    def spread(ids: list[int]) -> float:
        xs = [pads[i].x for i in ids]
        ys = [pads[i].y for i in ids]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    todo = sorted(((name, ids) for name, ids in by_net.items()
                   if len(ids) > 1 and name not in opts.skip_nets),
                  key=lambda item: (spread(item[1]), item[0]))

    failed = [name for name, ids in todo
              if not _route_net(grid, name, ids, pads, opts, result)]
    _add_stubs(grid, pads, opts, result)
    result.unrouted = sorted(failed)
    return result
