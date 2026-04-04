"""簡易記帳 CLI 範例"""
import argparse
from datetime import datetime
from llm_code.accounting_simple import (
    AccountingEngine,
    TransactionType,
)


def main():
    engine = AccountingEngine()
    
    parser = argparse.ArgumentParser(description="簡易記帳")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # add 命令
    add_parser = subparsers.add_parser("add", help="新增交易")
    add_parser.add_argument("amount", type=float, help="金額")
    add_parser.add_argument("type", choices=["收入", "支出"], help="交易類型")
    add_parser.add_argument("category", help="分類名稱")
    add_parser.add_argument("account", help="帳戶名稱")
    add_parser.add_argument("--desc", "--description", default="", help="說明")
    
    # list 命令
    subparsers.add_parser("list", help="列出交易")
    
    # summary 命令
    subparsers.add_parser("summary", help="統計摘要")
    
    args = parser.parse_args()
    
    if args.command == "add":
        # 取得或建立帳戶
        accounts = engine.get_all_accounts()
        account = None
        for acc in accounts:
            if acc.name == args.account:
                account = acc
                break
        if not account:
            account = engine.create_account(args.account)
        
        # 取得或建立分類
        categories = engine.get_categories()
        category = None
        for cat in categories:
            if cat.name == args.category:
                category = cat
                break
        if not category:
            cat_type = TransactionType.INCOME if args.type == "收入" else TransactionType.EXPENSE
            category = engine.create_category(args.category, cat_type)
        
        # 新增交易
        trans_type = TransactionType.INCOME if args.type == "收入" else TransactionType.EXPENSE
        transaction = engine.add_transaction(
            amount=args.amount,
            transaction_type=trans_type,
            category_id=category.id,
            account_id=account.id,
            description=args.desc
        )
        print(f"✅ 已記錄：{args.type} {args.amount} 元 ({args.category})")
    
    elif args.command == "list":
        transactions = engine.get_transactions(limit=20)
        if not transactions:
            print("沒有交易記錄")
            return
        
        print(f"{'日期':<12} {'類型':<6} {'金額':>10} {'分類':<15} {'說明'}")
        print("-" * 60)
        for t in transactions:
            type_str = "收入" if t.type == TransactionType.INCOME else "支出"
            amount_str = f"+{t.amount:.2f}" if t.type == TransactionType.INCOME else f"-{t.amount:.2f}"
            print(f"{t.date.strftime('%Y-%m-%d'):<12} {type_str:<6} {amount_str:>10} {'':<15} {t.description}")
    
    elif args.command == "summary":
        summary = engine.get_summary()
        print(f"總收入:  {summary.total_income:.2f}")
        print(f"總支出:  {summary.total_expense:.2f}")
        print(f"餘額:    {summary.balance:.2f}")
        print(f"交易次數: {summary.transaction_count}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
