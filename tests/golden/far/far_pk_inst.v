// Instantiation template for far_pk
far_pk #(
    .C_AXI_ADDR_WIDTH    (32),
    .C_AXI_DATA_WIDTH    (32),
    .OPT_SKIDBUFFER      (1'b0),
    .OPT_LOWPOWER        (0)
) u_far_pk (
    .S_AXI_ACLK          (),  // input
    .S_AXI_ARESETN       (),  // input
    .o_a                 (),  // output
    .o_b                 (),  // output [7:0]
    .SLAVE_AXI_REQ       (),  // input [110:0]
    .SLAVE_AXI_RSP       ()   // output [40:0]
);
