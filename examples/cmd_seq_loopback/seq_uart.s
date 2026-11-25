; seq_uart.s - SEQ_UART (0x0004_0000): UART loopback tester.
;
; The UART lines are looped back outside the FPGA. One test round sends LEN bytes from a sending
; UART and checks that the receiving UART gets exactly them, in order, without parity / frame
; errors and before a timeout. Then the same for a second path (both directions of a crossed cable):
;   path A: UART1 -> UART2        path B: UART2 -> UART1          (defaults)
; For a UART looped to itself (its TX to its own RX) set TX_A = RX_A (and TX_B = RX_B).
; A 2-wire RS-485 bus (both transceivers on the same A/B pair) works with the default PHYC.
;
; SEQ_MAIN starts a round by writing GO with a new value; when the round is over the program
; writes RESULT, then DONE = GO.
;
; Settings (written from the PC; 0 = default):
;   D[0] 0x0004_0400  TX_A     sending UART of path A     (0 = UART1, 0x0005_0000)
;   D[1] 0x0004_0404  RX_A     receiving UART of path A   (0 = UART2, 0x0005_1000)
;   D[2] 0x0004_0408  TX_B     sending UART of path B     (0 = UART2)
;   D[3] 0x0004_040C  RX_B     receiving UART of path B   (0 = UART1)
;   D[4] 0x0004_0410  PATHS    0 = A and B, 1 = only A
;   D[5] 0x0004_0414  LEN      bytes per path and round   (0 = 32)
;   D[6] 0x0004_0418  TMO      clock cycles with no byte sent or received before the rest counts
;                              as lost (0 = 1 000 000 = 20 ms at 50 MHz)
;   D[7] 0x0004_041C  PHYC     PHY_CTRL written to the UARTs (0 = 0xC2: TX and RX on, DE only while
;                              sending, so a 2-wire RS-485 bus works too)
;   D[8] 0x0004_0420  PATTERN  0 = pseudo-random (xorshift32), 1 = counter 00 01 02 ..
;   D[9] 0x0004_0424  SEED     pattern state; the program writes it back after each round, so every
;                              round sends other data (0 = 0x2545_F491)
; Handshake with SEQ_MAIN:
;   D[10] 0x0004_0428 GO       D[11] 0x0004_042C DONE     D[12] 0x0004_0430 RESULT (0 = passed)
;     RESULT b0 wrong byte, b1 byte(s) missing (timeout), b2 parity / frame error,
;            b3 the sending UART did not take all bytes, b5 unexpected byte before the round
; Statistics (read):
;   D[13] 0x434 ROUNDS   D[14] 0x438 GOOD bytes   D[15] 0x43C BAD bytes   D[16] 0x440 LOST bytes
;   D[17] 0x444 LINE_ERR (parity / frame)          D[18] 0x448 JUNK (unexpected bytes)
; First failure of the last failed round:
;   D[19] 0x44C F_PATH (1 = A, 2 = B)   D[20] 0x450 F_IDX (byte index)
;   D[21] 0x454 F_EXP (expected byte)   D[22] 0x458 F_GOT (received byte, 0x100 = nothing came)
;   D[23] 0x45C F_ROUND (GO value of that round)

.equ U_FIFO     0x04            ; axiluart_phy registers
.equ U_RXREG    0x08
.equ U_TXREG    0x0C
.equ U_PHYCTRL  0x10
.equ RX_EMPTY   0x100           ; RXREG bit 8
.equ RX_ERRS    0x6000          ; RXREG bits 13, 14: parity / frame error on this byte
.equ TX_SPACE   0x10000         ; FIFO bit 16
.equ RX_CLEAR   0x0600          ; RXREG write: clear the sticky parity / frame errors

.equ UART1      0x0005_0000
.equ UART2      0x0005_1000
.equ DEF_LEN    32
.equ DEF_TMO    1_000_000
.equ DEF_PHYC   0xC2
.equ DEF_SEED   0x2545_F491

.equ C_TXA      0               ; D-mem
.equ C_RXA      1
.equ C_TXB      2
.equ C_RXB      3
.equ C_PATHS    4
.equ C_LEN      5
.equ C_TMO      6
.equ C_PHYC     7
.equ C_PAT      8
.equ C_SEED     9
.equ H_GO       10
.equ H_DONE     11
.equ H_RES      12
.equ S_ROUNDS   13
.equ S_GOOD     14
.equ S_BAD      15
.equ S_LOST     16
.equ S_LINE     17
.equ S_JUNK     18
.equ F_PATH     19
.equ F_IDX      20
.equ F_EXP      21
.equ F_GOT      22
.equ F_ROUND    23
.equ CUR_GO     24              ; GO of the round being run

.equ R_BAD      0x01            ; RESULT bits
.equ R_LOST     0x02
.equ R_LINE     0x04
.equ R_TXSTUCK  0x08
.equ R_JUNK     0x20
.equ SIGN       255             ; D[255]: mark of this program
.equ SIGNATURE  0x4C0B_0003

; R4 / R5 sending / receiving UART, R6 / R7 pattern state of the sent / expected bytes,
; R8 bytes still to send, R9 bytes still to receive, R10 deadline, R11 RESULT of the round,
; R12 index of the next received byte, R13 path (0 = A, 1 = B), R14 pattern, R15 timeout,
; R1-R3 scratch (gen: R1 state in / out, R2 byte out)

; D-mem still holds what the previously loaded program left there (another program, other
; meanings). On the first start after loading, D[0..24] are cleared (all settings = default) and
; D[255] gets this program's mark; later starts keep the settings. To preset settings before the
; first start, write D[255] = 0x4C0B_0003 (offset 0x7FC) first.
        LDD   R1, D[SIGN]
        CMP   R1, SIGNATURE
        BR    Z, init_ok
        LDI   R1, 0
        LDI   R2, 25
init_lp: SUB  R2, 1
        STD   D[R2], R1
        CMP   R2, 0
        BR    NZ, init_lp
        LDI   R1, SIGNATURE
        STD   D[SIGN], R1
init_ok:
        LDI   R1, 0             ; statistics from zero
        STD   D[S_ROUNDS], R1
        STD   D[S_GOOD], R1
        STD   D[S_BAD], R1
        STD   D[S_LOST], R1
        STD   D[S_LINE], R1
        STD   D[S_JUNK], R1
        STD   D[F_PATH], R1
        LDD   R1, D[H_GO]       ; requests from before the start are not served
        STD   D[H_DONE], R1

wait:   LDD   R1, D[H_GO] @20   ; a new GO?
        LDD   R2, D[H_DONE]
        CMP   R1, R2
        BR    Z, wait

; ---- one round
        STD   D[CUR_GO], R1
        LDI   R11, 0
        LDD   R6, D[C_SEED]
        CMP   R6, 0
        BR    NZ, rd1
        LDI   R6, DEF_SEED
rd1:    LDD   R14, D[C_PAT]
        LDD   R15, D[C_TMO]
        CMP   R15, 0
        BR    NZ, rd2
        LDI   R15, DEF_TMO
rd2:    LDI   R13, 0
path:   CALL  bases
        CALL  run_path
        ADD   R13, 1
        LDD   R1, D[C_PATHS]
        CMP   R1, 0
        BR    NZ, rnd_end       ; only A
        CMP   R13, 2
        BR    LO, path
rnd_end: STD  D[C_SEED], R6     ; next round goes on with the pattern
        LDD   R1, D[S_ROUNDS]
        ADD   R1, 1
        STD   D[S_ROUNDS], R1
        STD   D[H_RES], R11     ; RESULT first, then DONE
        LDD   R1, D[CUR_GO]
        STD   D[H_DONE], R1
        JMP   wait

; ---- bases: R4 / R5 of path R13 (0 = default)
bases:  CMP   R13, 0
        BR    NZ, bs_b
        LDD   R4, D[C_TXA]
        LDD   R5, D[C_RXA]
        CMP   R4, 0
        BR    NZ, bs1
        LDI   R4, UART1
bs1:    CMP   R5, 0
        BR    NZ, bs_ret
        LDI   R5, UART2
        RET
bs_b:   LDD   R4, D[C_TXB]
        LDD   R5, D[C_RXB]
        CMP   R4, 0
        BR    NZ, bs2
        LDI   R4, UART2
bs2:    CMP   R5, 0
        BR    NZ, bs_ret
        LDI   R5, UART1
bs_ret: RET

; ---- run_path: send LEN bytes on R4, check them on R5
run_path:
        LDD   R1, D[C_PHYC]
        CMP   R1, 0
        BR    NZ, rp1
        LDI   R1, DEF_PHYC
rp1:    WR    [R5+U_PHYCTRL], R1
        WR    [R4+U_PHYCTRL], R1
        LDI   R1, RX_CLEAR
        WR    [R5+U_RXREG], R1
flush:  RD    R1, [R5+U_RXREG]  ; bytes waiting before the round: noise / left over
        AND   R1, RX_EMPTY
        BR    NZ, fl_end
        LDD   R1, D[S_JUNK]
        ADD   R1, 1
        STD   D[S_JUNK], R1
        OR    R11, R_JUNK
        JMP   flush
fl_end: LDD   R8, D[C_LEN]
        CMP   R8, 0
        BR    NZ, rp2
        LDI   R8, DEF_LEN
rp2:    MOV   R9, R8
        MOV   R7, R6            ; the receiver expects the same sequence
        LDI   R12, 0
        TMR   R10
        ADD   R10, R15

tx:     CMP   R8, 0             ; send one byte when there is one and the TX FIFO has room
        BR    Z, rx
        RD    R1, [R4+U_FIFO]
        AND   R1, TX_SPACE
        BR    Z, rx
        MOV   R1, R6
        CALL  gen
        MOV   R6, R1
        WR    [R4+U_TXREG], R2
        SUB   R8, 1
        TMR   R10               ; progress: new deadline
        ADD   R10, R15

rx:     RD    R3, [R5+U_RXREG]  ; take one received byte, if any
        MOV   R1, R3
        AND   R1, RX_EMPTY
        BR    NZ, no_rx
        TMR   R10               ; something came: new deadline
        ADD   R10, R15
        MOV   R1, R3
        AND   R1, RX_ERRS
        BR    Z, rx1
        LDD   R1, D[S_LINE]
        ADD   R1, 1
        STD   D[S_LINE], R1
        OR    R11, R_LINE
rx1:    AND   R3, 0xFF
        MOV   R1, R7
        CALL  gen               ; R2 = expected byte
        MOV   R7, R1
        CMP   R2, R3
        BR    NZ, wrong
        LDD   R1, D[S_GOOD]
        ADD   R1, 1
        STD   D[S_GOOD], R1
        JMP   rx2
wrong:  LDD   R1, D[S_BAD]
        ADD   R1, 1
        STD   D[S_BAD], R1
        CALL  first_fail
        OR    R11, R_BAD
rx2:    ADD   R12, 1
        SUB   R9, 1
        BR    NZ, tx
        JMP   rp_end            ; all bytes received

no_rx:  TMR   R1                ; nothing: deadline passed?
        SUB   R1, R10
        BR    MI, tx
        LDD   R1, D[S_LOST]     ; the rest is lost
        ADD   R1, R9
        STD   D[S_LOST], R1
        LDI   R2, 0
        LDI   R3, 0x100         ; F_GOT 0x100: nothing came; F_EXP: the next expected byte
        MOV   R1, R7
        CALL  gen
        CALL  first_fail
        OR    R11, R_LOST
        CMP   R8, 0
        BR    Z, np1
        OR    R11, R_TXSTUCK
np1:
rp_end: CMP   R4, R5            ; on a 2-wire bus the sender hears itself: drop that echo
        BR    Z, rp_ret
echo:   RD    R1, [R4+U_RXREG]
        AND   R1, RX_EMPTY
        BR    Z, echo
        LDI   R1, RX_CLEAR
        WR    [R4+U_RXREG], R1
rp_ret: RET

; ---- first_fail: note R2 (expected) / R3 (received) at byte R12 of path R13, unless this
; round already failed
first_fail:
        MOV   R1, R11
        AND   R1, 0x07
        BR    NZ, ff_ret
        MOV   R1, R13
        ADD   R1, 1
        STD   D[F_PATH], R1
        STD   D[F_IDX], R12
        STD   D[F_EXP], R2
        STD   D[F_GOT], R3
        LDD   R1, D[CUR_GO]
        STD   D[F_ROUND], R1
ff_ret: RET

; ---- gen: next pattern byte. R1 state in / out, R2 the byte (R14: 0 xorshift32, else counter)
gen:    CMP   R14, 0
        BR    NZ, g_cnt
        MOV   R2, R1
        SHL   R2, 13
        XOR   R1, R2
        MOV   R2, R1
        SHR   R2, 17
        XOR   R1, R2
        MOV   R2, R1
        SHL   R2, 5
        XOR   R1, R2
        MOV   R2, R1
        AND   R2, 0xFF
        RET
g_cnt:  MOV   R2, R1
        AND   R2, 0xFF
        ADD   R1, 1
        RET
