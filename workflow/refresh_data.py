#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据刷新脚本
支持命令行参数刷新各类数据

使用示例：
    # 刷新股票基础信息
    python refresh_data.py --type basic

    # 刷新日线数据（默认120天）
    python refresh_data.py --type daily --days 120

    # 刷新资金流向（需要高积分）
    python refresh_data.py --type moneyflow --days 20

    # 刷新指数数据
    python refresh_data.py --type index

    # 全量刷新
    python refresh_data.py --type all

    # 刷新指定股票
    python refresh_data.py --type daily --codes 000001.SZ,600519.SH
"""

import argparse
import time
from datetime import datetime
from data_manager import DataManager

def refresh_basic(dm: DataManager):
    """刷新股票基础信息"""
    print("\n===== 刷新股票基础信息 =====")
    dm.refresh_stocks_basic()
    dm.print_status()

def refresh_daily(dm: DataManager, days: int = 120, codes: list = None, batch_size: int = 50):
    """
    刷新日线数据

    :param dm: DataManager实例
    :param days: 刷新多少天的数据
    :param codes: 指定股票代码列表（None则刷新全部）
    :param batch_size: 每批处理数量
    """
    print(f"\n===== 刷新日线数据（{days}天） =====")

    if codes:
        # 刷新指定股票
        print(f"指定股票: {codes}")
        for code in codes:
            dm._fetch_and_store_daily(code, days)
            time.sleep(0.5)
    else:
        # 刷新全部股票
        dm.refresh_all_daily(days=days, batch_size=batch_size)

    dm.print_status()

def refresh_moneyflow(dm: DataManager, days: int = 20, codes: list = None):
    """
    刷新资金流向数据

    注意：此接口需要tushare高积分
    """
    print(f"\n===== 刷新资金流向数据（{days}天） =====")
    print("注意：资金流向接口需要tushare 2000+积分")

    if codes:
        stocks = [{'ts_code': code} for code in codes]
    else:
        stocks = dm.get_all_stocks()

    if stocks.empty:
        print("无股票数据，请先刷新基础信息")
        return

    total = len(stocks)
    success_count = 0
    fail_count = 0

    for idx, row in stocks.iterrows() if hasattr(stocks, 'iterrows') else enumerate(stocks):
        ts_code = row['ts_code'] if hasattr(row, 'ts_code') else row['ts_code']

        dm._fetch_and_store_moneyflow(ts_code, days)
        time.sleep(0.3)

        if (idx + 1) % 100 == 0:
            print(f"进度: {idx+1}/{total}")

    print(f"✓ 资金流向刷新完成")
    dm.print_status()

def refresh_index(dm: DataManager, index_code: str = '000001.SH', days: int = 30):
    """刷新指数数据"""
    print(f"\n===== 刷新指数数据 =====")
    dm._fetch_and_store_index(index_code, days)
    dm.print_status()

def refresh_all(dm: DataManager, days: int = 120):
    """全量刷新所有数据"""
    print("\n===== 全量刷新 =====")

    # 1. 股票基础信息
    refresh_basic(dm)

    # 2. 指数数据
    refresh_index(dm, days=days)

    # 3. 日线数据（耗时较长）
    refresh_daily(dm, days=days)

    # 4. 资金流向（可选，需要高积分）
    print("\n资金流向刷新需要高积分，是否继续？(y/n)")
    # 默认跳过，避免积分不足报错
    # refresh_moneyflow(dm, days=20)

    print("\n✓ 全量刷新完成")

def show_status(dm: DataManager):
    """显示数据状态"""
    dm.print_status()

def estimate_time(dm: DataManager, days: int = 120) -> str:
    """
    估算刷新时间

    基于tushare免费版约50次/分钟的限制
    """
    stocks = dm.get_all_stocks()
    count = len(stocks)

    if count == 0:
        return "无股票数据"

    # 每只股票约0.3秒 + 批次间隔2秒
    batch_size = 50
    batches = count // batch_size + 1
    total_seconds = count * 0.3 + batches * 2

    minutes = total_seconds // 60
    seconds = total_seconds % 60

    return f"预估耗时: {int(minutes)}分{int(seconds)}秒（{count}只股票）"

# ==================== 命令行入口 ====================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='YMOS数据刷新工具')
    parser.add_argument(
        '--type',
        type=str,
        default='status',
        choices=['basic', 'daily', 'moneyflow', 'index', 'all', 'status'],
        help='刷新类型: basic(基础信息), daily(日线), moneyflow(资金流向), index(指数), all(全量), status(查看状态)'
    )
    parser.add_argument('--days', type=int, default=120, help='刷新数据天数')
    parser.add_argument('--codes', type=str, default=None, help='指定股票代码(逗号分隔)')
    parser.add_argument('--index', type=str, default='000001.SH', help='指数代码')
    parser.add_argument('--batch-size', type=int, default=50, help='批量刷新每批数量')

    args = parser.parse_args()

    # 初始化数据管理器
    dm = DataManager()

    # 处理指定股票代码
    codes = None
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',')]

    # 执行刷新
    if args.type == 'status':
        show_status(dm)
        print(f"\n{estimate_time(dm, args.days)}")

    elif args.type == 'basic':
        refresh_basic(dm)

    elif args.type == 'daily':
        print(f"\n{estimate_time(dm, args.days)}")
        refresh_daily(dm, days=args.days, codes=codes, batch_size=args.batch_size)

    elif args.type == 'moneyflow':
        refresh_moneyflow(dm, days=args.days, codes=codes)

    elif args.type == 'index':
        refresh_index(dm, index_code=args.index, days=args.days)

    elif args.type == 'all':
        print(f"\n{estimate_time(dm, args.days)}")
        print("警告：全量刷新耗时较长，请耐心等待...")
        refresh_all(dm, days=args.days)