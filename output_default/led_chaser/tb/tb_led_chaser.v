// =============================================================================
// tb_led_chaser — self-checking testbench for led_chaser
// Auto-generated from Spec JSON by the hdc toolchain. DO NOT EDIT BY HAND.
//
// Assertions checked:
//   1. reset_initial_state : after reset, led == RESET_LED
//   2. hold_when_disabled  : with enable inactive, led must not move
//   3. interval            : each step takes exactly DIVIDER clock cycles
//   4. direction           : each step matches the expected pattern
//   5. wrap / no_wrap_hold : sweep loops back, or holds at the far end
//   6. no_unknown          : no X/Z left on led at end of simulation
// =============================================================================

`timescale 1ns / 1ps

module tb_led_chaser;

    localparam LED_COUNT = 4;
    localparam DIVIDER   = 25000000;

    localparam [LED_COUNT-1:0] RESET_LED = 4'b1000;
    localparam [LED_COUNT-1:0] END_LED   = 4'b0001;

    reg clk;
    reg rst_n;
    reg en;

    wire [LED_COUNT-1:0] led;

    integer fail_count;
    integer cycles;
    integer step;
    reg  [LED_COUNT-1:0] prev_led;
    reg  [LED_COUNT-1:0] expect_seq [0:3];

    led_chaser #(
        .LED_COUNT (LED_COUNT),
        .DIVIDER   (DIVIDER)
    ) dut (
        .clk (clk),
        .rst_n (rst_n),
        .en (en),

        .led (led)
    );

    // clock
    initial begin
        clk = 1'b0;
        forever #10.000000 clk = ~clk;
    end

    // expected pattern sequence (one entry per interval step)
    initial begin
        expect_seq[0] = 4'b0100;
        expect_seq[1] = 4'b0010;
        expect_seq[2] = 4'b0001;
        expect_seq[3] = 4'b1000;

    end

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

        // 1. reset -> initial state
        rst_n = 1'b0;
        en = 1'b0;
        repeat (10) @(posedge clk);
        #1;
        check("reset_initial_state", led === RESET_LED);

        // 2. release reset; must hold while disabled
        rst_n = 1'b1;
        repeat (DIVIDER + 10) @(posedge clk);
        #1;
        check("hold_when_disabled", led === RESET_LED);

        // 3. enable -> sweep through the expected sequence
        en = 1'b1;
        prev_led = led;

        for (step = 0; step < 4; step = step + 1) begin
            $display("-- step %0d: expecting led = %0b --", step, expect_seq[step]);
            cycles = 0;
            while (led === prev_led && cycles < (DIVIDER * 2)) begin
                @(posedge clk);
                #1;
                cycles = cycles + 1;
            end
            check("interval", cycles == DIVIDER);
            check("direction", led === expect_seq[step]);
            prev_led = led;
        end

        check("wrap_return", led === RESET_LED);


        // no unknown / high-Z on led
        check("no_unknown", ((led ^ led) === {LED_COUNT{1'b0}}));

        // summary
        if (fail_count == 0)
            $display("SIM_RESULT: PASS");
        else
            $display("SIM_RESULT: FAIL (%0d check(s) failed)", fail_count);
        $finish;
    end

endmodule
