; seq_main.s - SEQ_MAIN (0x0000_0000): runs the UART loopback test and shows its result.
;
;   PC --hexbus--> SEQ_MAIN --+--> SEQ_GPIO (0x0002_0000) --> GPIO  (0x0003_0000): OK / FAIL LEDs
;                             +--> SEQ_UART (0x0004_0000) --> UART1 (0x0005_0000), UART2 (0x0005_1000)
;                                                             looped back outside the FPGA
; Every round: GO to SEQ_UART, wait for its DONE (with a watchdog), take RESULT, pass it to
; SEQ_GPIO (OK LED or FAIL LED), wait the gap, next round. The two children never see each other;
; SEQ_MAIN carries the result across.
;
; Run SEQ_MAIN with CTRL = 0x61 (RUN + PT_WR_RUN + ERR_HALT). At its start it resets and starts
; SEQ_GPIO and SEQ_UART with CTRL = 0x61 if they hold a program and are not running (D[6] = 1: no).
; Settings (0 = default):
;   D[4] 0x0000_0410  GAP      cycles between rounds (0 = 25 000 000 = 0.5 s at 50 MHz)
;   D[5] 0x0000_0414  MAX      rounds to run, then stop and keep the LEDs (0 = for ever)
;   D[6] 0x0000_0418  NOSTART  1 = do not start SEQ_GPIO / SEQ_UART
;   D[7] 0x0000_041C  AGAIN    after MAX rounds: write 1 to run MAX rounds again (cleared)
;   D[8] 0x0000_0420  WDOG     cycles to wait for SEQ_UART (0 = 100 000 000 = 2 s);
;                              no answer: RESULT 0x10
; Read:
;   D[0] 0x0000_0400  ROUNDS   rounds run          D[1] 0x0000_0404  LAST     RESULT of the last one
;   D[2] 0x0000_0408  FAILS    failed rounds       D[3] 0x0000_040C  ROUND_T  cycles the last one took
;   D[9] 0x0000_0424  STATE    0 waiting for the first round, 1 testing, 2 MAX rounds done

.equ SG         0x0002_0000     ; SEQ_GPIO
.equ SU         0x0004_0000     ; SEQ_UART
.equ CTRL       0x08            ; cmd_seq registers
.equ STATUS     0x0C
.equ IMEM_CNT   0x28
.equ CHILD_RST  0x64            ; RESET + PT_WR_RUN + ERR_HALT
.equ CHILD_RUN  0x61            ; RUN + PT_WR_RUN + ERR_HALT

.equ SU_GO      0x0004_0428     ; SEQ_UART D[10]
.equ SU_DONE    0x0004_042C     ; SEQ_UART D[11]
.equ SU_RES     0x0004_0430     ; SEQ_UART D[12]
.equ SG_REQ     0x0002_0400     ; SEQ_GPIO D[0]
.equ SG_RES     0x0002_0404     ; SEQ_GPIO D[1]

.equ R_NOANSWER 0x10
.equ DEF_GAP    25_000_000
.equ DEF_WDOG   100_000_000

.equ M_ROUNDS   0               ; D-mem
.equ M_LAST     1
.equ M_FAILS    2
.equ M_TIME     3
.equ M_GAP      4
.equ M_MAX      5
.equ M_NOSTART  6
.equ M_AGAIN    7
.equ M_WDOG     8
.equ M_STATE    9

; R6 GO number, R7 rounds left (R9 = 0: for ever), R9 MAX of this run, R10 rounds, R11 round start,
; R12 fails, R8 child base (start), R1-R5 scratch

        LDI   R10, 0
        LDI   R12, 0
        STD   D[M_ROUNDS], R10
        STD   D[M_FAILS], R10
        STD   D[M_LAST], R10
        STD   D[M_STATE], R10
        LDD   R1, D[M_NOSTART]
        CMP   R1, 0
        BR    NZ, started
        LDI   R8, SG
        CALL  start
        LDI   R8, SU
        CALL  start
started:
        RD    R6, [SU_GO]       ; GO numbers go on from what SEQ_UART saw last
        LDD   R9, D[M_MAX]
        MOV   R7, R9
        CALL  gap               ; one gap first: the children settle, the UARTs see an idle line
        LDI   R1, 1
        STD   D[M_STATE], R1

round:  ADD   R6, 1
        TMR   R11
        WR    [SU_GO], R6
        LDD   R5, D[M_WDOG]
        CMP   R5, 0
        BR    NZ, rd1
        LDI   R5, DEF_WDOG
rd1:    ADD   R5, R11           ; R5 deadline
wt:     RD    R1, [SU_DONE] @50
        CMP   R1, R6
        BR    Z, answered
        TMR   R2
        SUB   R2, R5
        BR    MI, wt
        LDI   R3, R_NOANSWER
        JMP   result
answered: RD  R3, [SU_RES]
result: TMR   R2
        SUB   R2, R11
        STD   D[M_TIME], R2
        STD   D[M_LAST], R3
        ADD   R10, 1
        STD   D[M_ROUNDS], R10
        CMP   R3, 0
        BR    Z, show
        ADD   R12, 1
        STD   D[M_FAILS], R12
show:   WR    [SG_RES], R3      ; the result, then the request
        WR    [SG_REQ], R6
        CMP   R9, 0             ; MAX = 0: for ever
        BR    Z, next
        SUB   R7, 1
        BR    Z, finished
next:   CALL  gap
        JMP   round

finished:
        LDI   R1, 2
        STD   D[M_STATE], R1
fin_lp: LDD   R1, D[M_AGAIN] @200
        CMP   R1, 0
        BR    Z, fin_lp
        LDI   R1, 0
        STD   D[M_AGAIN], R1
        LDI   R1, 1
        STD   D[M_STATE], R1
        LDD   R9, D[M_MAX]
        MOV   R7, R9
        JMP   round

; ---- gap: wait D[GAP] cycles
gap:    LDD   R1, D[M_GAP]
        CMP   R1, 0
        BR    NZ, gp1
        LDI   R1, DEF_GAP
gp1:    WAIT  R1
        RET

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
