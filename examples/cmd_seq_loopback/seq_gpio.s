; seq_gpio.s - SEQ_GPIO (0x0002_0000): OK / FAIL LEDs of the UART loopback test (GPIO 0x0003_0000).
;
; SEQ_MAIN writes the RESULT of each round into D[RES], then a new value into D[REQ].
;   passed (RESULT 0): the OK LED is on, the FAIL LED off
;   failed           : the FAIL LED is on, the OK LED off
; The lit LED goes dark for a moment at every round, so a running test can be told from a stuck
; one. At the start both LEDs light for 6 x BLINK (lamp test).
;   D[0] 0x0002_0400  REQ      a new value = a new result
;   D[1] 0x0002_0404  RES      RESULT of the round (SEQ_MAIN writes it)
;   D[2] 0x0002_0408  OK_LED   mask of the OK LED   (0 = LED 0, o_gpio[0])
;   D[3] 0x0002_040C  ERR_LED  mask of the FAIL LED (0 = LED 1, o_gpio[1])
;   D[4] 0x0002_0410  HOLD     1 = after a failure the FAIL LED stays on, even if later rounds pass
;   D[5] 0x0002_0414  HELD     1 = a failure is being held (write 0 to let go)
;   D[6] 0x0002_0418  BLINK    cycles the lit LED is dark at a round (0 = 2 500 000 = 50 ms)
;   D[7] 0x0002_041C  SHOWN    results shown (read)
; Only SET / CLEAR are written: the other GPIO outputs are not touched.

.equ G_BASE     0x0003_0000     ; axilgpio
.equ G_SET      0x04
.equ G_CLR      0x08

.equ L_REQ      0               ; D-mem
.equ L_RES      1
.equ L_OK       2
.equ L_ERR      3
.equ L_HOLD     4
.equ L_HELD     5
.equ L_BLINK    6
.equ L_SHOWN    7

.equ DEF_OK     1
.equ DEF_ERR    2
.equ DEF_BLINK  2_500_000       ; 50 ms at 50 MHz

; R8 GPIO base, R6 last request, R12 shown, R2 OK mask, R3 FAIL mask, R4 lit, R5 dark,
; R9 time, R1 scratch

        LDI   R8, G_BASE
        LDD   R6, D[L_REQ]      ; requests from before the start are not shown
        LDI   R12, 0
        STD   D[L_SHOWN], R12
        STD   D[L_HELD], R12
        CALL  masks
        CALL  blink_t           ; lamp test: both on for 6 x BLINK
        MOV   R4, R1
        SHL   R1, 1
        SHL   R4, 2
        ADD   R4, R1
        MOV   R1, R2
        OR    R1, R3
        WR    [R8+G_SET], R1
        TMR   R9
        ADD   R9, R4
        WAITT R9
        WR    [R8+G_CLR], R1

idle:   LDD   R1, D[L_REQ] @20
        CMP   R1, R6
        BR    Z, idle
        MOV   R6, R1
        CALL  masks
        LDD   R1, D[L_RES]
        CMP   R1, 0
        BR    Z, pass
        LDD   R1, D[L_HOLD]     ; failed
        CMP   R1, 0
        BR    Z, fail
        LDI   R1, 1
        STD   D[L_HELD], R1
fail:   MOV   R4, R3
        MOV   R5, R2
        JMP   light
pass:   LDD   R1, D[L_HELD]     ; passed, unless a failure is held
        CMP   R1, 0
        BR    NZ, fail
        MOV   R4, R2
        MOV   R5, R3
light:  WR    [R8+G_CLR], R5    ; the other one off
        WR    [R8+G_CLR], R4    ; the lit one dark for a moment: a round was run
        CALL  blink_t
        TMR   R9
        ADD   R9, R1
        WAITT R9
        WR    [R8+G_SET], R4
        ADD   R12, 1
        STD   D[L_SHOWN], R12
        JMP   idle

; ---- masks: R2 OK LED, R3 FAIL LED (0 = defaults)
masks:  LDD   R2, D[L_OK]
        CMP   R2, 0
        BR    NZ, mk1
        LDI   R2, DEF_OK
mk1:    LDD   R3, D[L_ERR]
        CMP   R3, 0
        BR    NZ, mk2
        LDI   R3, DEF_ERR
mk2:    RET

; ---- blink_t: R1 = D[BLINK] (0 = default)
blink_t: LDD  R1, D[L_BLINK]
        CMP   R1, 0
        BR    NZ, bt1
        LDI   R1, DEF_BLINK
bt1:    RET
