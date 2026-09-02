// 4 位计数器 —— 本项目的基准示例电路。
//
// 选它当基准，是因为它同时压到两条链路上最容易出问题的地方：
//   * 有时序（寄存器 → 需要 dfflibmap 映射到 74HC 触发器）
//   * 有算术（+1 → 综合出 4 位加法器，而不是一堆离散门）
//   * 有异步复位（74HC273 的 /MR 脚要接对）
//   * 有多位输出（count[3:0] → 板上 4 个 LED，也是布线最密的一处）
//
// 这份文件是**示例**，不是工具的一部分。工具在 hdc/ 下；单测把这里当既成事实读进去
// （tests/examples.py），所以改了这里，测试跟着一起走。
module counter (
    input  wire clk,
    input  wire rst_n,
    output reg [3:0] count
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 4'b0;
        else
            count <= count + 1'b1;
    end
endmodule
