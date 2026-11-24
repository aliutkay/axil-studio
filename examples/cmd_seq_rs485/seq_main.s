; seq_main.s - SEQ_MAIN (0x0000_0000): starts the other two sequencers and connects them.
;
;   PC --hexbus--> SEQ_MAIN --+--> SEQ_GPIO (0x0002_0000) --> GPIO  (0x0003_0000)
;                             +--> SEQ_UART (0x0004_0000) --> UART1 (0x0005_0000), UART2 (0x0005_1000)
;
; SEQ_UART answers on RS-485 and counts the bytes in its D[EVENTS]; SEQ_GPIO blinks when its
; D[REQ] changes. SEQ_UART cannot reach SEQ_GPIO (each one only sees its own lower bus), so this
; program watches SEQ_UART and writes SEQ_GPIO: a byte '1'..'9' blinks that many times, any
; other byte once.
;
; Run SEQ_MAIN with CTRL = 0x61 (RUN + PT_WR_RUN + ERR_HALT): with PT_WR_RUN the PC can still
; write the lower blocks through it (settings in SEQ_UART / SEQ_GPIO, the UART interface).
; At its start it resets and starts SEQ_GPIO and SEQ_UART with CTRL = 0x61, if they hold a
; program (IMEM_CNT > 0) and are not running yet. D[2] = 1 turns this off.
;   D[0] 0x0000_0400  FWD     requests passed on (read)
;   D[1] 0x0000_0404  LAST    last byte (read)
;   D[2] 0x0000_0408  NOSTART 1 = do not start SEQ_GPIO / SEQ_UART

.equ SG         0x0002_0000     ; SEQ_GPIO
.equ SU         0x0004_0000     ; SEQ_UART
.equ CTRL       0x08            ; cmd_seq registers
.equ STATUS     0x0C
.equ IMEM_CNT   0x28
.equ CHILD_RST  0x64            ; RESET + PT_WR_RUN + ERR_HALT
.equ CHILD_RUN  0x61            ; RUN + PT_WR_RUN + ERR_HALT

.equ SU_LAST    0x0004_0414     ; SEQ_UART D[5]
.equ SU_EVENT   0x0004_041C     ; SEQ_UART D[7]
.equ SG_REQ     0x0002_0400     ; SEQ_GPIO D[0]
.equ SG_N       0x0002_0404     ; SEQ_GPIO D[1]

.equ M_FWD      0
.equ M_LAST     1
.equ M_NOSTART  2

; R6 SEQ_UART events seen, R12 requests passed on, R8 child base, R1-R3 scratch

        LDD   R1, D[M_NOSTART]
        CMP   R1, 0
        BR    NZ, started
        LDI   R8, SG
        CALL  start
        LDI   R8, SU
        CALL  start
started:
        LDI   R12, 0
        STD   D[M_FWD], R12
        RD    R6, [SU_EVENT]

loop:   RD    R1, [SU_EVENT] @50 ; a new byte answered?
        CMP   R1, R6
        BR    Z, loop
        MOV   R6, R1
        RD    R2, [SU_LAST]
        MOV   R3, R2            ; '1'..'9' -> 1..9, anything else -> 1
        SUB   R3, 0x31
        CMP   R3, 9
        BR    LO, digit
        LDI   R3, 0
digit:  ADD   R3, 1
        WR    [SG_N], R3        ; how often, then the request
        WR    [SG_REQ], R6
        ADD   R12, 1
        STD   D[M_FWD], R12
        STD   D[M_LAST], R2
        JMP   loop

; ---- start: reset and run the sequencer at R8 if it holds a program and is not running
start:  RD    R1, [R8+STATUS]
        AND   R1, 1             ; BUSY
        BR    NZ, st_ret
        RD    R1, [R8+IMEM_CNT]
        CMP   R1, 0
        BR    Z, st_ret
        LDI   R1, CHILD_RST
        WR    [R8+CTRL], R1
        LDI   R1, CHILD_RUN
        WR    [R8+CTRL], R1
st_ret: RET
