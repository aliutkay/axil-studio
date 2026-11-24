; led_fade.s - two LEDs on axilgpio, cross-fading under cmd_seq control
;
; LED A (o_gpio[0]) starts at full brightness, LED B (o_gpio[1]) off. Then B slowly
; brightens while A dims, until B is full and A off; then back again. Forever.
;
; axilgpio has no PWM: the program makes it. Every PWM period of PERIOD cycles is split in
; two parts: B on / A off for D cycles, then A on / B off for PERIOD - D cycles. So
;   brightness(B) = D / PERIOD,  brightness(A) = 1 - D / PERIOD.
; D goes 0 -> PERIOD -> 0 in steps of STEP. The edges are placed with WAITT on the free-running
; TIMER, so the period does not depend on the bus latency.
;
; Every switch is ONE write to TOGGLE with both LED bits: A and B change in the same clock, so
; they are never both on or both off, and the other GPIO outputs are not touched. R7 remembers
; which LED is on. Both edges go through the same instructions after their WAITT, so B is on
; for exactly D cycles.
;
; Times below are for a 100 MHz clock:
;   PERIOD 8000 cycles = 80 us (12.5 kHz PWM, no flicker)
;   STEP   80 cycles   -> 100 brightness levels
;   REP    188 periods per level  -> one fade = 100 x 188 x 80 us = 1.5 s
;   HOLD   6250 periods at each end (A full / B full) = 0.5 s
; Change the .equ lines for another clock or speed. Rules:
;   - PERIOD must be a multiple of STEP;
;   - STEP must be at least 64 cycles: from one edge to the next the program needs about 40
;     cycles (loop counters, CALL / RET, the TOGGLE write); a shorter slot makes that period late.

.equ LED_GPIO   0x0301_0000     ; axilgpio base = GPIO_BASE of led_fade.axproj.json (GPIO under SEQ0)
.equ SET        0x04            ; axilgpio SET    : 1 bits are set
.equ CLR        0x08            ; axilgpio CLEAR  : 1 bits are cleared
.equ TGL        0x0C            ; axilgpio TOGGLE : 1 bits are inverted
.equ LED_A      0b01            ; o_gpio[0]
.equ LED_B      0b10            ; o_gpio[1]

.equ PERIOD     8000
.equ STEP       80
.equ REP        188
.equ HOLD       6250

; registers: R1 = D (on time of B), R2 / R3 = LED masks, R4 = period counter,
;            R5 = start of the next period (TIMER value), R6 = start + D, R7 = 1 when B is on,
;            R8 = GPIO base, R9 = both LED bits

        LDI   R8, LED_GPIO
        LDI   R2, LED_A
        LDI   R3, LED_B
        LDI   R9, LED_A + LED_B
        WR    [R8+CLR], R3      ; B off
        WR    [R8+SET], R2      ; A on
        LDI   R7, 0
        TMR   R5
        ADD   R5, PERIOD        ; first period starts one period from now
        LDI   R1, 0             ; D = 0: A full, B off

cycle:  LDI   R4, HOLD          ; ---- hold at D = 0: A full, B off
hold_a: CALL  pwm
        LOOP  R4, hold_a

up:     ADD   R1, STEP          ; ---- B brightens, A dims: D = STEP .. PERIOD
        LDI   R4, REP
up_rep: CALL  pwm
        LOOP  R4, up_rep
        CMP   R1, PERIOD
        BR    LO, up

        LDI   R4, HOLD          ; ---- hold at D = PERIOD: B full, A off
hold_b: CALL  pwm
        LOOP  R4, hold_b

down:   SUB   R1, STEP          ; ---- A brightens, B dims: D = PERIOD - STEP .. 0
        LDI   R4, REP
dn_rep: CALL  pwm
        LOOP  R4, dn_rep
        CMP   R1, 0
        BR    NZ, down
        JMP   cycle

; ---- one PWM period: B on for R1 cycles from the period start, then A until the next one
pwm:    MOV   R6, R5
        ADD   R6, R1            ; R6 = period start + D
        CMP   R1, 0
        BR    Z, a_wait         ; D = 0: A the whole period
        WAITT R5                ; period start
        CMP   R7, 1
        BR    Z, b_on
        WR    [R8+TGL], R9      ; A off, B on
        LDI   R7, 1
b_on:   CMP   R1, PERIOD
        BR    HS, p_end         ; D = PERIOD: B the whole period
a_wait: WAITT R6                ; period start + D
        CMP   R7, 0
        BR    Z, p_end
        WR    [R8+TGL], R9      ; B off, A on
        LDI   R7, 0
p_end:  ADD   R5, PERIOD        ; next period start (waited for in the next call)
        RET
