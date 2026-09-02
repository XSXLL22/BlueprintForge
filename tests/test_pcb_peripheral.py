"""T1.3 板级外围单测：电源、去耦、RC 时钟、复位、LED、输入排针。"""
import unittest

from hdc.pcb import peripheral
from hdc.pcb.pack import GND_NET, VCC_NET, Assembly, ChipInstance, IoPort, PinConn
from hdc.pcb.cells import spec_for


def _counter_assembly() -> Assembly:
    """4 位计数器装箱结果的精简复刻：一片 74HC273 + 一片 74HC283。"""
    reg, add = spec_for("74AC273_8x1DFFR"), spec_for("74AC283_1x1ADD4")
    asm = Assembly(project="counter")
    asm.chips = [
        ChipInstance("U1", reg, 4),
        ChipInstance("U2", add, 1),
    ]
    asm.connections = [
        PinConn("U1", 20, VCC_NET, "VCC"), PinConn("U1", 10, GND_NET, "GND"),
        PinConn("U1", 11, "clk", "CLK"), PinConn("U1", 1, "rst_n", "C"),
        PinConn("U1", 2, "count[0]", "Q", True), PinConn("U1", 5, "count[1]", "Q", True),
        PinConn("U2", 16, VCC_NET, "VCC"), PinConn("U2", 8, GND_NET, "GND"),
        PinConn("U2", 5, "count[0]", "A0"), PinConn("U2", 4, "N9", "S0", True),
    ]
    asm.io = {
        "clk": IoPort("clk", "input", ["clk"]),
        "rst_n": IoPort("rst_n", "input", ["rst_n"]),
        "count": IoPort("count", "output", ["count[0]", "count[1]"]),
    }
    return asm


class TestPowerAndDecoupling(unittest.TestCase):
    def setUp(self):
        self.board = peripheral.build_board(_counter_assembly())

    def test_power_header_carries_vcc_and_gnd(self):
        j = self.board.by_ref("J1")
        self.assertEqual(set(j.pins.values()), {VCC_NET, GND_NET})
        self.assertIn("PinHeader_1x02", j.footprint)

    def test_bulk_capacitor_is_present(self):
        bulk = [c for c in self.board.components if c.kind == "cap_bulk"]
        self.assertEqual(len(bulk), 1)
        self.assertEqual(set(bulk[0].pins.values()), {VCC_NET, GND_NET})

    def test_one_decoupling_cap_per_ic_placed_near_it(self):
        ics = [c.ref for c in self.board.components if c.kind == "ic"]
        caps = [c for c in self.board.components if c.kind == "cap_decoupling"]
        self.assertEqual(len(caps), len(ics))
        self.assertEqual(sorted(c.near for c in caps), sorted(ics))
        for c in caps:
            self.assertEqual(set(c.pins.values()), {VCC_NET, GND_NET})
            self.assertEqual(c.value, "100nF")


class TestClock(unittest.TestCase):
    def setUp(self):
        self.board = peripheral.build_board(_counter_assembly())

    def test_schmitt_inverter_is_added_as_clock_source(self):
        hc14 = [c for c in self.board.components if c.value == "74HC14"]
        self.assertEqual(len(hc14), 1)

    def test_rc_network_wires_oscillator_node(self):
        r = self.board.by_ref("R1")
        c = next(x for x in self.board.components if x.kind == "cap_timing")
        self.assertEqual(set(r.pins.values()), {"CLK_RC", "CLK_OSC"})
        self.assertEqual(set(c.pins.values()), {"CLK_RC", GND_NET})

    def test_clock_select_header_lets_user_pick_rc_or_external(self):
        j = next(x for x in self.board.components if x.ref == "J2")
        self.assertEqual(j.pins, {1: "CLK_SRC", 2: "clk", 3: "CLK_EXT"})
        self.assertIn("PinHeader_1x03", j.footprint)

    def test_unused_schmitt_inputs_are_grounded(self):
        hc14 = next(c for c in self.board.components if c.value == "74HC14")
        for pin in (5, 9, 11, 13):
            self.assertEqual(hc14.pins.get(pin), GND_NET, f"pin {pin} 应接地")

    def test_notes_document_the_oscillator_frequency(self):
        joined = "\n".join(self.board.notes)
        self.assertIn("Hz", joined)
        self.assertIn("R1", joined)

    def test_no_clock_peripheral_for_combinational_design(self):
        asm = _counter_assembly()
        asm.connections = [c for c in asm.connections if c.port != "CLK"]
        board = peripheral.build_board(asm)
        self.assertFalse([c for c in board.components if c.value == "74HC14"])


class TestReset(unittest.TestCase):
    def test_active_low_reset_gets_pullup_and_button(self):
        board = peripheral.build_board(_counter_assembly())
        sw = next(c for c in board.components if c.kind == "switch")
        pull = next(c for c in board.components
                    if c.kind == "res" and "rst_n" in c.pins.values()
                    and VCC_NET in c.pins.values())
        self.assertEqual(set(sw.pins.values()), {"rst_n", GND_NET})
        self.assertEqual(pull.value, "10k")

    def test_reset_note_states_polarity(self):
        board = peripheral.build_board(_counter_assembly())
        self.assertTrue(any("低电平复位" in n for n in board.notes), board.notes)


class TestOutputsAndInputs(unittest.TestCase):
    def test_each_output_bit_drives_an_led_through_a_resistor(self):
        board = peripheral.build_board(_counter_assembly())
        leds = [c for c in board.components if c.kind == "led"]
        self.assertEqual(len(leds), 2)
        for i, led in enumerate(leds):
            anode_net = f"LED{i + 1}_A"
            self.assertEqual(led.pins, {2: anode_net, 1: GND_NET})
            res = next(c for c in board.components
                       if c.kind == "res" and anode_net in c.pins.values())
            self.assertEqual(set(res.pins.values()), {f"count[{i}]", anode_net})
            self.assertEqual(res.value, "1k")

    def test_extra_inputs_get_a_header_and_pulldowns(self):
        asm = _counter_assembly()
        asm.io["sw"] = IoPort("sw", "input", ["sw[0]", "sw[1]"])
        asm.connections.append(PinConn("U2", 6, "sw[0]", "B0"))
        asm.connections.append(PinConn("U2", 2, "sw[1]", "B1"))
        board = peripheral.build_board(asm)
        header = next(c for c in board.components
                      if c.kind == "header" and "sw[0]" in c.pins.values())
        self.assertEqual(header.pins, {1: "sw[0]", 2: "sw[1]"})
        downs = [c for c in board.components if c.kind == "res"
                 and GND_NET in c.pins.values()
                 and any(n.startswith("sw[") for n in c.pins.values())]
        self.assertEqual(len(downs), 2)


class TestBoardIntegrity(unittest.TestCase):
    def setUp(self):
        self.board = peripheral.build_board(_counter_assembly())

    def test_refs_are_unique(self):
        refs = [c.ref for c in self.board.components]
        self.assertEqual(len(refs), len(set(refs)), refs)

    def test_every_component_has_a_footprint_and_kind(self):
        for c in self.board.components:
            self.assertIn(":", c.footprint, c.ref)
            self.assertTrue(c.kind, c.ref)

    def test_nets_include_power_and_signals(self):
        self.assertIn(VCC_NET, self.board.nets)
        self.assertIn(GND_NET, self.board.nets)
        self.assertIn("count[0]", self.board.nets)

    def test_ic_pins_come_straight_from_the_assembly(self):
        u1 = self.board.by_ref("U1")
        self.assertEqual(u1.pins[11], "clk")
        self.assertEqual(u1.pins[20], VCC_NET)
        self.assertEqual(u1.value, "74HC273")

    def test_build_is_deterministic(self):
        again = peripheral.build_board(_counter_assembly())
        self.assertEqual([(c.ref, c.value, c.pins) for c in self.board.components],
                         [(c.ref, c.value, c.pins) for c in again.components])


if __name__ == "__main__":
    unittest.main()
