; Example 3: duty sweep with a step given by the master in D[0]
.equ PWM_BASE  0x0301_0000
.equ DUTY      0x08

        LDI   R8, PWM_BASE     ; base address in a register
        LDI   R1, 0            ; duty
        LDI   R2, 10           ; number of steps
step:   WR    [R8+DUTY], R1 @20
        LDD   R3, D[0]         ; step from the master
        ADD   R1, R3
        LOOP  R2, step
        STD   D[4], R1         ; publish the last duty
        IRQ
        END
