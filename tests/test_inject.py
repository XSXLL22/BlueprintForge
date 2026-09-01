"""错误注入的单测：每个 bug 都必须成功改动 RTL。"""
import unittest

from hdc import generate, inject
from hdc.spec import from_dict


class TestInject(unittest.TestCase):
    def setUp(self):
        self.s = from_dict({"behavior": {"led_count": 4, "interval_ms": 1}})
        self.rtl = generate.generate_rtl(self.s)

    def test_each_bug_modifies_rtl(self):
        for bug in inject.BUG_TYPES:
            changed = inject.apply(self.rtl, bug)
            self.assertNotEqual(changed, self.rtl, f"{bug} 未改动 RTL")

    def test_wrong_direction_flips_shift(self):
        changed = inject.apply(self.rtl, "wrong_direction")
        self.assertIn("led << 1", changed)
        self.assertNotIn("led >> 1", changed)

    def test_wrong_interval_halves_divider(self):
        changed = inject.apply(self.rtl, "wrong_interval")
        self.assertIn("tick == (DIVIDER / 2) - 1", changed)
        self.assertNotIn("tick == DIVIDER - 1", changed)

    def test_unknown_bug_raises(self):
        with self.assertRaises(ValueError):
            inject.apply(self.rtl, "nonsense")


if __name__ == "__main__":
    unittest.main()
