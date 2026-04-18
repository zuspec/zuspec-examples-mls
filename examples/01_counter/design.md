
counter.py is the canonical (and simplest) example of a MLS design.
- Process binds to the default clock and reset domains
- Reg.write is async, ensuring that the data is driven off the active clock edge

We expect this to synthesize to:

module Counter(
    input clock,
    input reset,
    output reg[31:0]    count);
  
  always @(posedge clock or posedge reset) begin
      if (reset) begin
        count <= 0;
      end else begin
        count <= count + 1;
      end
  end

endmodule
