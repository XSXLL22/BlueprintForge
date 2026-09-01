// =============================================================================
// {{ project }} - LED chaser
// Auto-generated from Spec JSON by the hdc toolchain. DO NOT EDIT BY HAND.
// Regenerate from the spec instead.
//
//   LEDs        : {{ led_count }}
//   Clock       : {{ freq_mhz }} MHz
//   Interval    : {{ interval_ms }} ms  ->  divider = {{ divider }} cycles
//   Direction   : {{ direction }}
//   Wrap        : {{ wrap }}
//   Reset       : {{ reset }}
//   Enable port : {{ enable_port }} ({{ enable_polarity }})
// =============================================================================

module {{ project }} #(
    parameter LED_COUNT = {{ led_count }},
    parameter DIVIDER   = {{ divider }}
)(
    input  wire              clk,
    input  wire              {{ reset_port }},
{{ enable_port_decl }}
    output reg  [LED_COUNT-1:0] led
);

    // first lit position, and the far end of the sweep
    localparam [LED_COUNT-1:0] RESET_LED = {{ led_count }}'b{{ reset_pattern }};
    localparam [LED_COUNT-1:0] END_LED   = {{ led_count }}'b{{ end_pattern }};

    // interval counter (width sized to hold DIVIDER-1)
    reg [{{ tick_msb }}:0] tick;

    always @(posedge clk{{ reset_sensitivity }}) begin
        if ({{ reset_active_cond }}) begin
            led  <= RESET_LED;
            tick <= 0;
        end else if ({{ enable_cond }}) begin
            if (tick == DIVIDER - 1) begin
                tick <= 0;
{{ shift_logic }}
            end else begin
                tick <= tick + 1;
            end
        end
    end

endmodule
