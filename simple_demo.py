#!/usr/bin/env python3
"""一個簡單的 Python 示範程式"""

def greet(name: str) -> str:
    """打招呼的函式"""
    return f"Hello, {name}! 歡迎使用 Python!"

def fibonacci(n: int) -> list:
    """生成前 n 個斐波那契數"""
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    
    fib = [0, 1]
    for i in range(2, n):
        fib.append(fib[i-1] + fib[i-2])
    return fib

def main():
    print("=" * 40)
    print("Python 簡單示範程式 (新版本)")
    print("=" * 40)
    
    # 1. 打招呼
    name = "世界"
    print(greet(name))
    print()
    
    # 2. 斐波那契數列
    print("前 15 個斐波那契數:")
    fib_list = fibonacci(15)
    print(fib_list)
    print()
    
    # 3. 簡單的計算
    numbers = [10, 20, 30, 40, 50, 60]
    total = sum(numbers)
    average = total / len(numbers)
    print(f"數字列表: {numbers}")
    print(f"總和: {total}")
    print(f"平均值: {average:.2f}")
    print()
    
    # 4. 字典操作
    person = {
        "name": "Bob",
        "age": 30,
        "city": "Tokyo",
        "hobby": "Programming"
    }
    print("個人資訊:")
    for key, value in person.items():
        print(f"  {key}: {value}")
    
    # 5. 新增: 顏色列表
    colors = ["Red", "Green", "Blue", "Yellow", "Purple"]
    print(f"\n顏色清單 ({len(colors)} 種):")
    for i, color in enumerate(colors, 1):
        print(f"  {i}. {color}")
    
    print("\n" + "=" * 40)
    print("程式執行完成!")

if __name__ == "__main__":
    main()
