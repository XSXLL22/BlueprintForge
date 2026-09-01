// =============================================================================
// led_chaser - LED chaser
// Auto-generated from Spec JSON by the hdc toolchain. DO NOT EDIT BY HAND.
// Regenerate from the spec instead.
//
//   LEDs        : 4
//   Clock       : 50 MHz
//   Interval    : 500 ms  ->  divider = 25000000 cycles
//   Direction   : left_to_right
//   Wrap        : true
//   Reset       : async_active_low
//   Enable port : yes (active_high)
// =============================================================================

module led_chaser #(
    parameter LED_COUNT = 4,
    parameter DIVIDER   = 25000000
)(
    input  wire              clk,
    input  wire              rst_n,
    input  wire              en,

    output reg  [LED_COUNT-1:0] led
);

    // first lit position, and the far end of the sweep
    localparam [LED_COUNT-1:0] RESET_LED = 4'b1000;
    localparam [LED_COUNT-1:0] END_LED   = 4'b0001;

    // interval counter (width sized to hold DIVIDER-1)
    reg [24:0] tick;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            led  <= RESET_LED;
            tick <= 0;
        end else if (en) begin
            if (tick == DIVIDER - 1) begin
                tick <= 0;
                if (led == END_LED)
                    led <= RESET_LED;
                else
                    led <= led >> 1;

            end else begin
                tick <= tick + 1;
            end
        end
    end

endmodule
