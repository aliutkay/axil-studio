// Instantiation template for far_pk
far_pk u_far_pk (
    .S_AXI_ACLK          (),  // input
    .S_AXI_ARESETN       (),  // input
    .o_a                 (),  // output
    .o_b                 (),  // output [7:0]
    .SLAVE_AXI_REQ       (),  // input [110:0]
    .SLAVE_AXI_RSP       ()   // output [40:0]
);
