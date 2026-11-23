; LDD must return the word it addresses (regression: one-cycle-late D-mem read)
        LDI  R1, 0x11
        STD  D[0], R1
        LDI  R1, 0x22
        STD  D[1], R1
        LDD  R2, D[1]           ; 0x22
        LDD  R3, D[0]           ; 0x11
        LDD  R4, D[1] @3        ; 0x22
        END
