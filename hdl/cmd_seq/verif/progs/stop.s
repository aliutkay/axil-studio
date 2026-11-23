; STOP during WAIT, DLY, WFE and a slow bus write; STEP
        WAIT 100000             ; 0
        NOP  @200               ; 1
        WFE  3                  ; 2
        LDI  R1, 0x000D0000     ; 3  slow slave
        WR   [R1+0], R2         ; 4
        LDI  R5, 0x55           ; 5
        END                     ; 6
