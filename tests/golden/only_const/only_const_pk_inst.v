// Instantiation template for only_const_pk
only_const_pk u_only_const_pk (
    .S_AXI_ACLK          (),  // input
    .S_AXI_ARESETN       (),  // input
    .SLAVE_AXI_REQ       (),  // input [110:0]
    .SLAVE_AXI_RSP       ()   // output [40:0]
);
