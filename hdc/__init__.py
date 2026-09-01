"""hdc — AI 辅助数字硬件设计闭环（MVP）。

从 Spec JSON 出发，生成可综合 Verilog + 自检 testbench，用 Icarus Verilog
仿真、Yosys 综合验证，并打包输出设计包。MVP 范围：流水灯（LED chaser）。
"""

__version__ = "0.1.0"
