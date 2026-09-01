---
name: hdc-design
description: 用 Claude 做数字逻辑设计的标准工作流。当用户提出任意数字电路需求（不限于流水灯：计数器、PWM、呼吸灯、有限状态机等），需要设计可综合 Verilog RTL + 自检 testbench + 状态机 + 设计构想，并用 hdc 工具链（iverilog 仿真 + yosys 综合）验证时使用。
---

# hdc-design：LLM 逻辑设计工作流

这是 hdc 从「模板填充」升级为「LLM 自由生成 + 工具链验证」后的**设计侧**标准流程。
你是设计大脑：理解开放式需求，产出完整设计产物，工具链负责验证兜底。

## 产物契约（最小硬约定，其余完全自由）

一份设计落盘为（`hdc/design.py` 定义）：

```
output/<project>/
├── design.json          # 结构化描述（project/requirement/interface/state_machine）
├── rtl/<project>.v      # 可综合 Verilog
├── tb/tb_<project>.v    # 自检 testbench
├── state_machine.md     # 状态机描述（Mermaid）
├── concept.md           # 设计构想
└── sim/ synth/ diagrams/   # 工具链产出
```

**必须满足的 4 条硬约定**（否则 `verify_design` 判失败）：

1. RTL 顶层模块名 = `project`；testbench 顶层模块名 = `tb_{project}`。
2. testbench 结束时打印 `SIM_RESULT: PASS`（通过）/ `SIM_RESULT: FAIL`（失败），
   且 `$finish` 在其之后。
3. RTL 可综合：Verilog-2001，无 `initial` 块，yosys 综合无 `ERROR`。
4. 建议（非强制）：每条断言用 `CHECK <name>: PASS/FAIL`；波形用
   `#ifdef DUMP_VCD` 包裹 `$dumpfile("waveform.vcd"); $dumpvars(0, tb_<project>);`。

端口名、参数名、内部结构、状态机组织方式**完全自由**——不要再套流水灯的
`LED_COUNT`/`RESET_LED`/`tick` 命名。

## 标准流程

1. **理解需求 → 设计接口与状态机**：想清楚输入/输出端口、是否有状态机、时序关系。
2. **写 `design.json`**：`project`、`requirement`、`interface`（inputs/outputs 必需）、
   可选 `state_machine`（states/transitions/reset_state）、`concept`。
3. **写 `rtl/<project>.v`**：可综合、非阻塞赋值 `<=`、无 `initial`。
4. **写 `tb/tb_<project>.v`**：自检，见下方标准写法。
5. **写 `state_machine.md` + `concept.md`**：Mermaid 状态图 + 设计思路/权衡/约束。
6. **验证**：用 `hdc.design.verify_design` 跑仿真 + 综合。
7. **修复**：按 `VerifyOutcome.errors()` 的错误分类（`compile_error` / `assertion_failure`
   / `synthesis_error`）定向修复，最多 `max_fix_rounds` 轮。

## testbench 自检标准写法

```verilog
`timescale 1ns / 1ps
module tb_<project>;
    reg clk;
    reg <rst>;            // 与 RTL 一致
    wire <outputs>;       // 与 RTL 一致
    integer fail_count;

    <project> dut (.clk(clk), .<rst>(<rst>), .<out>(<out>), ...);

    initial begin clk = 1'b0; forever #<half_ns> clk = ~clk; end

    `ifdef DUMP_VCD
    initial begin $dumpfile("waveform.vcd"); $dumpvars(0, tb_<project>); end
    `endif

    task check;
        input [255:0] name;
        input ok;
        begin
            if (ok)      $display("CHECK %0s: PASS", name);
            else begin
                $display("CHECK %0s: FAIL", name);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        fail_count = 0;
        // ... 激励 + 逐项 check("断言名", 条件) ...
        if (fail_count == 0)
            $display("SIM_RESULT: PASS");
        else
            $display("SIM_RESULT: FAIL (%0d check(s) failed)", fail_count);
        $finish;
    end
endmodule
```

断言要点：覆盖复位初值、关键行为、边界（如计数溢出/回绕）、结束时无 X/Z。

## 复用（不重造轮子）

- 验证：`hdc.design.verify_design(design, out_dir)`（内部复用 `hdc.verify`）。
- 图纸：`hdc.diagram.write_design_diagrams(design.design_json, diagrams_dir)`。
- 工具链：`hdc.toolchain.detect()` / `env_for()`。

## 与流水灯模板路径的关系

- `python -m hdc specs/led_chaser.json` 仍是旧的模板路径（流水灯专用，保留）。
- `hdc-design` 走的是新的自由设计路径，产物用 `hdc.design` 编排。
