// Instantiation template for only_ro_pk
only_ro_pk u_only_ro_pk (
    .S_AXI_ACLK          (),  // input
    .S_AXI_ARESETN       (),  // input
    .i_stat_v            (),  // input [31:0]
    .SLAVE_AXI_REQ       (),  // input [110:0]
    .SLAVE_AXI_RSP       ()   // output [40:0]
);
