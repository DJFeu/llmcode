# Python 基礎教學
# =====================

# 1. 變數與資料類型
print("=== 變數與資料類型 ===")

name = "Alice"           # 字串 (str)
age = 25                 # 整數 (int)
height = 1.75            # 浮點數 (float)
is_student = True        # 布林值 (bool)

print(f"Name: {name}, Type: {type(name)}")
print(f"Age: {age}, Type: {type(age)}")
print(f"Height: {height}, Type: {type(height)}")
print(f"Is Student: {is_student}, Type: {type(is_student)}")

# 2. 基本運算
print("\n=== 基本運算 ===")
a = 10
b = 3

print(f"{a} + {b} = {a + b}")
print(f"{a} - {b} = {a - b}")
print(f"{a} * {b} = {a * b}")
print(f"{a} / {b} = {a / b}")
print(f"{a} // {b} = {a // b}")  # 整數除法
print(f"{a} % {b} = {a % b}")    # 餘數
print(f"{a} ** {b} = {a ** b}")  # 冪運算

# 3. 條件判斷
print("\n=== 條件判斷 ===")
x = 15

if x > 20:
    print(f"{x} 大於 20")
elif x > 10:
    print(f"{x} 大於 10 但小於等於 20")
else:
    print(f"{x} 小於等於 10")

# 4. 迴圈
print("\n=== 迴圈 ===")

# for 迴圈
print("For 迴圈 (0-4):")
for i in range(5):
    print(f"  {i}")

# while 迴圈
print("\nWhile 迴圈 (0-4):")
count = 0
while count < 5:
    print(f"  {count}")
    count += 1

# 5. 列表 (List)
print("\n=== 列表 ===")
fruits = ["apple", "banana", "orange"]

print(f"列表: {fruits}")
print(f"第一個元素: {fruits[0]}")
print(f"最後一個元素: {fruits[-1]}")
print(f"列表長度: {len(fruits)}")

fruits.append("grape")
print(f"加入 grape 後: {fruits}")

# 6. 字典 (Dictionary)
print("\n=== 字典 ===")
person = {
    "name": "John",
    "age": 30,
    "city": "Taipei"
}

print(f"字典: {person}")
print(f"Name: {person['name']}")
print(f"Age: {person['age']}")

person["job"] = "Engineer"
print(f"加入 job 後: {person}")

# 7. 函數
print("\n=== 函數 ===")

def greet(name):
    return f"Hello, {name}!"

def add(a, b):
    return a + b

def greet_with_default(name, greeting="Hello"):
    return f"{greeting}, {name}!"

print(greet("Alice"))
print(f"5 + 3 = {add(5, 3)}")
print(greet_with_default("Bob"))
print(greet_with_default("Bob", "Hi"))

# 8. 類別 (Class)
print("\n=== 類別 ===")

class Dog:
    def __init__(self, name, age):
        self.name = name
        self.age = age
    
    def bark(self):
        return f"{self.name} says: Woof!"
    
    def get_human_age(self):
        return self.age * 7

my_dog = Dog("Buddy", 3)
print(f"狗狗名字: {my_dog.name}")
print(f"狗狗年齡: {my_dog.age}")
print(my_dog.bark())
print(f"人類年齡: {my_dog.get_human_age()}")

# 9. 檔案操作
print("\n=== 檔案操作 ===")

# 寫入檔案
with open("test.txt", "w", encoding="utf-8") as f:
    f.write("Hello, World!\n")
    f.write("這是一個測試檔案。\n")

# 讀取檔案
with open("test.txt", "r", encoding="utf-8") as f:
    content = f.read()
    print("檔案內容:")
    print(content)

# 10. 異常處理
print("\n=== 異常處理 ===")

def safe_divide(a, b):
    try:
        result = a / b
        return f"結果: {result}"
    except ZeroDivisionError:
        return "錯誤：不能除以零！"
    except TypeError:
        return "錯誤：型別錯誤！"
    finally:
        print("執行完成")

print(safe_divide(10, 2))
print(safe_divide(10, 0))
print(safe_divide(10, "a"))

print("\n=== 結束 ===")
print("感謝學習 Python 基礎！")
