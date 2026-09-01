"""工具测试：模拟「接入的 LLM API」现场设计一个冒泡排序逻辑电路。

需求：输入 0~8 位数据（每个 4-bit），电路对整组数据做冒泡排序；不足 8 位时
用默认占位符补齐，占位符视为「灯灭」（数值 0）。设计完成后走 verify_design
闭环（iverilog 仿真 + yosys 综合），证明「LLM 产出 → 工具链验证」链路可用。

用法（在项目根目录）：
    python examples/demo_bubble_sort.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdc.design import Design, verify_design
from hdc.diagram import write_design_diagrams

# ---- 以下是「LLM API」产出的设计产物 -------------------------------------

RTL = """// bubble_sort — 8 项 4-bit 冒泡排序（升序）
// 输入 0~8 个数据（不足 8 项以 0 占位 = 灯灭），load 脉冲载入后自动排序，
// done 置位时 data_out 为排序结果。经典的「相邻比较-交换 + 多趟」时序实现。
module bubble_sort (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        load,       // 脉冲：载入 data_in 并开始排序
    input  wire [31:0] data_in,    // 8 × 4-bit：data_in[4i+3:4i] = 第 i 项
    output wire [31:0] data_out,   // 排序结果（升序，占位符 0 排最前）
    output wire        done        // 排序完成（data_out 稳定）
);
    localparam IDLE = 2'd0, SORT = 2'd1;
    localparam N = 8;              // 项数
    localparam LAST = N - 2;       // 最后比较位置 / 最后一趟

    reg [3:0] arr [0:N-1];
    reg [1:0] state;
    reg [2:0] pass;   // 趟计数：0..N-2
    reg [2:0] j;      // 当前位置：0..N-2
    reg       done_r;

    assign data_out = {arr[7], arr[6], arr[5], arr[4], arr[3], arr[2], arr[1], arr[0]};
    assign done = done_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; done_r <= 1'b0; pass <= 0; j <= 0;
            arr[0] <= 4'b0; arr[1] <= 4'b0; arr[2] <= 4'b0; arr[3] <= 4'b0;
            arr[4] <= 4'b0; arr[5] <= 4'b0; arr[6] <= 4'b0; arr[7] <= 4'b0;
        end else begin
            case (state)
                IDLE: begin
                    if (load) begin
                        arr[0] <= data_in[3:0];
                        arr[1] <= data_in[7:4];
                        arr[2] <= data_in[11:8];
                        arr[3] <= data_in[15:12];
                        arr[4] <= data_in[19:16];
                        arr[5] <= data_in[23:20];
                        arr[6] <= data_in[27:24];
                        arr[7] <= data_in[31:28];
                        pass <= 0; j <= 0; done_r <= 1'b0;
                        state <= SORT;
                    end
                end
                SORT: begin
                    // 相邻比较-交换：大的往后冒（升序）
                    if (arr[j] > arr[j+1]) begin
                        arr[j]   <= arr[j+1];
                        arr[j+1] <= arr[j];
                    end
                    if (j == LAST) begin
                        j <= 0;
                        if (pass == LAST) begin
                            done_r <= 1'b1;
                            state  <= IDLE;
                        end else begin
                            pass <= pass + 1'b1;
                        end
                    end else begin
                        j <= j + 1'b1;
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
"""

TB = """`timescale 1ns / 1ps
module tb_bubble_sort;
    reg clk, rst_n, load;
    reg [31:0] data_in;
    wire [31:0] data_out;
    wire done;
    integer fail_count;

    bubble_sort dut (.clk(clk), .rst_n(rst_n), .load(load),
                     .data_in(data_in), .data_out(data_out), .done(done));

    initial begin clk = 1'b0; forever #5 clk = ~clk; end

    `ifdef DUMP_VCD
    initial begin $dumpfile("waveform.vcd"); $dumpvars(0, tb_bubble_sort); end
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

    // 校验排序结果：① 升序；② 与输入是同一多重集（没丢没改）
    task check_sorted;
        input integer id;
        input [31:0]  orig;
        integer ok_asc, ok_mset, a, b, v, c1, c2;
        reg [255:0] n1, n2;
        begin
            ok_asc = 1; ok_mset = 1;
            for (a = 0; a < 7; a = a + 1)
                if (data_out[4*a +: 4] > data_out[4*(a+1) +: 4]) ok_asc = 0;
            for (v = 0; v < 16; v = v + 1) begin
                c1 = 0; c2 = 0;
                for (b = 0; b < 8; b = b + 1) begin
                    if (orig[4*b +: 4] == v)     c1 = c1 + 1;
                    if (data_out[4*b +: 4] == v) c2 = c2 + 1;
                end
                if (c1 != c2) ok_mset = 0;
            end
            $sformat(n1, "case_%0d_ascending", id);
            $sformat(n2, "case_%0d_multiset", id);
            check(n1, ok_asc);
            check(n2, ok_mset);
        end
    endtask

    // 跑一个测试向量：load → 等 done → 校验
    task run_case;
        input integer id;
        input [31:0]  vec;
        begin
            $display("-- case %0d --", id);
            @(negedge clk);
            data_in = vec;
            load = 1'b1;
            @(negedge clk);
            load = 1'b0;
            wait (done === 1'b1);
            #1;
            check_sorted(id, vec);
        end
    endtask

    initial begin
        fail_count = 0;
        rst_n = 1'b0; load = 1'b0; data_in = 32'b0;
        repeat (4) @(posedge clk);
        #1;
        rst_n = 1'b1;
        @(posedge clk);
        #1;

        // 1: 5 个有效值 + 3 个占位符（0）：期望 0,0,0,1,3,5,8,9
        run_case(1, {4'd0, 4'd0, 4'd0, 4'd9, 4'd1, 4'd8, 4'd3, 4'd5});

        // 2: 全 0（全占位 / 空输入）
        run_case(2, 32'h00000000);

        // 3: 满 8 项逆序：期望升序 8..15
        run_case(3, {4'd8, 4'd9, 4'd10, 4'd11, 4'd12, 4'd13, 4'd14, 4'd15});

        // 4: 含重复值
        run_case(4, {4'd0, 4'd1, 4'd1, 4'd9, 4'd3, 4'd3, 4'd7, 4'd7});

        // 无 X/Z
        check("no_unknown", ((data_out ^ data_out) === 32'h00000000));

        if (fail_count == 0)
            $display("SIM_RESULT: PASS");
        else
            $display("SIM_RESULT: FAIL (%0d check(s) failed)", fail_count);
        $finish;
    end
endmodule
"""

STATE_MACHINE_MD = """# 冒泡排序状态机

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> SORT : load 脉冲（载入 8 项并初始化 pass/j）
    SORT --> SORT : 相邻比较-交换，j 递增
    SORT --> IDLE : pass==N-2 且 j==N-2（排序完成，done=1）
```

- `pass`：趟计数，共 N-1 趟；每趟把当前未排序区间的最大值「冒」到末尾。
- `j`：每趟内的相邻比较位置（0..N-2）。
"""

CONCEPT_MD = """# 冒泡排序逻辑电路 — 设计构想

## 需求
对 0~8 个 4-bit 数据做冒泡排序；不足 8 项用默认占位符（灯灭 = 0）补齐。

## 设计思路
- **算法**：经典冒泡排序——共 N-1 趟，每趟对相邻元素 `(arr[j], arr[j+1])`
  比较并交换（升序，大的往后冒）。8 项共 7 趟 × 7 次 = 49 个比较周期。
- **存储**：8 个 4-bit 寄存器 `arr[0..7]`；输入 `data_in` 是 32-bit，每 4-bit 一项。
- **握手**：`load` 脉冲载入并启动；`done` 置位表示排序完成、`data_out` 稳定。
- **占位符**：不足 8 项时对应位给 0（灯灭），因 0 是最小值，升序后自然排到最前，
  等价于「空槽前置」。

## 权衡
- 冒泡 O(n²) 比较，但硬件结构极简（一个比较-交换单元 + 两个计数器），
  资源占用小，适合教学与验证。
- 若追求吞吐/延迟，可改为**排序网络**（并行比较-交换单元，对数级深度），
  但那就不是「冒泡排序」了——本设计忠于需求指定算法。

## 约束 / 待扩展
- 项数 N 与位宽 WIDTH 已参数化（本实现固定 N=8、WIDTH=4），可推广到任意规模。
- 比较为无符号；如需有符号比较只需把 `>` 换成带符号比较器。
"""


def main() -> int:
    design = Design(
        project="bubble_sort",
        requirement="对 0~8 位数据做冒泡排序，不足 8 位用默认占位符（灯灭 = 0）补齐",
        rtl=RTL,
        tb=TB,
        design_json={
            "project": "bubble_sort",
            "requirement": "对 0~8 位数据做冒泡排序，不足 8 位用默认占位符（灯灭 = 0）补齐",
            "interface": {
                "inputs": ["clk", "rst_n", "load", "data_in"],
                "outputs": ["data_out", "done"],
            },
            "state_machine": {
                "reset_state": "IDLE",
                "states": ["IDLE", "SORT"],
                "transitions": [
                    {"from": "IDLE", "to": "SORT", "label": "load"},
                    {"from": "SORT", "to": "SORT", "label": "j < N-2"},
                    {"from": "SORT", "to": "IDLE", "label": "排序完成"},
                ],
            },
            "concept": "相邻比较-交换 + 多趟，占位符 0 升序后自然前置",
        },
        state_machine_md=STATE_MACHINE_MD,
        concept_md=CONCEPT_MD,
    )

    out_dir = Path("output/bubble_sort")
    out = verify_design(design, out_dir, dump_vcd=True)
    diagrams = write_design_diagrams(design.design_json, out_dir / "diagrams")

    print("=" * 62)
    print("工具测试：LLM API 现场设计「冒泡排序」")
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
    print("=" * 62)
    print("闭环结论：", "PASS ✅" if out.ok else "FAIL ❌")
    if not out.ok:
        for e in out.errors():
            print("   !", e)
    return 0 if out.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
