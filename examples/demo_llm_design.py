"""阶段 A 演示：LLM 现场设计「呼吸灯 PWM 调光」，走 verify_design 闭环。

这证明新架构成立：设计完全脱离流水灯模板，由 LLM 自由产出
RTL + 自检 TB + 结构化描述 + 状态机 + 设计构想，工具链（iverilog 仿真 +
yosys 综合）兜底验证。产出落在 output/breathing_led/（sim/synth/diagrams/）。

用法（在项目根目录）：
    python examples/demo_llm_design.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdc.design import Design, verify_design
from hdc.diagram import write_design_diagrams

# 以下 RTL / TB 是「LLM 现场产出」——端口名、参数名、内部结构完全自由，
# 与流水灯模板没有任何共同锚点。
RTL = """// breathing_led — 呼吸灯 PWM 调光
// 亮度呈三角波（先增后减）变化，通过 PWM 占空比驱动 LED，产生「呼吸」效果。
// PHASE_WIDTH 决定呼吸周期：完整周期 = 2^PHASE_WIDTH 个时钟。
module breathing_led #(
    parameter PHASE_WIDTH = 24
) (
    input  wire clk,
    input  wire rst_n,
    output wire led
);
    reg [PHASE_WIDTH-1:0] phase;   // 慢相位计数器：决定当前亮度
    reg [7:0]            pwm_cnt;  // PWM 载波计数器：每时钟 +1

    // 三角波亮度：取 phase 的高 8 位，phase 最高位决定递增/递减方向
    wire [7:0] brightness;
    assign brightness = phase[PHASE_WIDTH-1]
                      ? ~phase[PHASE_WIDTH-2 -: 8]
                      :  phase[PHASE_WIDTH-2 -: 8];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase   <= {PHASE_WIDTH{1'b0}};
            pwm_cnt <= 8'b0;
        end else begin
            phase   <= phase   + 1'b1;
            pwm_cnt <= pwm_cnt + 1'b1;
        end
    end

    // PWM 比较：载波小于亮度则点亮（占空比 = brightness/256）
    assign led = (pwm_cnt < brightness);
endmodule
"""

TB = """`timescale 1ns / 1ps
module tb_breathing_led;
    reg clk;
    reg rst_n;
    wire led;
    integer fail_count;
    integer i;
    integer low_on, high_on;

    // 仿真加速：PHASE_WIDTH=16 → 半呼吸周期 = 2^15 = 32768 时钟
    breathing_led #(.PHASE_WIDTH(16)) dut (.clk(clk), .rst_n(rst_n), .led(led));

    initial begin clk = 1'b0; forever #5 clk = ~clk; end

    `ifdef DUMP_VCD
    initial begin $dumpfile("waveform.vcd"); $dumpvars(0, tb_breathing_led); end
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
        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        #1;
        check("reset_led_off", led === 1'b0);

        rst_n = 1'b1;

        // 窗口 A：刚释放复位，亮度≈0，LED 应基本全灭
        low_on = 0;
        for (i = 0; i < 256; i = i + 1) begin
            @(posedge clk);
            #1;
            if (led === 1'b1) low_on = low_on + 1;
        end
        check("dim_phase_mostly_off", low_on <= 4);

        // 推进到亮度峰值：phase 到半周期的 0x8000
        for (i = 0; i < 32768; i = i + 1) @(posedge clk);
        #1;

        // 窗口 B：亮度≈255，LED 应基本全亮
        high_on = 0;
        for (i = 0; i < 256; i = i + 1) begin
            @(posedge clk);
            #1;
            if (led === 1'b1) high_on = high_on + 1;
        end
        check("bright_phase_mostly_on", high_on >= 250);

        // 呼吸是动态的：两窗口占空比差异显著
        check("pwm_breathing", high_on > low_on);

        // 无 X/Z
        check("no_unknown", (led ^ led) === 1'b0);

        if (fail_count == 0)
            $display("SIM_RESULT: PASS");
        else
            $display("SIM_RESULT: FAIL (%0d check(s) failed)", fail_count);
        $finish;
    end
endmodule
"""

STATE_MACHINE_MD = """# 呼吸灯状态机

呼吸灯无离散事件，但可抽象为两个相位的循环：

```mermaid
stateDiagram-v2
    [*] --> BRIGHTEN
    BRIGHTEN --> DIM : 亮度达峰
    DIM --> BRIGHTEN : 亮度触底
```

实际由 `phase` 计数器的最高位隐式编码方向，非显式寄存器状态机。
"""

CONCEPT_MD = """# 呼吸灯 PWM 调光 — 设计构想

## 需求
一个 LED 呼吸灯：亮度像呼吸一样从暗到亮再到暗，循环往复。

## 设计思路
- **亮度曲线**：三角波（线性上升→线性下降），比正弦更易用组合逻辑实现，
  视觉上同样柔和。
- **调光方式**：PWM（脉宽调制）。8 位亮度值 → 占空比 `brightness/256`，
  靠人眼积分效应呈现不同亮度。
- **两计数器结构**：
  - `phase`（PHASE_WIDTH 位）：慢相位，其高 8 位 + 最高位方向 → 三角波亮度。
  - `pwm_cnt`（8 位）：PWM 载波，每时钟 +1，与亮度比较生成波形。

## 权衡
- 三角波实现无乘法器，仅用加法器 + 反相器 + 比较器，资源极小。
- `PHASE_WIDTH` 决定呼吸周期（2^PHASE_WIDTH 个时钟）；8 位 PWM 给出 256 级亮度，
  对人眼足够平滑。

## 约束 / 待扩展
- 当前 PWM 固定 8 位；如需更平滑可参数化 PWM_WIDTH。
- 呼吸节奏由输入时钟决定；如需可调可加分频或外部相位输入。
"""


def main() -> int:
    design = Design(
        project="breathing_led",
        requirement="做一个 LED 呼吸灯：亮度从暗缓慢变亮再变暗，循环往复",
        rtl=RTL,
        tb=TB,
        design_json={
            "project": "breathing_led",
            "requirement": "LED 呼吸灯：亮度从暗缓慢变亮再变暗，循环往复",
            "interface": {"inputs": ["clk", "rst_n"], "outputs": ["led"]},
            "state_machine": {
                "reset_state": "BRIGHTEN",
                "states": ["BRIGHTEN", "DIM"],
                "transitions": [
                    {"from": "BRIGHTEN", "to": "DIM", "label": "亮度达峰"},
                    {"from": "DIM", "to": "BRIGHTEN", "label": "亮度触底"},
                ],
            },
            "concept": "三角波亮度 + PWM 调光，双计数器结构，无乘法器",
        },
        state_machine_md=STATE_MACHINE_MD,
        concept_md=CONCEPT_MD,
    )

    out_dir = Path("output/breathing_led")
    out = verify_design(design, out_dir, dump_vcd=True)
    diagrams = write_design_diagrams(design.design_json, out_dir / "diagrams")

    print("=" * 62)
    print("LLM 自由设计演示：呼吸灯 PWM 调光")
    print("=" * 62)
    if out.sim is not None:
        print(f"[仿真] passed={out.sim.passed}  error_class={out.sim.error_class}")
        for line in (out.sim.sim_output or "").splitlines():
            if "CHECK" in line or "SIM_RESULT" in line:
                print("   ", line)
    else:
        print(f"[仿真] 跳过：{out.skipped}")
    if out.synth is not None:
        print(f"[综合] ok={out.synth.ok}  error_class={out.synth.error_class}")
    else:
        print(f"[综合] 跳过：{out.skipped}")
    print(f"[图纸] " + ", ".join(str(p.relative_to(out_dir)) for p in diagrams))
    print(f"[产物] {out_dir}/")
    print("=" * 62)
    print("闭环结论：", "PASS ✅" if out.ok else "FAIL ❌")
    if not out.ok:
        for e in out.errors():
            print("   !", e)
    return 0 if out.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
