"""T1.5 布线器单测 —— 纯几何，不依赖 KiCad。

判定标准全部独立于实现：

* **连通性**：把产出的线段/过孔当成图，用并查集算连通分量（含「端点落在
  另一段中间」的 T 型接点），再看同网络的焊盘是否落在同一分量里。
* **无短路**：任意两条不同网络的线段（同层）距离必须 ≥ 间距要求；线段到
  异网焊盘同理。这两件事是板子能不能用的底线。
"""
import math
import unittest

from hdc.pcb.router import Pad, RouteOptions, Segment, Via, route


# --- 独立判定工具 -----------------------------------------------------------

def _seg_points(seg: Segment) -> tuple[tuple[float, float], tuple[float, float]]:
    return (round(seg.x1, 4), round(seg.y1, 4)), (round(seg.x2, 4), round(seg.y2, 4))


class _Union:
    def __init__(self) -> None:
        self.parent: dict[object, object] = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def join(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _on_segment(pt, seg, tol=1e-6) -> bool:
    """点是否落在线段上（含端点）。"""
    (x1, y1), (x2, y2) = _seg_points(seg)
    px, py = pt
    cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    if abs(cross) > tol:
        return False
    return (min(x1, x2) - tol <= px <= max(x1, x2) + tol
            and min(y1, y2) - tol <= py <= max(y1, y2) + tol)


def _components(result, net: str) -> _Union:
    """把某网络的线段与过孔合成连通分量。节点是 (x, y, layer)。"""
    segs = [s for s in result.segments if s.net == net]
    vias = [v for v in result.vias if v.net == net]
    union, nodes = _Union(), []
    for seg in segs:
        a, b = _seg_points(seg)
        union.join((*a, seg.layer), (*b, seg.layer))
        nodes += [(*a, seg.layer), (*b, seg.layer)]
    for via in vias:
        key = (round(via.x, 4), round(via.y, 4))
        for layer in range(2):
            nodes.append((*key, layer))
        union.join((*key, 0), (*key, 1))
    for node in nodes:                      # T 型接点：端点落在别的线段中间
        for seg in segs:
            if seg.layer == node[2] and _on_segment(node[:2], seg):
                union.join(node, (*_seg_points(seg)[0], seg.layer))
    return union


def _net_is_connected(result, pads: list[Pad], net: str) -> bool:
    union = _components(result, net)
    roots = set()
    for pad in [p for p in pads if p.net == net]:
        found = {union.find((round(pad.x, 4), round(pad.y, 4), layer))
                 for layer in pad.layers
                 if (round(pad.x, 4), round(pad.y, 4), layer) in union.parent}
        if not found:
            return False
        roots |= found
    return len({union.find(r) for r in roots}) == 1


def _seg_distance(a: Segment, b: Segment) -> float:
    (ax1, ay1), (ax2, ay2) = _seg_points(a)
    (bx1, by1), (bx2, by2) = _seg_points(b)
    return _segment_segment((ax1, ay1), (ax2, ay2), (bx1, by1), (bx2, by2))


def _point_segment(p, a, b) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _segment_segment(a1, a2, b1, b2) -> float:
    d1 = (a2[0] - a1[0], a2[1] - a1[1])
    d2 = (b2[0] - b1[0], b2[1] - b1[1])
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if denom != 0:                          # 不平行：先看是否真的相交
        s = ((b1[0] - a1[0]) * d2[1] - (b1[1] - a1[1]) * d2[0]) / denom
        t = ((b1[0] - a1[0]) * d1[1] - (b1[1] - a1[1]) * d1[0]) / denom
        if 0 <= s <= 1 and 0 <= t <= 1:
            return 0.0
    return min(_point_segment(a1, b1, b2), _point_segment(a2, b1, b2),
               _point_segment(b1, a1, a2), _point_segment(b2, a1, a2))


def _min_foreign_gap(result, pads: list[Pad], opts: RouteOptions) -> float:
    """不同网络之间最近的铜距离（线-线、线-焊盘）。"""
    worst = math.inf
    segs = result.segments
    for i, a in enumerate(segs):
        for b in segs[i + 1:]:
            if a.net == b.net or a.layer != b.layer:
                continue
            worst = min(worst, _seg_distance(a, b) - opts.track_width)
    for seg in segs:
        (x1, y1), (x2, y2) = _seg_points(seg)
        for pad in pads:
            if pad.net == seg.net or seg.layer not in pad.layers:
                continue
            gap = _point_segment((pad.x, pad.y), (x1, y1), (x2, y2))
            worst = min(worst, gap - pad.radius - opts.track_width / 2)
    return worst


def _dip(net_of: dict[int, str], x0=0.0, y0=0.0, count=14) -> list[Pad]:
    """一片 DIP 的焊盘：1 号脚在 (x0, y0)，左列向下、右列向上，行距 7.62。"""
    pads = []
    for pin, net in net_of.items():
        half = count // 2
        if pin <= half:
            pads.append(Pad(net, x0, y0 + (pin - 1) * 2.54))
        else:
            pads.append(Pad(net, x0 + 7.62, y0 + (count - pin) * 2.54))
    return pads
class TestBasicRouting(unittest.TestCase):
    def test_two_pads_on_one_net_get_connected(self):
        pads = [Pad("a", 0, 0), Pad("a", 12.7, 0)]
        result = route(pads)
        self.assertEqual(result.unrouted, [])
        self.assertTrue(_net_is_connected(result, pads, "a"))

    def test_straight_run_uses_a_single_segment(self):
        result = route([Pad("a", 0, 0), Pad("a", 12.7, 0)])
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(len(result.vias), 0)

    def test_segments_are_orthogonal_and_on_grid(self):
        pads = [Pad("a", 0, 0), Pad("a", 12.7, 7.62), Pad("b", 5.08, 15.24),
                Pad("b", 20.32, 2.54)]
        opts = RouteOptions()
        for seg in route(pads, opts).segments:
            self.assertTrue(seg.x1 == seg.x2 or seg.y1 == seg.y2,
                            f"斜线段 {seg}")
            for value in (seg.x1, seg.y1, seg.x2, seg.y2):
                self.assertAlmostEqual(value / opts.grid,
                                       round(value / opts.grid), places=6)

    def test_zero_length_segments_are_not_emitted(self):
        pads = [Pad("a", 0, 0), Pad("a", 12.7, 7.62)]
        for seg in route(pads).segments:
            self.assertNotEqual(_seg_points(seg)[0], _seg_points(seg)[1])

    def test_single_pad_net_needs_no_track(self):
        result = route([Pad("a", 0, 0)])
        self.assertEqual(result.segments, [])
        self.assertEqual(result.unrouted, [])

    def test_skipped_nets_are_left_to_the_copper_zone(self):
        pads = [Pad("GND", 0, 0), Pad("GND", 25.4, 0), Pad("a", 0, 5.08),
                Pad("a", 25.4, 5.08)]
        result = route(pads, RouteOptions(skip_nets=frozenset({"GND"})))
        self.assertFalse([s for s in result.segments if s.net == "GND"])
        self.assertTrue(_net_is_connected(result, pads, "a"))
        self.assertEqual(result.unrouted, [])

    def test_routing_is_deterministic(self):
        pads = _dip({1: "a", 7: "b", 8: "b", 14: "a"}) + [Pad("a", 25.4, 0),
                                                          Pad("b", 25.4, 7.62)]
        first, second = route(pads), route(pads)
        self.assertEqual(first.segments, second.segments)
        self.assertEqual(first.vias, second.vias)


class TestMultiTerminalNets(unittest.TestCase):
    def test_three_pads_end_up_in_one_connected_tree(self):
        pads = [Pad("a", 0, 0), Pad("a", 25.4, 0), Pad("a", 12.7, 20.32)]
        result = route(pads)
        self.assertEqual(result.unrouted, [])
        self.assertTrue(_net_is_connected(result, pads, "a"))

    def test_a_wide_fanout_net_still_connects(self):
        pads = [Pad("clk", 0, 0)] + [Pad("clk", 12.7 * i, 15.24) for i in range(1, 6)]
        result = route(pads)
        self.assertEqual(result.unrouted, [])
        self.assertTrue(_net_is_connected(result, pads, "clk"))

    def test_branching_reuses_existing_copper_instead_of_re_routing(self):
        """第三个焊盘应接到已有的树上，而不是再从头拉一条平行线。"""
        far = [Pad("a", 0, 0), Pad("a", 50.8, 0), Pad("a", 50.8, 2.54)]
        length = sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1)
                     for s in route(far).segments)
        self.assertLess(length, 50.8 * 2, "分支没有复用主干铜箔")


class TestObstaclesAndLayers(unittest.TestCase):
    def test_foreign_pads_force_a_detour(self):
        """两焊盘之间横着一道异网焊盘墙，直线走不通，只能绕（贯孔焊盘两层都挡）。"""
        pads = [Pad("a", 0, 0), Pad("a", 25.4, 0)]
        pads += [Pad("b", 12.7, y * 1.27) for y in range(-6, 7)]
        result = route(pads)
        self.assertEqual(result.unrouted, [])
        self.assertTrue(_net_is_connected(result, pads, "a"))
        straight = 25.4
        length = sum(abs(s.x2 - s.x1) + abs(s.y2 - s.y1)
                     for s in result.segments if s.net == "a")
        self.assertGreater(length, straight, "本该绕行却走了直线")

    def test_completely_walled_in_pad_is_reported_not_silently_dropped(self):
        pads = [Pad("a", 0, 0), Pad("a", 25.4, 0)]
        for x in (-1.27, 0, 1.27):          # 把 a 的第一个焊盘四面围住
            for y in (-1.27, 0, 1.27):
                if (x, y) != (0.0, 0.0):
                    pads.append(Pad("b", x, y, radius=0.4))
        result = route(pads)
        self.assertIn("a", result.unrouted)
        self.assertFalse([s for s in result.segments if s.net == "a"])

    def test_single_layer_board_never_emits_a_via(self):
        pads = [Pad("a", 0, 0, layers=(0,)), Pad("a", 25.4, 0, layers=(0,))]
        result = route(pads, RouteOptions(layers=1))
        self.assertEqual(result.vias, [])
        self.assertTrue(all(s.layer == 0 for s in result.segments))

    def test_a_via_is_used_when_the_top_layer_is_blocked(self):
        """顶层被一道异网铜墙封死时，唯一出路是打过孔到底层。"""
        pads = [Pad("a", 0, 0), Pad("a", 25.4, 0)]
        wall = [Pad("b", 12.7, y * 1.27, radius=0.5, layers=(0,))
                for y in range(-16, 17)]
        result = route(pads + wall)
        self.assertEqual(result.unrouted, [])
        self.assertTrue(_net_is_connected(result, pads + wall, "a"))
        self.assertTrue(result.vias, "顶层封死却没打过孔")


class TestNoShorts(unittest.TestCase):
    def test_dense_two_chip_board_has_no_clearance_violation(self):
        nets = {1: "n1", 2: "n2", 3: "n3", 4: "n4", 5: "n5", 6: "n6",
                7: "GND", 8: "n1", 9: "n2", 10: "n3", 11: "n4", 12: "n5",
                13: "n6", 14: "VCC"}
        pads = _dip(nets) + _dip({p: n for p, n in nets.items()}, x0=25.4)
        opts = RouteOptions()
        result = route(pads, opts)
        gap = _min_foreign_gap(result, pads, opts)
        self.assertGreaterEqual(gap, opts.clearance - 1e-9,
                                f"最小异网间距 {gap:.3f}mm < {opts.clearance}mm")

    def test_two_nets_never_share_a_grid_cell(self):
        pads = [Pad("a", 0, 0), Pad("a", 25.4, 12.7),
                Pad("b", 0, 12.7), Pad("b", 25.4, 0)]
        result = route(pads)
        self.assertEqual(result.unrouted, [])
        gap = _min_foreign_gap(result, pads, RouteOptions())
        self.assertGreater(gap, 0, "两个网络的铜箔重叠了")


class TestOffGridPads(unittest.TestCase):
    """电容/按键的引脚间距（2.5mm、5.0mm）不是 1.27 的整数倍。"""

    def test_off_grid_pad_gets_a_stub_to_the_nearest_grid_point(self):
        pads = [Pad("a", 0.0, 0.0), Pad("a", 20.0, 0.0)]
        result = route(pads)
        self.assertEqual(result.unrouted, [])
        self.assertTrue(_net_is_connected(result, pads, "a"))

    def test_off_grid_pads_keep_their_distance_from_foreign_copper(self):
        pads = [Pad("a", 0.0, 0.0), Pad("a", 20.0, 0.0),
                Pad("b", 10.0, 2.5), Pad("b", 10.0, 20.0)]
        opts = RouteOptions()
        result = route(pads, opts)
        self.assertEqual(result.unrouted, [])
        gap = _min_foreign_gap(result, pads, opts)
        self.assertGreaterEqual(gap, opts.clearance - 1e-9, f"间距只有 {gap:.3f}mm")


if __name__ == "__main__":
    unittest.main()
