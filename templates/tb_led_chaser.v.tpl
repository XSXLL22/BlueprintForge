// =============================================================================
// tb_{{ project }} — self-checking testbench for {{ project }}
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

module tb_{{ project }};

    localparam LED_COUNT = {{ led_count }};
    localparam DIVIDER   = {{ divider }};

    localparam [LED_COUNT-1:0] RESET_LED = {{ led_count }}'b{{ reset_pattern }};
    localparam [LED_COUNT-1:0] END_LED   = {{ led_count }}'b{{ end_pattern }};

    reg clk;
    reg {{ reset_port }};
{{ en_port_decl }}
    wire [LED_COUNT-1:0] led;

    integer fail_count;
    integer cycles;
    integer step;
    reg  [LED_COUNT-1:0] prev_led;
    reg  [LED_COUNT-1:0] expect_seq [0:{{ n_steps_minus_1 }}];

    {{ project }} #(
        .LED_COUNT (LED_COUNT),
        .DIVIDER   (DIVIDER)
    ) dut (
        .clk (clk),
        .{{ reset_port }} ({{ reset_port }}),
{{ en_port_connect }}
        .led (led)
    );

    // clock
    initial begin
        clk = 1'b0;
        forever #{{ half_ns }} clk = ~clk;
    end

    // expected pattern sequence (one entry per interval step)
    initial begin
{{ expect_seq_init }}
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

{{ stimulus_prelude }}
{{ transition_block }}
{{ end_behavior_block }}

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
