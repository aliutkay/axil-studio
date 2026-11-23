; Example 4: POLL with a timeout
.equ ADC_DATA  0x0302_0000
.equ ADC_STAT  0x0302_0004
.equ TMO       1000

        TMR   R6
        ADD   R6, TMO          ; deadline
poll:   RD    R4, [ADC_STAT]
        AND   R4, 1            ; ready bit
        BR    NZ, ready
        TMR   R7
        SUB   R7, R6           ; now - deadline
        BR    MI, poll         ; negative: not yet (wrap-safe; LT would be wrong here)
        KILL  0x10             ; timeout
ready:  RD    D[1], [ADC_DATA]
        END
