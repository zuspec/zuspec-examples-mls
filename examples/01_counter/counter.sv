
module counter(
    input               clock,
    input               reset,
    output reg[31:0]    count);
    import counter_pkg::*;
    import zsp_pkg::*;

    zsp_reg_if #(reg[31:0])  count_if(.clock(clock), .val(count));
    zsp_clock_if clock_if(.clock(clock));
    zsp_reset_if reset_if(.reset(reset));

    initial begin
        automatic zsp_component_root #(counter_c) counter = new();

        counter.count = zsp_reg_c #(reg[31:0])::mk(count_if);
        counter.clock_d = zsp_domain_clock_c::mk(clock_if);

        counter.run();
    end

endmodule
