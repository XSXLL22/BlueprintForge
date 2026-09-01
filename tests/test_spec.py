"""Spec 加载、校验与派生参数的单测。"""
import unittest

from hdc.spec import SpecError, from_dict, load


class TestLoadDefaults(unittest.TestCase):
    def test_minimal_dict_uses_defaults(self):
        s = from_dict({})
        self.assertEqual(s.project, "led_chaser")
        self.assertEqual(s.led_count, 4)
        self.assertEqual(s.freq_mhz, 50)
        self.assertEqual(s.interval_ms, 500)
        self.assertTrue(s.wrap)
        self.assertTrue(s.enable_port)
        self.assertEqual(s.divider, 25_000_000)
        self.assertEqual(s.reset_port, "rst_n")

    def test_load_fast_file(self):
        s = load("specs/led_chaser_fast.json")
        self.assertEqual(s.divider, 50_000)
        self.assertEqual(s.tick_msb, 15)


class TestValidation(unittest.TestCase):
    def test_bad_project_identifier(self):
        with self.assertRaises(SpecError):
            from_dict({"project": "1bad_name"})

    def test_led_count_too_small(self):
        with self.assertRaises(SpecError):
            from_dict({"behavior": {"led_count": 1}})

    def test_bad_direction(self):
        with self.assertRaises(SpecError):
            from_dict({"behavior": {"direction": "upwards"}})

    def test_bad_reset(self):
        with self.assertRaises(SpecError):
            from_dict({"clock": {"reset": "sync_low"}})

    def test_divider_out_of_range(self):
        with self.assertRaises(SpecError):
            from_dict({"clock": {"freq_mhz": 1000}, "behavior": {"interval_ms": 1e6}})


class TestDerived(unittest.TestCase):
    def test_reset_pattern_ltr(self):
        s = from_dict({"behavior": {"led_count": 4, "direction": "left_to_right"}})
        self.assertEqual(s.reset_pattern, "1000")
        self.assertEqual(s.end_pattern, "0001")
        self.assertEqual([s.literal(v) for v in s.expected_sequence()],
                         ["4'b0100", "4'b0010", "4'b0001", "4'b1000"])

    def test_reset_pattern_rtl(self):
        s = from_dict({"behavior": {"led_count": 4, "direction": "right_to_left"}})
        self.assertEqual(s.reset_pattern, "0001")
        self.assertEqual(s.end_pattern, "1000")
        self.assertEqual([s.literal(v) for v in s.expected_sequence()],
                         ["4'b0010", "4'b0100", "4'b1000", "4'b0001"])

    def test_no_wrap_sequence(self):
        s = from_dict({"behavior": {"led_count": 4, "wrap": False}})
        self.assertEqual([s.literal(v) for v in s.expected_sequence()],
                         ["4'b0100", "4'b0010", "4'b0001"])

    def test_async_active_high(self):
        s = from_dict({"clock": {"reset": "async_active_high"}})
        self.assertEqual(s.reset_port, "rst")
        self.assertEqual(s.reset_active, "1'b1")
        self.assertEqual(s.reset_sensitivity, " or posedge rst")


if __name__ == "__main__":
    unittest.main()
