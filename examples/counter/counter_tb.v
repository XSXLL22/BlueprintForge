// 4 位计数器的自检测试台 —— 与 counter.v 配套。
//
// 「自检」是本项目对 TB 的硬契约：TB 自己判定对错，不靠人看波形。
//   * 每条断言打印一行 `CHECK <名字>: PASS` 或 `FAIL`
//   * 全部通过时，且仅在全部通过时，打印 `SIM_RESULT: PASS`
//   * 无论对错都要 `$finish`，不能挂住
//
// hdc 就是抓 stdout 里的 `SIM_RESULT: PASS` 来判定仿真通过的（见
// hdc/design.py 的 error_class 分类），所以这两个标记字符串不能改写法。
//
// 顶层模块名必须是 `tb_<project>`，这里即 `tb_counter` —— 编排层按这个名字调
// iverilog 的 `-s`。改名会被归类成 compile_error。
`timescale 1ns / 1ps
module tb_counter;
    reg clk;
    reg rst_n;
    wire [3:0] count;
    integer fail_count;

    counter dut (.clk(clk), .rst_n(rst_n), .count(count));

    initial begin clk = 1'b0; forever #5 clk = ~clk; end

    task check;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("CHECK %0s: PASS", name);
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
        check("reset_zero", count === 4'b0);
        rst_n = 1'b1;
        repeat (1) @(posedge clk);
        #1;
        check("counts_up", count === 4'd1);
        repeat (3) @(posedge clk);
        #1;
        check("counts_up_4", count === 4'd4);
        if (fail_count == 0)
            $display("SIM_RESULT: PASS");
        else
            $display("SIM_RESULT: FAIL (%0d check(s) failed)", fail_count);
        $finish;
    end
endmodule
