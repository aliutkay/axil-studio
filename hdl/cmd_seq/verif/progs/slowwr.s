; holds the write engine with a slow transfer
        LDI  R1, 0x000D0000
        WR   [R1+12], R2
        END
