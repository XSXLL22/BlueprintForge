# 74xx-liberty（vendored）

来源：<https://github.com/pepijndevos/74xx-liberty>

为 hdc 的「yosys → 74 系列离散逻辑门」综合 vendored（去掉了 `benchmarks/` 与 `.git`，
其余保持上游原样）。用途：

- `74ac.lib` —— Liberty 单元库，`dfflibmap -liberty` / `abc -liberty` 映射目标。
- `74_*.v`（`74_adder.v` `74_cmp.v` `74_eq.v` `74_counter.v` `74_dffe.v`
  `74_models.v` `74_mux.v`）—— 高层次单元的 Verilog 模型，`techmap` 用。
- `74_extract.il` —— yosys 的 `extract` 模式文件。
- `synth_74.ys` / `ic_count.py` / `kicad/parts.py` —— 参考综合脚本 / 装箱统计 / SKiDL 建件。
- `kicad/74xx.lib` + `kicad/74xx.dcm` —— KiCad 符号库（74 系列芯片符号）。

> 上游仓库未随附 LICENSE 文件。hdc 仅作只读参考与综合输入，不修改、不重新分发其
> 二进制产物；如对外分发请自行确认上游授权。
