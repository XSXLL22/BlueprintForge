"""T1.2 装箱单测：门级 cell → 74HC 芯片 + 引脚级连接表。

测试直接手搓 `Netlist74`（不依赖 yosys），因为装箱是纯函数。
"""
import unittest
from collections import Counter

from hdc.pcb import pack
from hdc.pcb.synth74 import Cell, Netlist74, Port


def _nl(cells, ports=(), net_names=None) -> Netlist74:
    return Netlist74(
        project="t", cells=list(cells), ports=list(ports),
        net_names=dict(net_names or {}),
    )


def _nand(name, a, b, y) -> Cell:
    return Cell(name=name, type="74AC00_4x1NAND2",
                connections={"A": [a], "B": [b], "Y": [y]})


def _dffr(name, clk, rst, d, q) -> Cell:
    return Cell(name=name, type="74AC273_8x1DFFR",
                connections={"CLK": [clk], "C": [rst], "D": [d], "Q": [q]})


class TestSlotPacking(unittest.TestCase):
    def test_four_nand_gates_fit_in_one_chip(self):
        cells = [_nand(f"g{i}", f"{10+i}", f"{20+i}", f"{30+i}") for i in range(4)]
        asm = pack.pack(_nl(cells))
        self.assertEqual(len(asm.chips), 1)
        self.assertEqual(asm.chips[0].spec.part, "74HC00")
        self.assertEqual(asm.chips[0].used_slots, 4)

    def test_fifth_gate_spills_into_a_second_chip(self):
        cells = [_nand(f"g{i}", f"{10+i}", f"{20+i}", f"{30+i}") for i in range(5)]
        asm = pack.pack(_nl(cells))
        self.assertEqual(len(asm.chips), 2)
        self.assertEqual([c.used_slots for c in asm.chips], [4, 1])
        self.assertEqual(asm.bom, {"74HC00": 2})

    def test_refs_are_unique_and_sequential(self):
        cells = [_nand(f"g{i}", f"{10+i}", f"{20+i}", f"{30+i}") for i in range(9)]
        asm = pack.pack(_nl(cells))
        self.assertEqual([c.ref for c in asm.chips], ["U1", "U2", "U3"])


class TestSharedPinGrouping(unittest.TestCase):
    def test_registers_with_same_clock_and_reset_share_one_chip(self):
        cells = [_dffr(f"ff{i}", "2", "3", f"{10+i}", f"{20+i}") for i in range(4)]
        asm = pack.pack(_nl(cells))
        self.assertEqual(len(asm.chips), 1)
        self.assertEqual(asm.chips[0].spec.part, "74HC273")
        self.assertEqual(asm.chips[0].used_slots, 4)

    def test_registers_with_different_clocks_get_separate_chips(self):
        cells = [_dffr("a", "2", "3", "10", "20"), _dffr("b", "4", "3", "11", "21")]
        asm = pack.pack(_nl(cells))
        self.assertEqual(len(asm.chips), 2)

    def test_shared_pin_is_wired_once_per_chip(self):
        cells = [_dffr(f"ff{i}", "2", "3", f"{10+i}", f"{20+i}") for i in range(4)]
        asm = pack.pack(_nl(cells, net_names={"2": "clk", "3": "rst_n"}))
        clk_pins = [c for c in asm.connections if c.net == "clk"]
        self.assertEqual(len(clk_pins), 1)
        self.assertEqual(clk_pins[0].pin, 11)


class TestConstantsAndAliases(unittest.TestCase):
    def test_constant_bits_map_to_power_nets(self):
        cells = [_nand("g", "0", "1", "30")]
        asm = pack.pack(_nl(cells))
        by_pin = {c.pin: c.net for c in asm.connections if c.ref == "U1"}
        self.assertEqual(by_pin[1], "GND")
        self.assertEqual(by_pin[2], "VCC")

    def test_buffer_cells_merge_their_two_nets(self):
        cells = [
            Cell(name="buf", type="$_BUF_", connections={"A": ["10"], "Y": ["11"]}),
            _nand("g", "11", "12", "13"),
        ]
        asm = pack.pack(_nl(cells, net_names={"10": "src"}))
        # 缓冲器不占芯片，且 g 的 A 端直接落在合并后的网络上
        self.assertEqual(len(asm.chips), 1)
        a_net = next(c.net for c in asm.connections if c.ref == "U1" and c.pin == 1)
        self.assertEqual(a_net, "src")

    def test_dont_care_input_bits_are_tied_to_gnd(self):
        """x/z 位是「任意值」，接地既满足逻辑又避免 CMOS 悬空输入。"""
        asm = pack.pack(_nl([_nand("g", "10", "x", "13")]))
        pins = {c.pin: c.net for c in asm.connections if c.ref == "U1"}
        self.assertEqual(pins[2], "GND")


class TestPowerAndUnusedPins(unittest.TestCase):
    def test_every_chip_gets_vcc_and_gnd(self):
        cells = [_nand("g", "10", "11", "12"), _dffr("ff", "2", "3", "20", "21")]
        asm = pack.pack(_nl(cells))
        for chip in asm.chips:
            pins = {c.pin: c.net for c in asm.connections if c.ref == chip.ref}
            self.assertEqual(pins[chip.spec.vcc], "VCC")
            self.assertEqual(pins[chip.spec.gnd], "GND")

    def test_unused_slot_inputs_are_tied_to_gnd(self):
        """CMOS 悬空输入会震荡/漏电，未用槽位的输入必须接地；输出保持悬空。"""
        asm = pack.pack(_nl([_nand("g", "10", "11", "12")]))
        pins = {c.pin: c.net for c in asm.connections if c.ref == "U1"}
        for pin in (4, 5, 9, 10, 12, 13):     # 其余三个门的 A/B
            self.assertEqual(pins.get(pin), "GND", f"pin {pin} 应接地")
        for pin in (6, 8, 11):                # 其余三个门的 Y
            self.assertNotIn(pin, pins, f"pin {pin} 是输出，不应接网络")

    def test_tie_pins_from_spec_are_wired(self):
        cells = [Cell(name="cnt", type="74AC161_1x1COUNT4", connections={
            "A": ["0", "0", "0", "0"], "Q": ["10", "11", "12", "13"],
            "CLK": ["2"], "LOAD": ["1"], "ENT": ["1"], "RCO": ["14"],
        })]
        asm = pack.pack(_nl(cells))
        pins = {c.pin: c.net for c in asm.connections if c.ref == "U1"}
        self.assertEqual(pins[1], "VCC")   # ~MR
        self.assertEqual(pins[7], "VCC")   # CEP

    def test_no_pin_is_wired_twice(self):
        cells = [_nand(f"g{i}", f"{10+i}", f"{20+i}", f"{30+i}") for i in range(7)]
        asm = pack.pack(_nl(cells))
        seen = Counter((c.ref, c.pin) for c in asm.connections)
        self.assertFalse([k for k, v in seen.items() if v > 1], seen)


class TestBusPorts(unittest.TestCase):
    def test_bus_bits_map_to_indexed_slot_pins(self):
        cells = [Cell(name="add", type="74AC283_1x1ADD4", connections={
            "A": ["10", "11", "12", "13"], "B": ["1", "0", "0", "0"],
            "CI": ["0"], "S": ["20", "21", "22", "23"], "CO": ["24"],
        })]
        asm = pack.pack(_nl(cells))
        pins = {c.pin: c.net for c in asm.connections if c.ref == "U1"}
        self.assertEqual(pins[5], "N10")    # A0
        self.assertEqual(pins[12], "N13")   # A3
        self.assertEqual(pins[6], "VCC")    # B0 = 常量 1
        self.assertEqual(pins[4], "N20")    # S0
        self.assertEqual(pins[9], "N24")    # CO


class TestIoAndChecks(unittest.TestCase):
    def test_top_level_ports_are_recorded_with_nets(self):
        nl = _nl(
            [_dffr("ff", "2", "3", "10", "4")],
            ports=[Port("clk", "input", ["2"]), Port("rst_n", "input", ["3"]),
                   Port("q", "output", ["4"])],
            net_names={"2": "clk", "3": "rst_n", "4": "q"},
        )
        asm = pack.pack(nl)
        self.assertEqual(asm.io["clk"].direction, "input")
        self.assertEqual(asm.io["clk"].nets, ["clk"])
        self.assertEqual(asm.io["q"].nets, ["q"])

    def test_multiple_drivers_on_one_net_is_reported(self):
        cells = [_nand("g1", "10", "11", "99"), _nand("g2", "12", "13", "99")]
        asm = pack.pack(_nl(cells))
        self.assertTrue(any("多驱动" in w for w in asm.warnings), asm.warnings)

    def test_unmapped_cell_raises(self):
        from hdc.pcb.cells import UnmappedCellError

        with self.assertRaises(UnmappedCellError):
            pack.pack(_nl([Cell(name="x", type="$_NAND_", connections={})]))


if __name__ == "__main__":
    unittest.main()
