#!/usr/bin/env python3
# 99 乘法表

for i in range(1, 10):
    for j in range(1, i + 1):
        print(f"{j}x{i}={i*j:2d} ", end="")
    print()
