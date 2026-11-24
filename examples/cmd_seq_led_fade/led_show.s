; led_show.s - an LED show on 8 LEDs of axilgpio, run by cmd_seq
;
; Things that would each need their own counters and state machines in HDL, here a few lines:
;   - 16 brightness levels on every LED at once (bit-plane modulation, edges placed with WAITT)
;   - a frame buffer and a wave table in D-mem, walked with indexed addressing D[n+Rx]
;   - four effects as subroutines (CALL / RET): comet with a fading tail, travelling sine wave,
;     random sparkle (16-bit LFSR), breathing
;   - live control from the PC while it runs (Debug Bridge, D-mem window, SNAP_EN = 0):
;       D[16] SPEED  frames per animation step (0 = the default 200; 1 = fastest)
;       D[17] MODE   0 = playlist, 1 = comet, 2 = wave, 3 = sparkle, 4 = breathe
;       D[18] NOW    the effect running now (read only for the PC)
;     e.g. SEQ0 base 0x0000_0000: write 0x0000_0444 = 2 -> only the wave; 0x0000_0440 = 50 -> faster
;
; How the brightness works: a frame is 15 time units. LED i is on in plane k (k = 0..3, 1, 2, 4
; and 8 units long) when bit k of its level B[i] is 1, so it is on B[i] / 15 of the time. The
; planes are written to the GPIO LOAD register (o_gpio[7:0] = LED 0..7; LOAD also sets the other
; outputs to 0). Times are for a 100 MHz clock: U = 1000 cycles -> a frame is 150 us (6.7 kHz).

.equ LED_GPIO   0x0001_0000     ; axilgpio base (GPIO under SEQ0)
.equ LOAD       0x00            ; axilgpio LOAD: all outputs at once

.equ NLED       8               ; LEDs on o_gpio[NLED-1:0] (the effects use LAST and LEDMASK)
.equ LAST       7               ; NLED - 1
.equ LEDMASK    7               ; NLED - 1, NLED a power of two (sparkle)

.equ U          1000            ; time unit (cycles); the planes are U, 2U, 4U, 8U long
.equ U2         2000
.equ U4         4000
.equ U8         8000
.equ DEF_SPEED  200             ; frames per step when D[SPEED] = 0 (200 x 150 us = 30 ms)

.equ FB         0               ; D[0..7]: brightness 0..15 of LED 0..7
.equ SPEED      16
.equ MODE       17
.equ NOW        18
.equ TAB        32              ; D[32..47]: one period of a sine-like wave, 16 levels

; registers: R1 R2 R3 scratch / index, R4 frame counter (hold), R5 time line, R6 position / phase,
; R7 LFSR, R8 GPIO base, R9 comet direction, R10-R13 bit planes, R14 effect step counter, R15 scratch

        LDI   R8, LED_GPIO
        LDI   R7, 0xACE1        ; LFSR seed (not 0)
        LDI   R6, 0
        LDI   R9, 1
        LDI   R1, 0             ; wave table: 0 1 2 4 6 9 12 15 15 12 9 6 4 2 1 0
        STD   D[TAB+0], R1
        STD   D[TAB+15], R1
        LDI   R1, 1
        STD   D[TAB+1], R1
        STD   D[TAB+14], R1
        LDI   R1, 2
        STD   D[TAB+2], R1
        STD   D[TAB+13], R1
        LDI   R1, 4
        STD   D[TAB+3], R1
        STD   D[TAB+12], R1
        LDI   R1, 6
        STD   D[TAB+4], R1
        STD   D[TAB+11], R1
        LDI   R1, 9
        STD   D[TAB+5], R1
        STD   D[TAB+10], R1
        LDI   R1, 12
        STD   D[TAB+6], R1
        STD   D[TAB+9], R1
        LDI   R1, 15
        STD   D[TAB+7], R1
        STD   D[TAB+8], R1
        LDI   R1, 0             ; all LEDs off
        LDI   R3, LAST
clr:    STD   D[FB+R3], R1
        SUB   R3, 1
        BR    PL, clr

; ---- main loop: the playlist, or the effect the PC chose in D[MODE] (checked between effects)
main:   LDD   R1, D[MODE]
        CMP   R1, 1
        BR    Z, m_comet
        CMP   R1, 2
        BR    Z, m_wave
        CMP   R1, 3
        BR    Z, m_spark
        CMP   R1, 4
        BR    Z, m_breath
        CALL  comet
        CALL  comet
        CALL  comet
        CALL  wave
        CALL  sparkle
        CALL  breathe
        JMP   main
m_comet: CALL comet
        JMP   main
m_wave: CALL  wave
        JMP   main
m_spark: CALL sparkle
        JMP   main
m_breath: CALL breathe
        JMP   main

; ---- comet: one bounce (there and back) with a tail that halves every step: 15 7 3 1
comet:  LDI   R1, 1
        STD   D[NOW], R1
        LDI   R14, 14           ; 2 x LAST steps
        LDI   R15, 15
cm_lp:  CALL  fade
        STD   D[FB+R6], R15     ; the head
        CALL  hold
        CMP   R6, LAST          ; turn at the ends, before moving
        BR    LO, cm_low
        LDI   R9, 0xFFFF_FFFF   ; at the right end: go left (-1)
cm_low: CMP   R6, 0
        BR    NZ, cm_mv
        LDI   R9, 1             ; at the left end: go right
cm_mv:  ADD   R6, R9            ; move
        LOOP  R14, cm_lp
        RET

; ---- wave: a sine wave travelling along the LEDs, B[i] = TAB[(phase + 2i) mod 16]
wave:   LDI   R1, 2
        STD   D[NOW], R1
        LDI   R14, 64
wv_lp:  LDI   R3, LAST
wv_i:   MOV   R2, R3
        SHL   R2, 1
        ADD   R2, R6
        AND   R2, 15
        LDD   R1, D[TAB+R2]
        STD   D[FB+R3], R1
        SUB   R3, 1
        BR    PL, wv_i
        CALL  hold
        ADD   R6, 1
        LOOP  R14, wv_lp
        AND   R6, LAST          ; leave a valid comet position
        RET

; ---- sparkle: a random LED flashes up every step, all of them fade
sparkle: LDI  R1, 3
        STD   D[NOW], R1
        LDI   R14, 48
        LDI   R15, 15
sp_lp:  CALL  fade
        CALL  rand
        STD   D[FB+R1], R15
        CALL  hold
        LOOP  R14, sp_lp
        RET

; ---- breathe: all LEDs together, two slow breaths
breathe: LDI  R1, 4
        STD   D[NOW], R1
        LDI   R14, 32
br_lp:  MOV   R2, R6
        AND   R2, 15
        LDD   R1, D[TAB+R2]
        LDI   R3, LAST
br_i:   STD   D[FB+R3], R1
        SUB   R3, 1
        BR    PL, br_i
        CALL  hold
        ADD   R6, 1
        LOOP  R14, br_lp
        AND   R6, LAST
        RET

; ---- fade: every LED to half its brightness
fade:   LDI   R3, LAST
fd_i:   LDD   R1, D[FB+R3]
        SHR   R1, 1
        STD   D[FB+R3], R1
        SUB   R3, 1
        BR    PL, fd_i
        RET

; ---- rand: next value of a 16-bit Galois LFSR (taps 0xB400) in R7; R1 = a random LED number
rand:   MOV   R2, R7
        AND   R2, 1
        SHR   R7, 1
        CMP   R2, 0
        BR    Z, rd_x
        XOR   R7, 0xB400
rd_x:   MOV   R1, R7
        AND   R1, LEDMASK
        RET

; ---- hold: show the frame buffer for D[SPEED] frames
hold:   LDD   R4, D[SPEED]
        CMP   R4, 0
        BR    NZ, hd_lp
        LDI   R4, DEF_SPEED
hd_lp:  CALL  frame
        LOOP  R4, hd_lp
        RET

; ---- frame: one frame of the buffer, 4 bit planes of 1, 2, 4 and 8 units
frame:  LDI   R10, 0
        LDI   R11, 0
        LDI   R12, 0
        LDI   R13, 0
        LDI   R3, LAST          ; LED LAST first: after the shifts LED i is bit i
fr_i:   LDD   R1, D[FB+R3]
        SHL   R10, 1
        MOV   R2, R1
        AND   R2, 1
        OR    R10, R2
        SHL   R11, 1
        MOV   R2, R1
        SHR   R2, 1
        AND   R2, 1
        OR    R11, R2
        SHL   R12, 1
        MOV   R2, R1
        SHR   R2, 2
        AND   R2, 1
        OR    R12, R2
        SHL   R13, 1
        MOV   R2, R1
        SHR   R2, 3
        AND   R2, 1
        OR    R13, R2
        SUB   R3, 1
        BR    PL, fr_i
        TMR   R5
        WR    [R8+LOAD], R10    ; plane 0: 1 unit
        ADD   R5, U
        WAITT R5
        WR    [R8+LOAD], R11    ; plane 1: 2 units
        ADD   R5, U2
        WAITT R5
        WR    [R8+LOAD], R12    ; plane 2: 4 units
        ADD   R5, U4
        WAITT R5
        WR    [R8+LOAD], R13    ; plane 3: 8 units (it stays while the next frame is computed)
        ADD   R5, U8
        WAITT R5
        RET
