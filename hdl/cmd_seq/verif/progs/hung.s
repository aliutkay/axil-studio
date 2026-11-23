; bus timeout on a slave that never answers
        LDI  R1, 0x000C0000     ; 0
        RD   R2, [R1+0]         ; 1  ERR_HALT=1: ERR_CODE 5
        BR   ERR, seen          ; 2  ERR_HALT=0: ERR flag set, continue
        END                     ; 3
seen:   LDI  R3, 0x77           ; 4
        END                     ; 5
