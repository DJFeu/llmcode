#!/usr/bin/env python3
"""記帳工具示範"""

from simple_expense_tracker import ExpenseTracker

# 建立記帳系統
tracker = ExpenseTracker("demo_expenses.json")

# 新增支出
tracker.add_expense(150, "飲食", "早餐")
tracker.add_expense(500, "交通", "捷運")
tracker.add_expense(1200, "購物", "衣服")
tracker.add_expense(300, "飲食", "午餐")
tracker.add_expense(800, "娛樂", "電影")

# 新增收入
tracker.add_income(50000, "薪水", "12 月薪水")
tracker.add_income(5000, "兼職", "設計案")

# 查看記錄
print("最近支出:")
for e in tracker.list_expenses(5):
    print(f"  {e.date} | {e.category:8} | {e.amount:>10.2f} | {e.note}")

print("\n最近收入:")
for i in tracker.list_incomes(5):
    print(f"  {i.date} | {i.source:8} | {i.amount:>10.2f} | {i.note}")

# 統計摘要
print("\n【統計摘要】")
summary = tracker.get_summary()
print(f"  總收入:     {summary['total_income']:>12.2f}")
print(f"  總支出:     {summary['total_expense']:>12.2f}")
print(f"  淨餘額:     {summary['balance']:>12.2f}")

print("\n支出分類:")
for cat, amount in sorted(summary["expense_by_category"].items(), key=lambda x: -x[1]):
    print(f"  {cat:10}: {amount:>10.2f}")

print("\n收入來源:")
for src, amount in sorted(summary["income_by_source"].items(), key=lambda x: -x[1]):
    print(f"  {src:10}: {amount:>10.2f}")
