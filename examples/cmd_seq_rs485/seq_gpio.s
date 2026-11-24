; seq_gpio.s - SEQ_GPIO (0x0002_0000): blinks LEDs on the GPIO (0x0003_0000) on request.
;
; SEQ_MAIN (or the PC) asks for a blink by writing D[REQ] with a new value; D[N] says how often.
;   D[0] 0x0002_0400  REQ     a new value = blink now (SEQ_MAIN writes the number of bytes seen)
;   D[1] 0x0002_0404  N       blinks per request (0 = 1)
;   D[2] 0x0002_0408  MASK    LEDs that blink (0 = LED 0, o_gpio[0])
;   D[3] 0x0002_040C  ON      cycles on  (0 = 3 000 000 = 60 ms at 50 MHz)
;   D[4] 0x0002_0410  OFF     cycles off (0 = 3 000 000)
;   D[5] 0x0002_0414  DONE    requests served (read)
;   D[6] 0x0002_0418  HB      heartbeat LEDs, toggled while idle (0 = off; e.g. 2 = LED 1)
;   D[7] 0x0002_041C  HB_T    heartbeat half period in cycles (0 = 25 000 000 = 0.5 s at 50 MHz)
; Only SET / CLEAR / TOGGLE are written: the other GPIO outputs are not touched. Requests that
; come while it blinks are served together after it.

.equ G_BASE     0x0003_0000     ; axilgpio
.equ G_SET      0x04
.equ G_CLR      0x08
.equ G_TGL      0x0C

.equ R_REQ      0               ; D-mem
.equ R_N        1
.equ R_MASK     2
.equ R_ON       3
.equ R_OFF      4
.equ R_DONE     5
.equ R_HB       6
.equ R_HBT      7

.equ DEF_MASK   1
.equ DEF_ON     3_000_000       ; 60 ms at 50 MHz
.equ DEF_OFF    3_000_000
.equ DEF_HBT    25_000_000      ; 0.5 s at 50 MHz

; R8 GPIO base, R6 last request seen, R7 next heartbeat time, R12 requests served,
; R2 mask, R4 blink counter, R5 time line, R9 / R10 on / off time, R1 R3 scratch

        LDI   R8, G_BASE
        LDD   R6, D[R_REQ]      ; requests from before the start are not blinked
        LDI   R12, 0
        STD   D[R_DONE], R12
        TMR   R7

idle:   LDD   R1, D[R_REQ]
        CMP   R1, R6
        BR    NZ, req
        LDD   R2, D[R_HB]       ; heartbeat
        CMP   R2, 0
        BR    Z, idle
        TMR   R3
        SUB   R3, R7            ; now - next: negative = not yet (wrap-safe)
        BR    MI, idle
        WR    [R8+G_TGL], R2
        LDD   R3, D[R_HBT]
        CMP   R3, 0
        BR    NZ, hb1
        LDI   R3, DEF_HBT
hb1:    ADD   R7, R3
        JMP   idle

req:    MOV   R6, R1
        LDD   R4, D[R_N]
        CMP   R4, 0
        BR    NZ, rq1
        LDI   R4, 1
rq1:    LDD   R2, D[R_MASK]
        CMP   R2, 0
        BR    NZ, rq2
        LDI   R2, DEF_MASK
rq2:    LDD   R9, D[R_ON]
        CMP   R9, 0
        BR    NZ, rq3
        LDI   R9, DEF_ON
rq3:    LDD   R10, D[R_OFF]
        CMP   R10, 0
        BR    NZ, rq4
        LDI   R10, DEF_OFF
rq4:    TMR   R5
blink:  WR    [R8+G_SET], R2
        ADD   R5, R9
        WAITT R5
        WR    [R8+G_CLR], R2
        ADD   R5, R10
        WAITT R5
        LOOP  R4, blink
        ADD   R12, 1
        STD   D[R_DONE], R12
        TMR   R7                ; heartbeat goes on from now
        JMP   idle
