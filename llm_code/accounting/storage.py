"""記帳資料儲存"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from .types import Transaction, TransactionType, Category, Budget, Account


class AccountingStorage:
    """記帳資料儲存"""
    
    def __init__(self, data_dir: str = "~/.llm-code/accounting"):
        self.data_dir = Path(os.path.expanduser(data_dir))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self._transactions: Dict[str, Transaction] = {}
        self._categories: Dict[str, Category] = {}
        self._accounts: Dict[str, Account] = {}
        self._budgets: Dict[str, Budget] = {}
        
        self._load_data()
    
    def _get_file_path(self, filename: str) -> Path:
        return self.data_dir / f"{filename}.json"
    
    def _load_data(self):
        """載入資料"""
        self._load_transactions()
        self._load_categories()
        self._load_accounts()
        self._load_budgets()
    
    def _load_transactions(self):
        """載入交易記錄"""
        file_path = self._get_file_path("transactions")
        if not file_path.exists():
            return
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                transaction = Transaction(
                    id=item["id"],
                    amount=item["amount"],
                    type=TransactionType(item["type"]),
                    category_id=item["category_id"],
                    account_id=item["account_id"],
                    description=item.get("description", ""),
                    date=datetime.fromisoformat(item["date"]) if item.get("date") else datetime.now(),
                    tags=item.get("tags", []),
                    notes=item.get("notes", ""),
                )
                self._transactions[transaction.id] = transaction
    
    def _load_categories(self):
        """載入分類"""
        file_path = self._get_file_path("categories")
        if not file_path.exists():
            # 預設分類
            self._categories = self._get_default_categories()
            return
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                category = Category(
                    id=item["id"],
                    name=item["name"],
                    type=TransactionType(item["type"]),
                    icon=item.get("icon", ""),
                    parent_id=item.get("parent_id"),
                )
                self._categories[category.id] = category
    
    def _load_accounts(self):
        """載入帳戶"""
        file_path = self._get_file_path("accounts")
        if not file_path.exists():
            # 預設帳戶
            self._accounts = self._get_default_accounts()
            return
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                account = Account(
                    id=item["id"],
                    name=item["name"],
                    type=item["type"],
                    balance=item.get("balance", 0.0),
                    currency=item.get("currency", "TWD"),
                )
                self._accounts[account.id] = account
    
    def _load_budgets(self):
        """載入預算"""
        file_path = self._get_file_path("budgets")
        if not file_path.exists():
            return
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                budget = Budget(
                    id=item["id"],
                    category_id=item["category_id"],
                    amount=item["amount"],
                    period=item["period"],
                    start_date=datetime.fromisoformat(item["start_date"]),
                    end_date=datetime.fromisoformat(item["end_date"]) if item.get("end_date") else None,
                )
                self._budgets[budget.id] = budget
    
    def _save_transactions(self):
        """儲存交易記錄"""
        file_path = self._get_file_path("transactions")
        data = []
        for t in self._transactions.values():
            data.append({
                "id": t.id,
                "amount": t.amount,
                "type": t.type.value,
                "category_id": t.category_id,
                "account_id": t.account_id,
                "description": t.description,
                "date": t.date.isoformat(),
                "tags": t.tags,
                "notes": t.notes,
            })
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _save_categories(self):
        """儲存分類"""
        file_path = self._get_file_path("categories")
        data = []
        for c in self._categories.values():
            data.append({
                "id": c.id,
                "name": c.name,
                "type": c.type.value,
                "icon": c.icon,
                "parent_id": c.parent_id,
            })
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _save_accounts(self):
        """儲存帳戶"""
        file_path = self._get_file_path("accounts")
        data = []
        for a in self._accounts.values():
            data.append({
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "balance": a.balance,
                "currency": a.currency,
            })
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _save_budgets(self):
        """儲存預算"""
        file_path = self._get_file_path("budgets")
        data = []
        for b in self._budgets.values():
            data.append({
                "id": b.id,
                "category_id": b.category_id,
                "amount": b.amount,
                "period": b.period,
                "start_date": b.start_date.isoformat(),
                "end_date": b.end_date.isoformat() if b.end_date else None,
            })
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _get_default_categories(self) -> Dict[str, Category]:
        """預設分類"""
        categories = {
            "food": Category(id="food", name="餐飲", type=TransactionType.EXPENSE, icon="🍜"),
            "transport": Category(id="transport", name="交通", type=TransactionType.EXPENSE, icon="🚗"),
            "shopping": Category(id="shopping", name="購物", type=TransactionType.EXPENSE, icon="🛒"),
            "entertainment": Category(id="entertainment", name="娛樂", type=TransactionType.EXPENSE, icon="🎬"),
            "utilities": Category(id="utilities", name="水電", type=TransactionType.EXPENSE, icon="💡"),
            "salary": Category(id="salary", name="薪資", type=TransactionType.INCOME, icon="💰"),
            "investment": Category(id="investment", name="投資", type=TransactionType.INCOME, icon="📈"),
            "other_income": Category(id="other_income", name="其他收入", type=TransactionType.INCOME, icon="💵"),
        }
        return categories
    
    def _get_default_accounts(self) -> Dict[str, Account]:
        """預設帳戶"""
        return {
            "cash": Account(id="cash", name="現金", type="cash", balance=0.0),
            "bank": Account(id="bank", name="銀行帳戶", type="bank", balance=0.0),
            "credit": Account(id="credit", name="信用卡", type="credit", balance=0.0),
        }
    
    # Transaction methods
    def add_transaction(self, transaction: Transaction) -> Transaction:
        """新增交易"""
        self._transactions[transaction.id] = transaction
        self._save_transactions()
        return transaction
    
    def get_transaction(self, transaction_id: str) -> Optional[Transaction]:
        """取得交易"""
        return self._transactions.get(transaction_id)
    
    def get_transactions(self, 
                        start_date: Optional[datetime] = None,
                        end_date: Optional[datetime] = None,
                        category_id: Optional[str] = None,
                        account_id: Optional[str] = None) -> List[Transaction]:
        """查詢交易"""
        result = list(self._transactions.values())
        
        if start_date:
            result = [t for t in result if t.date >= start_date]
        if end_date:
            result = [t for t in result if t.date <= end_date]
        if category_id:
            result = [t for t in result if t.category_id == category_id]
        if account_id:
            result = [t for t in result if t.account_id == account_id]
        
        return sorted(result, key=lambda t: t.date, reverse=True)
    
    def update_transaction(self, transaction: Transaction) -> Transaction:
        """更新交易"""
        self._transactions[transaction.id] = transaction
        self._save_transactions()
        return transaction
    
    def delete_transaction(self, transaction_id: str) -> bool:
        """刪除交易"""
        if transaction_id in self._transactions:
            del self._transactions[transaction_id]
            self._save_transactions()
            return True
        return False
    
    # Category methods
    def add_category(self, category: Category) -> Category:
        """新增分類"""
        self._categories[category.id] = category
        self._save_categories()
        return category
    
    def get_categories(self, type: Optional[TransactionType] = None) -> List[Category]:
        """取得分類"""
        if type:
            return [c for c in self._categories.values() if c.type == type]
        return list(self._categories.values())
    
    # Account methods
    def add_account(self, account: Account) -> Account:
        """新增帳戶"""
        self._accounts[account.id] = account
        self._save_accounts()
        return account
    
    def get_accounts(self) -> List[Account]:
        """取得帳戶"""
        return list(self._accounts.values())
    
    def update_account_balance(self, account_id: str, balance: float) -> bool:
        """更新帳戶餘額"""
        if account_id in self._accounts:
            self._accounts[account_id].balance = balance
            self._save_accounts()
            return True
        return False
    
    # Budget methods
    def add_budget(self, budget: Budget) -> Budget:
        """新增預算"""
        self._budgets[budget.id] = budget
        self._save_budgets()
        return budget
    
    def get_budgets(self, category_id: Optional[str] = None) -> List[Budget]:
        """取得預算"""
        if category_id:
            return [b for b in self._budgets.values() if b.category_id == category_id]
        return list(self._budgets.values())
