"""記帳系統類型定義"""
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
    parent_id: Optional[str] = None


@dataclass
class Account:
    """帳戶"""
    id: str
    name: str
    type: str  # 現金、銀行、信用卡等
    balance: float = 0.0
    currency: str = "TWD"


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
    tags: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Budget:
    """預算"""
    id: str
    category_id: str
    amount: float
    period: str  # monthly, weekly, yearly
    start_date: datetime
    end_date: Optional[datetime] = None
