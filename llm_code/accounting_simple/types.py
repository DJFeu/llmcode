"""簡易記帳系統類型定義"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class TransactionType(Enum):
    """交易類型"""
    INCOME = "收入"
    EXPENSE = "支出"


@dataclass
class Category:
    """分類"""
    id: str
    name: str
    type: TransactionType
    icon: str = ""


@dataclass
class Account:
    """帳戶"""
    id: str
    name: str
    balance: float = 0.0


@dataclass
class Transaction:
    """交易記錄"""
    id: str
    amount: float
    type: TransactionType
    category_id: str
    account_id: str
    description: str = ""
    date: datetime = field(default_factory=datetime.now)
    notes: str = ""


@dataclass
class Budget:
    """預算"""
    id: str
    category_id: str
    amount: float
    period: str = "monthly"


@dataclass
class Summary:
    """統計摘要"""
    total_income: float = 0.0
    total_expense: float = 0.0
    balance: float = 0.0
    transaction_count: int = 0
