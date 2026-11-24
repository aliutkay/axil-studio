; seq_uart.s - SEQ_UART (0x0004_0000): RS-485 terminal. Every byte received gets a reply
; ("OK: <byte>" + CR LF), and SEQ_MAIN is told through D[EVENTS] (it makes SEQ_GPIO blink).
;
; Which UART receives and which one sends is set from the PC (Debug Bridge, no new program):
;   D[0] 0x0004_0400  RX_BASE   receiving UART      (0 = UART1, 0x0005_0000)
;   D[1] 0x0004_0404  TX_BASE   sending UART        (0 = UART2, 0x0005_1000)
;   D[2] 0x0004_0408  RX_CTRL   PHY_CTRL of the receiver   (0 = 0x80: RX on, TX off, DE low)
;   D[3] 0x0004_040C  TX_CTRL   PHY_CTRL of the sender     (0 = 0x42: TX on, RX off, DE while sending)
;   e.g. swap the two: write 0x0004_0400 = 0x0005_1000 and 0x0004_0404 = 0x0005_0000.
; The program reads them before every byte and applies a change at once (the receiver first, so
; the two drivers never drive together).
; Read by the PC:
;   D[4] 0x0004_0410  COUNT     bytes answered      D[5] 0x0004_0414  LAST    last byte
;   D[6] 0x0004_0418  ERRORS    parity / frame errors seen
;   D[7] 0x0004_041C  EVENTS    +1 per byte answered (SEQ_MAIN watches it)
; Reply text: D[32..63] 0x0004_0480.., one character per word, 0 ends it (0 at start = "OK: ").
; CR and LF are not answered (Enter in a terminal sends them). Baud rate: SETUP of the UARTs
; (hardware default INITIAL_SETUP, or set it from the UART interface).

.equ U_FIFO     0x04            ; axiluart_phy registers
.equ U_RXREG    0x08
.equ U_TXREG    0x0C
.equ U_PHYCTRL  0x10
.equ RX_EMPTY   0x100           ; RXREG bit 8
.equ RX_ERRS    0x6000          ; RXREG bits 13, 14: this byte had a parity / frame error
.equ TX_SPACE   0x10000         ; FIFO bit 16
.equ RX_CLEAR   0x0600          ; RXREG write: clear the sticky parity / frame errors (bit 12 would
                                ; reset the receiver, which then ignores the line for ~16 bit times)

.equ DEF_RX     0x0005_0000     ; UART1
.equ DEF_TX     0x0005_1000     ; UART2
.equ DEF_RXC    0x80
.equ DEF_TXC    0x42

.equ C_RX       0               ; D-mem
.equ C_TX       1
.equ C_RXC      2
.equ C_TXC      3
.equ S_COUNT    4
.equ S_LAST     5
.equ S_ERR      6
.equ S_EVENT    7
.equ MSG        32
.equ MSG_MAX    31

; R8 / R9 receiving / sending UART, R10 / R11 their PHY_CTRL, R12 count, R13 events, R14 errors,
; R5 the byte, R4 text index, R1-R3 R6 scratch

        LDI   R12, 0
        STD   D[S_COUNT], R12
        STD   D[S_ERR], R12
        LDI   R14, 0
        LDD   R13, D[S_EVENT]   ; go on counting: SEQ_MAIN compares with what it saw last
        LDD   R1, D[MSG]
        CMP   R1, 0
        BR    NZ, have_msg
        LDI   R1, 0x4F          ; "OK: "
        STD   D[MSG+0], R1
        LDI   R1, 0x4B
        STD   D[MSG+1], R1
        LDI   R1, 0x3A
        STD   D[MSG+2], R1
        LDI   R1, 0x20
        STD   D[MSG+3], R1
        LDI   R1, 0
        STD   D[MSG+4], R1
have_msg:
        LDI   R8, 0             ; no UART yet: config applies the settings
        LDI   R9, 0
        LDI   R10, 0
        LDI   R11, 0

loop:   CALL  config
        RD    R1, [R8+U_RXREG]  ; reading takes the byte out of the RX FIFO
        MOV   R2, R1
        AND   R2, RX_EMPTY
        BR    NZ, loop
        MOV   R2, R1
        AND   R2, RX_ERRS
        BR    Z, rx_ok
        ADD   R14, 1
        STD   D[S_ERR], R14
rx_ok:  AND   R1, 0xFF
        CMP   R1, 0x0D          ; CR, LF: no answer
        BR    Z, loop
        CMP   R1, 0x0A
        BR    Z, loop
        MOV   R5, R1
        ADD   R12, 1
        STD   D[S_COUNT], R12
        STD   D[S_LAST], R5
        LDI   R4, 0             ; the reply: text, the byte, CR LF
msg_lp: LDD   R3, D[MSG+R4]
        CMP   R3, 0
        BR    Z, msg_end
        CALL  putc
        ADD   R4, 1
        CMP   R4, MSG_MAX
        BR    LO, msg_lp
msg_end: MOV  R3, R5
        CALL  putc
        LDI   R3, 0x0D
        CALL  putc
        LDI   R3, 0x0A
        CALL  putc
        ADD   R13, 1            ; tell SEQ_MAIN
        STD   D[S_EVENT], R13
        JMP   loop

; ---- putc: send R3 when the TX FIFO has room
putc:   RD    R2, [R9+U_FIFO]
        AND   R2, TX_SPACE
        BR    Z, putc
        WR    [R9+U_TXREG], R3
        RET

; ---- config: take D[0..3] (0 = default); apply when something changed
config: LDD   R1, D[C_RX]
        CMP   R1, 0
        BR    NZ, cf1
        LDI   R1, DEF_RX
cf1:    LDD   R2, D[C_TX]
        CMP   R2, 0
        BR    NZ, cf2
        LDI   R2, DEF_TX
cf2:    LDD   R3, D[C_RXC]
        CMP   R3, 0
        BR    NZ, cf3
        LDI   R3, DEF_RXC
cf3:    LDD   R6, D[C_TXC]
        CMP   R6, 0
        BR    NZ, cf4
        LDI   R6, DEF_TXC
cf4:    CMP   R1, R8
        BR    NZ, apply
        CMP   R2, R9
        BR    NZ, apply
        CMP   R3, R10
        BR    NZ, apply
        CMP   R6, R11
        BR    NZ, apply
        RET
apply:  MOV   R8, R1
        MOV   R9, R2
        MOV   R10, R3
        MOV   R11, R6
        WR    [R8+U_PHYCTRL], R10   ; the receiver first: its driver goes off
        WR    [R9+U_PHYCTRL], R11
        LDI   R1, RX_CLEAR
        WR    [R8+U_RXREG], R1
        RET
