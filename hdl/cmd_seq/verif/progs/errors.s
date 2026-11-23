; entry points for single errors (ENTRY selects one)
        JMP  0                              ; 0 (unused)
        .word64 0x8000000000000258          ; 1  JMP 600: outside I-mem -> 6
        .word64 0x8800000000000000          ; 2  LOOP R0 -> 1
        KILL 0x42                           ; 3  -> 0x42
        .word64 0x0800050000000000          ; 4  KILL 5 -> 1
        WR   [0x0300_0010], R1              ; 5  own window -> 3, ERR_ADDR
        .word64 0x8400FF0000000000          ; 6  BR cond 255 -> 1
        .word64 0x1400000000000000          ; 7  opcode 0x05 -> 1
        RET                                 ; 8  -> 4
        NOP                                 ; 9
