"""記帳引擎"""
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from .types import Transaction, TransactionType, Category, Budget, Account
from .storage import AccountingStorage


class AccountingEngine:
    """記帳引擎"""
    
    def __init__(self, storage: Optional[AccountingStorage] = None):
        self.storage = storage or AccountingStorage()
    
    def add_transaction(
        self,
        amount: float,
        transaction_type: TransactionType,
        category_id: str,
        account_id: str,
        description: str = "",
        date: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        notes: str = ""
    ) -> Transaction:
        """新增交易記錄"""
        transaction = Transaction(
            id=str(uuid.uuid4()),
            amount=amount,
            type=transaction_type,
            category_id=category_id,
            account_id=account_id,
            description=description,
            date=date or datetime.now(),
            tags=tags or [],
            notes=notes,
        )
        
        # 更新帳戶餘額
        self._update_account_balance(transaction)
        
        return self.storage.add_transaction(transaction)
    
    def _update_account_balance(self, transaction: Transaction):
        """更新帳戶餘額"""
        account = self.storage._accounts.get(transaction.account_id)
        if not account:
            return
        
        if transaction.type == TransactionType.INCOME:
            account.balance += transaction.amount
        else:
            account.balance -= transaction.amount
        
        self.storage._save_accounts()
    
    def get_summary(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """取得摘要統計"""
        transactions = self.storage.get_transactions(
            start_date=start_date,
            end_date=end_date
        )
        
        total_income = 0.0
        total_expense = 0.0
        category_summary: Dict[str, float] = {}
        
        for t in transactions:
            if t.type == TransactionType.INCOME:
                total_income += t.amount
            else:
                total_expense += t.amount
                category_summary[t.category_id] = category_summary.get(t.category_id, 0.0) + t.amount
        
        return {
            "total_income": total_income,
            "total_expense": total_expense,
            "net": total_income - total_expense,
            "category_breakdown": category_summary,
            "transaction_count": len(transactions),
        }
    
    def get_monthly_summary(self, year: int, month: int) -> Dict:
        """取得月度統計"""
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = datetime(year, month + 1, 1) - timedelta(days=1)
        
        return self.get_summary(start_date, end_date)
    
    def get_category_expenses(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, float]:
        """取得分類支出"""
        transactions = self.storage.get_transactions(
            start_date=start_date,
            end_date=end_date,
        )
        
        category_expenses: Dict[str, float] = {}
        for t in transactions:
            if t.type == TransactionType.EXPENSE:
                category_expenses[t.category_id] = category_expenses.get(t.category_id, 0.0) + t.amount
        
        return category_expenses
    
    def check_budget(
        self,
        category_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Tuple[float, float, bool]:
        """檢查預算使用情況
        
        回傳：(已使用金額，預算金額，是否超支)
        """
        budgets = self.storage.get_budgets(category_id)
        if not budgets:
            return (0.0, 0.0, False)
        
        # 找最適合的預算
        budget = None
        for b in budgets:
            if b.start_date <= (start_date or datetime.now()) <= (b.end_date or datetime.now()):
                budget = b
                break
        
        if not budget:
            return (0.0, 0.0, False)
        
        spent = sum(
            t.amount for t in self.storage.get_transactions(
                start_date=start_date,
                end_date=end_date,
                category_id=category_id,
            )
            if t.type == TransactionType.EXPENSE
        )
        
        return (spent, budget.amount, spent > budget.amount)
    
    def get_accounts_summary(self) -> Dict[str, float]:
        """取得帳戶總覽"""
        return {
            account.id: account.balance 
            for account in self.storage.get_accounts()
        }
    
    def get_total_balance(self) -> float:
        """取得總餘額"""
        return sum(
            account.balance 
            for account in self.storage.get_accounts()
        )
