// Instantiation template for example_ip_axil_pk
example_ip_axil_pk u_example_ip_axil_pk (
    .S_AXI_ACLK          (),  // input
    .S_AXI_ARESETN       (),  // input
    .o_ctrl_enable       (),  // output
    .o_ctrl_mode         (),  // output [2:0]
    .o_ctrl_start        (),  // output
    .o_ctrl_soft_reset   (),  // output
    .o_ctrl_divider      (),  // output [15:0]
    .o_ctrl_wr           (),  // output
    .i_status_busy       (),  // input
    .i_status_level      (),  // input [7:0]
    .i_status_ext_pin    (),  // input [1:0]
    .i_irq_done          (),  // input
    .i_irq_error         (),  // input
    .i_irq_overrun       (),  // input [3:0]
    .i_data_rxdata       (),  // input [15:0]
    .o_data_txdata       (),  // output [15:0]
    .o_data_wr           (),  // output
    .o_data_rd           (),  // output
    .o_irq               (),  // output
    .SLAVE_AXI_REQ       (),  // input [110:0]
    .SLAVE_AXI_RSP       ()   // output [40:0]
);
