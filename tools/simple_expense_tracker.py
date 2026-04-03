#!/usr/bin/env python3
"""簡單記帳工具"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class Expense:
    """支出記錄"""
    id: int
    date: str
    category: str
    amount: float
    note: str = ""
    

@dataclass
class Income:
    """收入記錄"""
    id: int
    date: str
    source: str
    amount: float
    note: str = ""


class ExpenseTracker:
    """簡單記帳系統"""
    
    CATEGORIES = {
        "expense": [
            "飲食", "交通", "購物", "娛樂", "居住", 
            "醫療", "教育", "其他"
        ],
        "income": [
            "薪水", "獎金", "兼職", "投資", "禮物", "其他"
        ]
    }
    
    def __init__(self, data_file: str = "expenses.json"):
        self.data_file = Path(data_file)
        self._next_expense_id = 1
        self._next_income_id = 1
        self._load_data()
    
    def _load_data(self):
        """載入資料"""
        if self.data_file.exists():
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.expenses = [Expense(**e) for e in data.get("expenses", [])]
                self.incomes = [Income(**i) for i in data.get("incomes", [])]
                self._next_expense_id = data.get("next_expense_id", 1)
                self._next_income_id = data.get("next_income_id", 1)
        else:
            self.expenses: List[Expense] = []
            self.incomes: List[Income] = []
    
    def _save_data(self):
        """儲存資料"""
        data = {
            "expenses": [asdict(e) for e in self.expenses],
            "incomes": [asdict(i) for i in self.incomes],
            "next_expense_id": self._next_expense_id,
            "next_income_id": self._next_income_id
        }
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def add_expense(self, amount: float, category: str, note: str = "", date: str = None) -> Expense:
        """新增支出"""
        if category not in self.CATEGORIES["expense"]:
            print(f"警告：類別 '{category}' 不存在，已自動新增")
            self.CATEGORIES["expense"].append(category)
        
        expense = Expense(
            id=self._next_expense_id,
            date=date or datetime.now().strftime("%Y-%m-%d"),
            category=category,
            amount=amount,
            note=note
        )
        self.expenses.append(expense)
        self._next_expense_id += 1
        self._save_data()
        return expense
    
    def add_income(self, amount: float, source: str, note: str = "", date: str = None) -> Income:
        """新增收入"""
        if source not in self.CATEGORIES["income"]:
            print(f"警告：來源 '{source}' 不存在，已自動新增")
            self.CATEGORIES["income"].append(source)
        
        income = Income(
            id=self._next_income_id,
            date=date or datetime.now().strftime("%Y-%m-%d"),
            source=source,
            amount=amount,
            note=note
        )
        self.incomes.append(income)
        self._next_income_id += 1
        self._save_data()
        return income
    
    def list_expenses(self, limit: int = 10) -> List[Expense]:
        """列出最近支出"""
        return sorted(self.expenses, key=lambda x: x.date, reverse=True)[:limit]
    
    def list_incomes(self, limit: int = 10) -> List[Income]:
        """列出最近收入"""
        return sorted(self.incomes, key=lambda x: x.date, reverse=True)[:limit]
    
    def get_summary(self, start_date: str = None, end_date: str = None) -> Dict:
        """取得統計摘要"""
        # 篩選日期範圍
        expenses = self.expenses
        incomes = self.incomes
        
        if start_date:
            expenses = [e for e in expenses if e.date >= start_date]
            incomes = [i for i in incomes if i.date >= start_date]
        if end_date:
            expenses = [e for e in expenses if e.date <= end_date]
            incomes = [i for i in incomes if i.date <= end_date]
        
        # 計算總額
        total_expense = sum(e.amount for e in expenses)
        total_income = sum(i.amount for e in incomes)
        
        # 分類統計
        expense_by_category = {}
        for e in expenses:
            expense_by_category[e.category] = expense_by_category.get(e.category, 0) + e.amount
        
        income_by_source = {}
        for i in incomes:
            income_by_source[i.source] = income_by_source.get(i.source, 0) + i.amount
        
        return {
            "total_expense": total_expense,
            "total_income": total_income,
            "balance": total_income - total_expense,
            "expense_by_category": expense_by_category,
            "income_by_source": income_by_source,
            "expense_count": len(expenses),
            "income_count": len(incomes)
        }
    
    def delete_expense(self, expense_id: int) -> bool:
        """刪除支出記錄"""
        for i, e in enumerate(self.expenses):
            if e.id == expense_id:
                self.expenses.pop(i)
                self._save_data()
                return True
        return False
    
    def delete_income(self, income_id: int) -> bool:
        """刪除收入記錄"""
        for i, i in enumerate(self.incomes):
            if i.id == income_id:
                self.incomes.pop(i)
                self._save_data()
                return True
        return False
    
    def get_categories(self) -> Dict:
        """取得所有分類"""
        return self.CATEGORIES.copy()


def main():
    """主程式 - 互動式介面"""
    tracker = ExpenseTracker()
    
    while True:
        print("\n" + "=" * 40)
        print("  簡單記帳工具")
        print("=" * 40)
        print("1. 新增支出")
        print("2. 新增收入")
        print("3. 查看最近支出")
        print("4. 查看最近收入")
        print("5. 統計摘要")
        print("6. 查看分類")
        print("7. 刪除支出記錄")
        print("8. 刪除收入記錄")
        print("0. 離開")
        print("-" * 40)
        
        choice = input("請選擇功能 (0-8): ").strip()
        
        if choice == "0":
            print("再見！")
            break
        
        elif choice == "1":
            print("\n支出類別:", ", ".join(tracker.CATEGORIES["expense"]))
            amount = float(input("金額: "))
            category = input("類別: ").strip()
            note = input("備註 (選填): ").strip()
            expense = tracker.add_expense(amount, category, note)
            print(f"✓ 已記錄支出: {expense.amount} {expense.category}")
        
        elif choice == "2":
            print("\n收入來源:", ", ".join(tracker.CATEGORIES["income"]))
            amount = float(input("金額: "))
            source = input("來源: ").strip()
            note = input("備註 (選填): ").strip()
            income = tracker.add_income(amount, source, note)
            print(f"✓ 已記錄收入: {income.amount} {income.source}")
        
        elif choice == "3":
            print("\n【最近支出】")
            for e in tracker.list_expenses(10):
                print(f"  {e.date} | {e.category:8} | {e.amount:>10.2f} | {e.note}")
        
        elif choice == "4":
            print("\n【最近收入】")
            for i in tracker.list_incomes(10):
                print(f"  {i.date} | {i.source:8} | {i.amount:>10.2f} | {i.note}")
        
        elif choice == "5":
            print("\n【統計摘要】")
            summary = tracker.get_summary()
            print(f"  總收入:     {summary['total_income']:>12.2f}")
            print(f"  總支出:     {summary['total_expense']:>12.2f}")
            print(f"  淨餘額:     {summary['balance']:>12.2f}")
            print(f"  支出筆數:   {summary['expense_count']}")
            print(f"  收入筆數:   {summary['income_count']}")
            
            if summary["expense_by_category"]:
                print("\n  支出分類:")
                for cat, amount in sorted(summary["expense_by_category"].items(), key=lambda x: -x[1]):
                    print(f"    {cat:10}: {amount:>10.2f}")
            
            if summary["income_by_source"]:
                print("\n  收入來源:")
                for src, amount in sorted(summary["income_by_source"].items(), key=lambda x: -x[1]):
                    print(f"    {src:10}: {amount:>10.2f}")
        
        elif choice == "6":
            print("\n【支出類別】", ", ".join(tracker.CATEGORIES["expense"]))
            print("【收入來源】", ", ".join(tracker.CATEGORIES["income"]))
        
        elif choice == "7":
            expense_id = int(input("要刪除的支出 ID: "))
            if tracker.delete_expense(expense_id):
                print(f"✓ 已刪除支出 ID {expense_id}")
            else:
                print(f"✗ 找不到支出 ID {expense_id}")
        
        elif choice == "8":
            income_id = int(input("要刪除的收入 ID: "))
            if tracker.delete_income(income_id):
                print(f"✓ 已刪除收入 ID {income_id}")
            else:
                print(f"✗ 找不到收入 ID {income_id}")
        
        else:
            print("無效的選擇，請重新輸入")


if __name__ == "__main__":
    main()
