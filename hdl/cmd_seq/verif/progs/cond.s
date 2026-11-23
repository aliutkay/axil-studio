; every condition after CMP; taken branches set a bit (R1, R4, R6)
        LDI  R2, 0x7FFFFFFF
        LDI  R3, 0xFFFFFFFF
        LDI  R5, 5
        CMP  R2, R3
        BR   AL, t1
        JMP  n1
t1:     OR   R1, 1
n1:
        CMP  R2, R3
        BR   Z, t2
        JMP  n2
t2:     OR   R1, 2
n2:
        CMP  R2, R3
        BR   NZ, t3
        JMP  n3
t3:     OR   R1, 4
n3:
        CMP  R2, R3
        BR   LT, t4
        JMP  n4
t4:     OR   R1, 8
n4:
        CMP  R2, R3
        BR   GE, t5
        JMP  n5
t5:     OR   R1, 16
n5:
        CMP  R2, R3
        BR   ERR, t6
        JMP  n6
t6:     OR   R1, 32
n6:
        CMP  R2, R3
        BR   LO, t7
        JMP  n7
t7:     OR   R1, 64
n7:
        CMP  R2, R3
        BR   HS, t8
        JMP  n8
t8:     OR   R1, 128
n8:
        CMP  R2, R3
        BR   MI, t9
        JMP  n9
t9:     OR   R1, 256
n9:
        CMP  R2, R3
        BR   PL, t10
        JMP  n10
t10:     OR   R1, 512
n10:
        CMP  R5, R5
        BR   AL, t11
        JMP  n11
t11:     OR   R4, 1
n11:
        CMP  R5, R5
        BR   Z, t12
        JMP  n12
t12:     OR   R4, 2
n12:
        CMP  R5, R5
        BR   NZ, t13
        JMP  n13
t13:     OR   R4, 4
n13:
        CMP  R5, R5
        BR   LT, t14
        JMP  n14
t14:     OR   R4, 8
n14:
        CMP  R5, R5
        BR   GE, t15
        JMP  n15
t15:     OR   R4, 16
n15:
        CMP  R5, R5
        BR   ERR, t16
        JMP  n16
t16:     OR   R4, 32
n16:
        CMP  R5, R5
        BR   LO, t17
        JMP  n17
t17:     OR   R4, 64
n17:
        CMP  R5, R5
        BR   HS, t18
        JMP  n18
t18:     OR   R4, 128
n18:
        CMP  R5, R5
        BR   MI, t19
        JMP  n19
t19:     OR   R4, 256
n19:
        CMP  R5, R5
        BR   PL, t20
        JMP  n20
t20:     OR   R4, 512
n20:
        CMP  R3, R2
        BR   AL, t21
        JMP  n21
t21:     OR   R6, 1
n21:
        CMP  R3, R2
        BR   Z, t22
        JMP  n22
t22:     OR   R6, 2
n22:
        CMP  R3, R2
        BR   NZ, t23
        JMP  n23
t23:     OR   R6, 4
n23:
        CMP  R3, R2
        BR   LT, t24
        JMP  n24
t24:     OR   R6, 8
n24:
        CMP  R3, R2
        BR   GE, t25
        JMP  n25
t25:     OR   R6, 16
n25:
        CMP  R3, R2
        BR   ERR, t26
        JMP  n26
t26:     OR   R6, 32
n26:
        CMP  R3, R2
        BR   LO, t27
        JMP  n27
t27:     OR   R6, 64
n27:
        CMP  R3, R2
        BR   HS, t28
        JMP  n28
t28:     OR   R6, 128
n28:
        CMP  R3, R2
        BR   MI, t29
        JMP  n29
t29:     OR   R6, 256
n29:
        CMP  R3, R2
        BR   PL, t30
        JMP  n30
t30:     OR   R6, 512
n30:
        END
