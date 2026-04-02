#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据管理模块
- SQLite本地缓存
- 数据完整性检查
- 自动拉取tushare数据

使用方式：
    from data_manager import DataManager
    dm = DataManager()

    # 获取日线数据（本地优先，缺失则自动拉取）
    df = dm.get_daily_prices('000001.SZ', days=60)

    # 检查数据状态
    status = dm.get_data_status()
"""

import sqlite3
import pandas as pd
import numpy as np
import tushare as ts
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import time
import os

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Tushare Token（从环境变量获取）
TS_TOKEN = os.environ.get('TUSHARE_TOKEN', 'your_token_here')

class DataManager:
    """A股数据管理器，SQLite缓存 + Tushare数据源"""

    DB_PATH = Path(__file__).parent / 'data' / 'ymos_stocks.db'

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化数据管理器

        :param db_path: 自定义数据库路径，默认 workflow/data/ymos_stocks.db
        """
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = self.DB_PATH

        # 确保目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self._init_db()

        # 初始化tushare
        if TS_TOKEN != 'your_token_here':
            ts.set_token(TS_TOKEN)
            self.pro = ts.pro_api()
        else:
            self.pro = None
            print("警告: 未配置TUSHARE_TOKEN，数据拉取功能不可用")

    def _init_db(self):
        """创建数据表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. 股票基础信息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stocks_basic (
                ts_code TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                market TEXT,
                list_date TEXT,
                updated_at TEXT
            )
        """)

        # 2. 日线行情表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_prices (
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                close REAL,
                high REAL,
                low REAL,
                vol REAL,
                amount REAL,
                pct_chg REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)

        # 3. 资金流向表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS moneyflow (
                ts_code TEXT,
                trade_date TEXT,
                net_amount_elg REAL,
                net_amount_lg REAL,
                net_amount_md REAL,
                net_amount_sm REAL,
                buy_elg_amount REAL,
                buy_lg_amount REAL,
                sell_elg_amount REAL,
                sell_lg_amount REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)

        # 4. 指数日线表（用于抗跌对比）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_daily (
                ts_code TEXT,
                trade_date TEXT,
                close REAL,
                pct_chg REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)

        # 创建索引加速查询
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_ts_code ON daily_prices(ts_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(trade_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_moneyflow_ts_code ON moneyflow(ts_code)")

        conn.commit()
        conn.close()
        print(f"✓ 数据库初始化完成: {self.db_path}")

    # ==================== 数据完整性检查 ====================

    def check_data_availability(self, ts_code: str, data_type: str, days: int) -> bool:
        """
        检查指定股票的数据是否完整

        :param ts_code: 股票代码如 '000001.SZ'
        :param data_type: 数据类型 'daily' / 'moneyflow' / 'index'
        :param days: 需要的数据天数
        :return: True如果数据完整，False需要拉取
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        table_map = {
            'daily': 'daily_prices',
            'moneyflow': 'moneyflow',
            'index': 'index_daily'
        }
        table = table_map.get(data_type, 'daily_prices')

        # 查询该股票的数据条数
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ts_code = ?",
            (ts_code,)
        )
        count = cursor.fetchone()[0]
        conn.close()

        return count >= days

    def check_stocks_basic_exists(self) -> bool:
        """检查股票基础信息表是否有数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stocks_basic")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    # ==================== 数据查询接口 ====================

    def get_daily_prices(self, ts_code: str, days: int = 60) -> pd.DataFrame:
        """
        获取日线行情数据（本地优先，缺失则自动拉取）

        :param ts_code: 股票代码
        :param days: 需要的天数
        :return: DataFrame with columns: trade_date, close, vol, amount, etc.
        """
        # 检查本地数据
        if not self.check_data_availability(ts_code, 'daily', days):
            # 自动拉取
            self._fetch_and_store_daily(ts_code, days + 30)  # 多拉一些以防不足

        # 从本地读取
        return self._query_daily(ts_code, days)

    def get_moneyflow(self, ts_code: str, days: int = 20) -> pd.DataFrame:
        """
        获取资金流向数据（本地优先，缺失则自动拉取）
        """
        if not self.check_data_availability(ts_code, 'moneyflow', days):
            self._fetch_and_store_moneyflow(ts_code, days + 10)

        return self._query_moneyflow(ts_code, days)

    def get_index_daily(self, index_code: str = '000001.SH', days: int = 20) -> pd.DataFrame:
        """
        获取指数日线数据
        """
        if not self.check_data_availability(index_code, 'index', days):
            self._fetch_and_store_index(index_code, days + 10)

        return self._query_index(index_code, days)

    def get_all_stocks(self) -> pd.DataFrame:
        """
        获取全部A股列表（本地优先，缺失则拉取）
        """
        if not self.check_stocks_basic_exists():
            self.refresh_stocks_basic()

        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql("SELECT * FROM stocks_basic", conn)
        conn.close()
        return df

    # ==================== 内部查询方法 ====================

    def _query_daily(self, ts_code: str, days: int) -> pd.DataFrame:
        """从SQLite读取日线数据"""
        conn = sqlite3.connect(self.db_path)

        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')

        query = """
            SELECT * FROM daily_prices
            WHERE ts_code = ?
            AND trade_date >= ?
            AND trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT ?
        """
        df = pd.read_sql(query, conn, params=(ts_code, start_date, end_date, days))
        conn.close()

        if not df.empty:
            df = df.sort_values('trade_date')
        return df

    def _query_moneyflow(self, ts_code: str, days: int) -> pd.DataFrame:
        """从SQLite读取资金流向"""
        conn = sqlite3.connect(self.db_path)

        query = """
            SELECT * FROM moneyflow
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
        """
        df = pd.read_sql(query, conn, params=(ts_code, days))
        conn.close()

        if not df.empty:
            df = df.sort_values('trade_date')
        return df

    def _query_index(self, index_code: str, days: int) -> pd.DataFrame:
        """从SQLite读取指数数据"""
        conn = sqlite3.connect(self.db_path)

        query = """
            SELECT * FROM index_daily
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
        """
        df = pd.read_sql(query, conn, params=(index_code, days))
        conn.close()

        if not df.empty:
            df = df.sort_values('trade_date')
        return df

    # ==================== 数据拉取与存储 ====================

    def _fetch_and_store_daily(self, ts_code: str, days: int):
        """从tushare拉取日线数据并存入SQLite"""
        if self.pro is None:
            print(f"无法拉取 {ts_code} 数据：未配置TUSHARE_TOKEN")
            return

        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')

            df = self.pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date
            )

            if df.empty:
                return

            # 存入SQLite
            conn = sqlite3.connect(self.db_path)
            df.to_sql('daily_prices', conn, if_exists='append', index=False)
            conn.close()

            print(f"✓ 拉取日线: {ts_code} ({len(df)}条)")

        except Exception as e:
            print(f"拉取日线失败 {ts_code}: {e}")

    def _fetch_and_store_moneyflow(self, ts_code: str, days: int):
        """从tushare拉取资金流向"""
        if self.pro is None:
            return

        try:
            df = self.pro.moneyflow_dc(ts_code=ts_code, limit=days)

            if df.empty:
                return

            # 重命名列以匹配表结构
            df = df.rename(columns={
                'net_mf_vol': 'net_amount_elg',  # 特大单
                'net_mf_amount': 'net_amount_lg',  # 大单（简化）
            })

            conn = sqlite3.connect(self.db_path)
            df.to_sql('moneyflow', conn, if_exists='append', index=False)
            conn.close()

            print(f"✓ 拉取资金流向: {ts_code}")

        except Exception as e:
            # 资金流向接口可能需要高积分
            print(f"拉取资金流向失败 {ts_code}（可能积分不足）: {e}")

    def _fetch_and_store_index(self, index_code: str, days: int):
        """拉取指数数据"""
        if self.pro is None:
            return

        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days + 10)).strftime('%Y%m%d')

            df = self.pro.index_daily(
                ts_code=index_code,
                start_date=start_date,
                end_date=end_date
            )

            if df.empty:
                return

            conn = sqlite3.connect(self.db_path)
            df[['ts_code', 'trade_date', 'close', 'pct_chg']].to_sql(
                'index_daily', conn, if_exists='append', index=False
            )
            conn.close()

            print(f"✓ 拉取指数: {index_code}")

        except Exception as e:
            print(f"拉取指数失败 {index_code}: {e}")

    def refresh_stocks_basic(self):
        """刷新股票基础信息表"""
        if self.pro is None:
            print("无法刷新股票列表：未配置TUSHARE_TOKEN")
            return

        try:
            df = self.pro.stock_basic(
                exchange='',
                list_status='L',
                fields='ts_code,symbol,name,market,list_date'
            )

            # 过滤北交所
            df = df[~df['ts_code'].str.startswith(('8', '43', '4'))]

            # 添加更新时间
            df['updated_at'] = datetime.now().strftime('%Y%m%d')

            # 清空旧数据并写入新数据
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stocks_basic")
            conn.commit()
            df.to_sql('stocks_basic', conn, if_exists='append', index=False)
            conn.close()

            print(f"✓ 刷新股票列表: {len(df)}只")

        except Exception as e:
            print(f"刷新股票列表失败: {e}")

    # ==================== 批量刷新 ====================

    def refresh_all_daily(self, days: int = 120, batch_size: int = 50):
        """
        批量刷新所有股票的日线数据

        :param days: 刷新多少天的数据
        :param batch_size: 每批处理多少只股票
        """
        stocks = self.get_all_stocks()
        total = len(stocks)

        print(f"开始批量刷新日线数据，共{total}只股票...")

        for i in range(0, total, batch_size):
            batch = stocks.iloc[i:i+batch_size]

            for _, row in batch.iterrows():
                self._fetch_and_store_daily(row['ts_code'], days)
                time.sleep(0.3)  # 控制频率

            print(f"进度: {min(i+batch_size, total)}/{total}")
            time.sleep(2)  # 批次间休息

        print("✓ 日线数据刷新完成")

    # ==================== 数据状态报告 ====================

    def get_data_status(self) -> dict:
        """
        返回数据状态报告
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        status = {}

        # 股票数量
        cursor.execute("SELECT COUNT(*) FROM stocks_basic")
        status['stocks_count'] = cursor.fetchone()[0]

        # 日线数据覆盖
        cursor.execute("SELECT COUNT(DISTINCT ts_code) FROM daily_prices")
        status['daily_covered'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM daily_prices")
        status['daily_total_records'] = cursor.fetchone()[0]

        # 资金流向覆盖
        cursor.execute("SELECT COUNT(DISTINCT ts_code) FROM moneyflow")
        status['moneyflow_covered'] = cursor.fetchone()[0]

        # 指数数据
        cursor.execute("SELECT COUNT(*) FROM index_daily")
        status['index_records'] = cursor.fetchone()[0]

        conn.close()
        return status

    def print_status(self):
        """打印数据状态"""
        status = self.get_data_status()
        print("\n===== 数据状态报告 =====")
        print(f"股票总数: {status['stocks_count']}")
        print(f"日线覆盖: {status['daily_covered']}只 ({status['daily_total_records']}条)")
        print(f"资金流向覆盖: {status['moneyflow_covered']}只")
        print(f"指数数据: {status['index_records']}条")


# ==================== 便捷测试 ====================
if __name__ == '__main__':
    dm = DataManager()
    dm.print_status()

    # 测试获取日线数据
    # df = dm.get_daily_prices('000001.SZ', days=60)
    # print(df.head())