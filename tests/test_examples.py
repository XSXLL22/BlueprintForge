"""`examples/` 目录的契约单测。

示例现在是**测试的事实来源**（见 `tests/examples.py`）。一旦有人改坏了示例，
应该在这里报一条能读懂的错，而不是让下游十几个测试一起红成一片看不出原因。

只查那些「改坏了下游一定崩」的事实：文件在不在、顶层模块叫什么、自检 TB 的
两个标记字符串还在不在。不查具体逻辑 —— 那是 `test_design.py`（仿真）和
`test_pcb_synth74.py`（综合）的事。
"""
import unittest

from tests import examples


class TestCounterExample(unittest.TestCase):
    def test_both_source_files_ship_in_the_repo(self):
        self.assertTrue(examples.COUNTER_V.is_file(), examples.COUNTER_V)
        self.assertTrue(examples.COUNTER_TB_V.is_file(), examples.COUNTER_TB_V)

    def test_the_example_has_a_readme_so_it_can_be_used_standalone(self):
        self.assertTrue((examples.COUNTER_DIR / "README.md").is_file())

    def test_the_design_under_test_is_named_after_the_project(self):
        """项目名 counter → 顶层必须是 `module counter`，综合的 `-top` 靠它。"""
        self.assertIn("module counter", examples.counter_rtl())

    def test_the_testbench_top_follows_the_tb_project_convention(self):
        """编排层用 `tb_<project>` 当 iverilog 的 `-s`，改名就是 compile_error。"""
        self.assertIn("module tb_counter", examples.counter_tb())

    def test_the_testbench_instantiates_the_design(self):
        self.assertIn("counter dut", examples.counter_tb())

    def test_the_pass_marker_hdc_greps_for_is_present(self):
        """`SIM_RESULT: PASS` 是仿真通过的唯一判据，写法不能变。"""
        self.assertIn("SIM_RESULT: PASS", examples.counter_tb())

    def test_the_testbench_can_fail_and_says_so(self):
        """只会打印 PASS 的 TB 等于没测；FAIL 分支与计数器都得在。"""
        tb = examples.counter_tb()
        self.assertIn("SIM_RESULT: FAIL", tb)
        self.assertIn("fail_count", tb)

    def test_the_testbench_always_terminates(self):
        """没有 $finish 的 TB 会把 CI 挂死，不是判失败。"""
        self.assertIn("$finish", examples.counter_tb())

    def test_a_missing_example_names_the_file_it_wants(self):
        """路径打错时要说清缺哪份文件，别抛一个裸 FileNotFoundError。"""
        with self.assertRaises(FileNotFoundError) as ctx:
            examples._read(examples.COUNTER_DIR / "nope.v")
        self.assertIn("nope.v", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
