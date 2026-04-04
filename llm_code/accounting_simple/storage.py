"""簡易記帳資料儲存"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from .types import Transaction, TransactionType, Category, Budget, Account


class AccountingStorage:
    """簡易記帳資料儲存"""
    
    def __init__(self, db_path: str = "~/.llm-code/accounting_simple.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """初始化資料庫"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                balance REAL DEFAULT 0
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                icon TEXT DEFAULT ''
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                amount REAL NOT NULL,
                type TEXT NOT NULL,
                category_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                description TEXT,
                date TEXT NOT NULL,
                notes TEXT,
                FOREIGN KEY (category_id) REFERENCES categories(id),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL,
                amount REAL NOT NULL,
                period TEXT DEFAULT 'monthly',
                FOREIGN KEY (category_id) REFERENCES categories(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    # Accounts
    def add_account(self, account: Account):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO accounts (id, name, balance) VALUES (?, ?, ?)",
            (account.id, account.name, account.balance)
        )
        conn.commit()
        conn.close()
    
    def get_account(self, account_id: str) -> Optional[Account]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return Account(
                id=row['id'],
                name=row['name'],
                balance=row['balance']
            )
        return None
    
    def get_all_accounts(self) -> List[Account]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts")
        rows = cursor.fetchall()
        conn.close()
        return [Account(id=r['id'], name=r['name'], balance=r['balance']) for r in rows]
    
    def update_account_balance(self, account_id: str, balance: float):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET balance = ? WHERE id = ?", (balance, account_id))
        conn.commit()
        conn.close()
    
    # Categories
    def add_category(self, category: Category):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO categories (id, name, type, icon) VALUES (?, ?, ?, ?)",
            (category.id, category.name, category.type.value, category.icon)
        )
        conn.commit()
        conn.close()
    
    def get_category(self, category_id: str) -> Optional[Category]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return Category(
                id=row['id'],
                name=row['name'],
                type=TransactionType(row['type']),
                icon=row['icon']
            )
        return None
    
    def get_categories_by_type(self, type: TransactionType) -> List[Category]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories WHERE type = ?", (type.value,))
        rows = cursor.fetchall()
        conn.close()
        return [Category(id=r['id'], name=r['name'], type=TransactionType(r['type']), icon=r['icon']) for r in rows]
    
    # Transactions
    def add_transaction(self, transaction: Transaction):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO transactions (id, amount, type, category_id, account_id, description, date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (transaction.id, transaction.amount, transaction.type.value, transaction.category_id,
             transaction.account_id, transaction.description, transaction.date.isoformat(), transaction.notes)
        )
        conn.commit()
        conn.close()
    
    def get_transaction(self, transaction_id: str) -> Optional[Transaction]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return Transaction(
                id=row['id'],
                amount=row['amount'],
                type=TransactionType(row['type']),
                category_id=row['category_id'],
                account_id=row['account_id'],
                description=row['description'],
                date=datetime.fromisoformat(row['date']),
                notes=row['notes']
            )
        return None
    
    def get_transactions(self, limit: int = 100, offset: int = 0) -> List[Transaction]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions ORDER BY date DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            Transaction(
                id=r['id'],
                amount=r['amount'],
                type=TransactionType(r['type']),
                category_id=r['category_id'],
                account_id=r['account_id'],
                description=r['description'],
                date=datetime.fromisoformat(r['date']),
                notes=r['notes']
            )
            for r in rows
        ]
    
    def get_transactions_by_date_range(self, start: datetime, end: datetime) -> List[Transaction]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM transactions WHERE date >= ? AND date <= ? ORDER BY date DESC""",
            (start.isoformat(), end.isoformat())
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            Transaction(
                id=r['id'],
                amount=r['amount'],
                type=TransactionType(r['type']),
                category_id=r['category_id'],
                account_id=r['account_id'],
                description=r['description'],
                date=datetime.fromisoformat(r['date']),
                notes=r['notes']
            )
            for r in rows
        ]
    
    # Budgets
    def add_budget(self, budget: Budget):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO budgets (id, category_id, amount, period) VALUES (?, ?, ?, ?)",
            (budget.id, budget.category_id, budget.amount, budget.period)
        )
        conn.commit()
        conn.close()
    
    def get_budget(self, budget_id: str) -> Optional[Budget]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM budgets WHERE id = ?", (budget_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return Budget(
                id=row['id'],
                category_id=row['category_id'],
                amount=row['amount'],
                period=row['period']
            )
        return None
    
    def get_budgets_by_category(self, category_id: str) -> List[Budget]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM budgets WHERE category_id = ?", (category_id,))
        rows = cursor.fetchall()
        conn.close()
        return [Budget(id=r['id'], category_id=r['category_id'], amount=r['amount'], period=r['period']) for r in rows]
