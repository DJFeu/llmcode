"""記帳 CLI 工具"""
import argparse
import sys
from datetime import datetime
from typing import Optional
from .types import TransactionType
from .engine import AccountingEngine


def parse_date(date_str: str) -> datetime:
    """解析日期字串"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y/%m/%d")
        except ValueError:
            return datetime.now()


def cmd_add(args, engine: AccountingEngine):
    """新增交易"""
    transaction_type = TransactionType.INCOME if args.type == "income" else TransactionType.EXPENSE
    
    transaction = engine.add_transaction(
        amount=args.amount,
        transaction_type=transaction_type,
        category_id=args.category,
        account_id=args.account,
        description=args.description or "",
        date=parse_date(args.date) if args.date else None,
        tags=args.tags.split(",") if args.tags else None,
        notes=args.notes or "",
    )
    
    category = engine.storage._categories.get(transaction.category_id)
    category_name = category.name if category else transaction.category_id
    
    print(f"✓ 已新增交易")
    print(f"  金額: {transaction.amount}")
    print(f"  類型: {transaction.type.value}")
    print(f"  分類: {category_name}")
    print(f"  帳戶: {transaction.account_id}")
    if transaction.description:
        print(f"  說明: {transaction.description}")


def cmd_list(args, engine: AccountingEngine):
    """列出交易"""
    start_date = parse_date(args.start_date) if args.start_date else None
    end_date = parse_date(args.end_date) if args.end_date else None
    
    transactions = engine.storage.get_transactions(
        start_date=start_date,
        end_date=end_date,
        category_id=args.category,
        account_id=args.account,
    )
    
    if not transactions:
        print("沒有交易記錄")
        return
    
    print(f"{'日期':<12} {'類型':<6} {'金額':>10} {'分類':<12} {'說明'}")
    print("-" * 60)
    
    for t in transactions[:args.limit]:
        date_str = t.date.strftime("%Y-%m-%d")
        type_str = "收入" if t.type == TransactionType.INCOME else "支出"
        amount_str = f"+{t.amount}" if t.type == TransactionType.INCOME else f"-{t.amount}"
        
        category = engine.storage._categories.get(t.category_id)
        category_name = category.name if category else t.category_id
        
        desc = t.description[:20] if t.description else "-"
        
        print(f"{date_str:<12} {type_str:<6} {amount_str:>10} {category_name:<12} {desc}")
    
    print(f"\n共 {len(transactions)} 筆交易")


def cmd_summary(args, engine: AccountingEngine):
    """統計摘要"""
    summary = engine.get_summary()
    
    print("╔════════════════════════════════════════╗")
    print("║           記帳統計摘要                  ║")
    print("╠════════════════════════════════════════╣")
    print(f"║ 總收入:      {summary['total_income']:>10}  ║")
    print(f"║ 總支出:      {summary['total_expense']:>10}  ║")
    print(f"║ 淨額:        {summary['net']:>10}  ║")
    print(f"║ 交易筆數:    {summary['transaction_count']:>10}  ║")
    print("╚════════════════════════════════════════╝")
    
    if summary['category_breakdown']:
        print("\n支出分類:")
        for cat_id, amount in sorted(summary['category_breakdown'].items(), key=lambda x: -x[1]):
            category = engine.storage._categories.get(cat_id)
            cat_name = category.name if category else cat_id
            percentage = (amount / summary['total_expense'] * 100) if summary['total_expense'] > 0 else 0
            print(f"  {cat_name}: {amount:.2f} ({percentage:.1f}%)")


def cmd_balance(args, engine: AccountingEngine):
    """帳戶餘額"""
    accounts = engine.storage.get_accounts()
    
    print("╔════════════════════════════════════════╗")
    print("║           帳戶餘額                      ║")
    print("╠════════════════════════════════════════╣")
    
    total = 0
    for account in accounts:
        print(f"║ {account.name:<18} {account.balance:>14}  ║")
        total += account.balance
    
    print("╠════════════════════════════════════════╣")
    print(f"║ 總計:            {total:>14}  ║")
    print("╚════════════════════════════════════════╝")


def cmd_categories(args, engine: AccountingEngine):
    """列出分類"""
    categories = engine.storage.get_categories()
    
    print("\n收入分類:")
    for cat in categories:
        if cat.type == TransactionType.INCOME:
            icon = cat.icon or "💰"
            print(f"  {icon} {cat.name} ({cat.id})")
    
    print("\n支出分類:")
    for cat in categories:
        if cat.type == TransactionType.EXPENSE:
            icon = cat.icon or "💸"
            print(f"  {icon} {cat.name} ({cat.id})")


def cmd_budget(args, engine: AccountingEngine):
    """預算檢查"""
    spent, budget, over = engine.check_budget(args.category)
    
    category = engine.storage._categories.get(args.category)
    cat_name = category.name if category else args.category
    
    print(f"\n{cat_name} 預算:")
    print(f"  已使用: {spent:.2f}")
    print(f"  預算:   {budget:.2f}")
    
    if over:
        print(f"  ⚠️  已超支 {spent - budget:.2f}")
    elif budget > 0:
        remaining = budget - spent
        print(f"  ✓ 剩餘: {remaining:.2f}")


def create_parser() -> argparse.ArgumentParser:
    """建立命令列解析器"""
    parser = argparse.ArgumentParser(
        prog="accounting",
        description="個人記帳工具"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # add 命令
    add_parser = subparsers.add_parser("add", help="新增交易")
    add_parser.add_argument("amount", type=float, help="金額")
    add_parser.add_argument("--type", "-t", choices=["income", "expense"], 
                           default="expense", help="交易類型")
    add_parser.add_argument("--category", "-c", required=True, help="分類 ID")
    add_parser.add_argument("--account", "-a", default="cash", help="帳戶 ID")
    add_parser.add_argument("--description", "-d", help="說明")
    add_parser.add_argument("--date", help="日期 (YYYY-MM-DD)")
    add_parser.add_argument("--tags", help="標籤 (逗號分隔)")
    add_parser.add_argument("--notes", help="備註")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出交易")
    list_parser.add_argument("--start-date", help="開始日期")
    list_parser.add_argument("--end-date", help="結束日期")
    list_parser.add_argument("--category", "-c", help="分類 ID")
    list_parser.add_argument("--account", "-a", help="帳戶 ID")
    list_parser.add_argument("--limit", "-l", type=int, default=20, help="顯示筆數")
    
    # summary 命令
    subparsers.add_parser("summary", help="統計摘要")
    
    # balance 命令
    subparsers.add_parser("balance", help="帳戶餘額")
    
    # categories 命令
    subparsers.add_parser("categories", help="列出分類")
    
    # budget 命令
    budget_parser = subparsers.add_parser("budget", help="預算檢查")
    budget_parser.add_argument("--category", "-c", required=True, help="分類 ID")
    
    return parser


def main(args=None):
    """主函數"""
    parser = create_parser()
    parsed_args = parser.parse_args(args)
    
    if not parsed_args.command:
        parser.print_help()
        return
    
    engine = AccountingEngine()
    
    commands = {
        "add": cmd_add,
        "list": cmd_list,
        "summary": cmd_summary,
        "balance": cmd_balance,
        "categories": cmd_categories,
        "budget": cmd_budget,
    }
    
    if parsed_args.command in commands:
        commands[parsed_args.command](parsed_args, engine)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
