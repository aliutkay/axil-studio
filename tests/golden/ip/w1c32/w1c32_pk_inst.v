// Instantiation template for w1c32_pk
w1c32_pk #(
    .C_AXI_ADDR_WIDTH    (32),
    .C_AXI_DATA_WIDTH    (32),
    .OPT_SKIDBUFFER      (1'b0),
    .OPT_LOWPOWER        (0)
) u_w1c32_pk (
    .S_AXI_ACLK          (),  // input
    .S_AXI_ARESETN       (),  // input
    .i_ev_e              (),  // input [31:0]
    .o_p_go              (),  // output [31:0]
    .o_irq               (),  // output
    .SLAVE_AXI_REQ       (),  // input [110:0]
    .SLAVE_AXI_RSP       ()   // output [40:0]
);
