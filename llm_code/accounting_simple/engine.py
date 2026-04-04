"""簡易記帳引擎"""
import uuid
from datetime import datetime
from typing import List, Optional
from .types import Transaction, TransactionType, Category, Budget, Account, Summary
from .storage import AccountingStorage


class AccountingEngine:
    """簡易記帳引擎"""
    
    def __init__(self, storage: Optional[AccountingStorage] = None):
        self.storage = storage or AccountingStorage()
    
    # Account operations
    def create_account(self, name: str, balance: float = 0.0) -> Account:
        """建立帳戶"""
        account = Account(
            id=str(uuid.uuid4()),
            name=name,
            balance=balance
        )
        self.storage.add_account(account)
        return account
    
    def get_account(self, account_id: str) -> Optional[Account]:
        """取得帳戶"""
        return self.storage.get_account(account_id)
    
    def get_all_accounts(self) -> List[Account]:
        """取得所有帳戶"""
        return self.storage.get_all_accounts()
    
    # Category operations
    def create_category(self, name: str, type: TransactionType, icon: str = "") -> Category:
        """建立分類"""
        category = Category(
            id=str(uuid.uuid4()),
            name=name,
            type=type,
            icon=icon
        )
        self.storage.add_category(category)
        return category
    
    def get_categories(self, type: Optional[TransactionType] = None) -> List[Category]:
        """取得分類"""
        if type:
            return self.storage.get_categories_by_type(type)
        return self.storage.get_categories_by_type(TransactionType.EXPENSE) + \
               self.storage.get_categories_by_type(TransactionType.INCOME)
    
    # Transaction operations
    def add_transaction(
        self,
        amount: float,
        transaction_type: TransactionType,
        category_id: str,
        account_id: str,
        description: str = "",
        date: Optional[datetime] = None,
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
            notes=notes
        )
        self.storage.add_transaction(transaction)
        
        # 更新帳戶餘額
        account = self.storage.get_account(account_id)
        if account:
            if transaction_type == TransactionType.INCOME:
                account.balance += amount
            else:
                account.balance -= amount
            self.storage.update_account_balance(account_id, account.balance)
        
        return transaction
    
    def get_transaction(self, transaction_id: str) -> Optional[Transaction]:
        """取得交易記錄"""
        return self.storage.get_transaction(transaction_id)
    
    def get_transactions(self, limit: int = 100, offset: int = 0) -> List[Transaction]:
        """取得交易記錄列表"""
        return self.storage.get_transactions(limit, offset)
    
    def get_transactions_by_date_range(self, start: datetime, end: datetime) -> List[Transaction]:
        """取得指定日期範圍的交易"""
        return self.storage.get_transactions_by_date_range(start, end)
    
    # Budget operations
    def create_budget(self, category_id: str, amount: float, period: str = "monthly") -> Budget:
        """建立預算"""
        budget = Budget(
            id=str(uuid.uuid4()),
            category_id=category_id,
            amount=amount,
            period=period
        )
        self.storage.add_budget(budget)
        return budget
    
    def get_budgets(self, category_id: Optional[str] = None) -> List[Budget]:
        """取得預算"""
        if category_id:
            return self.storage.get_budgets_by_category(category_id)
        return []  # 簡化版本暫不支援取得所有預算
    
    # Summary
    def get_summary(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> Summary:
        """取得統計摘要"""
        if start and end:
            transactions = self.storage.get_transactions_by_date_range(start, end)
        else:
            transactions = self.storage.get_transactions(limit=10000)
        
        total_income = sum(t.amount for t in transactions if t.type == TransactionType.INCOME)
        total_expense = sum(t.amount for t in transactions if t.type == TransactionType.EXPENSE)
        
        return Summary(
            total_income=total_income,
            total_expense=total_expense,
            balance=total_income - total_expense,
            transaction_count=len(transactions)
        )
