; Example 1: PWM with an exact period (100 MHz: both edges 500 ns apart)
.equ GPIO_OUT  0x0301_0004
.equ HALF      50              ; half period (cycles)

        LDI   R1, 1
        TMR   R5               ; start time
loop:   ADD   R5, HALF
        WAITT R5
        WR    [GPIO_OUT], R0   ; pin = 0
        ADD   R5, HALF
        WAITT R5
        WR    [GPIO_OUT], R1   ; pin = 1
        JMP   loop
