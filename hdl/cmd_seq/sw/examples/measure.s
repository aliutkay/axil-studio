; Example 2: periodic measurement, published with SNAP (run with CTRL = 0x51)
.equ ADC_DATA  0x0302_0000
.equ ADC_STAT  0x0302_0004
.equ PERIOD    200

        TMR   R5
loop:   RD    D[1], [ADC_DATA]
        RD    D[2], [ADC_STAT]
        SNAP                     ; D[1..2] go to the master side
        ADD   R5, PERIOD
        WAITT R5
        JMP   loop
