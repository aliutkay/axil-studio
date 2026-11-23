; indexed D-mem: fill D[8..39] in a loop, read back through an index, read a bus word into D[n+Rx]
        LDI  R2, 0              ; index
        LDI  R3, 32             ; count
fill:   MOV  R4, R2
        SHL  R4, 4
        STD  D[8+R2], R4        ; D[8+i] = 16*i
        ADD  R2, 1
        LOOP R3, fill
        LDI  R5, 30
        LDD  R6, D[R5+8]        ; D[38] = 480
        LDI  R1, 0x00010000
        LDI  R7, 2
        RD   D[40+R7], [R1+8]   ; D[42] = IP1 word 2
        LDI  R8, 300
        LDD  R9, D[R8]          ; out of range (DMEM_AW = 8): error 7
        END
