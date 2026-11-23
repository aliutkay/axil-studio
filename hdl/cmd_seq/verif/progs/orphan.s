; RESET during a slow write, then a new program: its transfers must not take
; the response of the abandoned one
        LDI  R1, 0x000D0000     ; 0
        LDI  R2, 0xAAAA         ; 1
        WR   [R1+4], R2         ; 2  slow (200 cycles)
        END                     ; 3
        LDI  R3, 0x00010000     ; 4  entry of the second program
        LDI  R4, 0x1234         ; 5
        WR   [R3+8], R4         ; 6
        RD   R5, [R3+8]         ; 7
        END                     ; 8
