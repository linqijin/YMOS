#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股主力控盘 + 低涨幅选股工具
基于 tushare 数据源 + SQLite本地缓存

筛选条件：
1. 主力控盘特征明显（多维度评分）
2. 主力平均持仓成本涨幅不高（安全介入位置）

评分维度（权重调整后）：
- 资金流向 30%
- 筹码集中度 25%
- 脉冲放量 15%
- 逆势抗跌 15%
- 成本涨幅 15%（新增）
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
import argparse
warnings.filterwarnings('ignore')

# 引入数据管理模块
from data_manager import DataManager

# 初始化数据管理器
dm = DataManager()

# ==================== 核心筛选维度 ====================

# 1. 资金流向维度（权重30%）
def calc_moneyflow_score(ts_code: str, days: int = 20) -> float | None:
    """
    资金流向维度：主力净流入占比
    若接口权限不足则返回None，降级使用替代指标
    """
    try:
        df = dm.get_moneyflow(ts_code, days)
        if df.empty:
            return None

        # 计算主力（特大单+大单）净流入
        net_main = (df['net_amount_elg'].fillna(0) + df['net_amount_lg'].fillna(0)).sum()
        total_amount = (
            df['buy_elg_amount'].fillna(0) + df['buy_lg_amount'].fillna(0) +
            df.get('sell_elg_amount', 0).fillna(0) + df.get('sell_lg_amount', 0).fillna(0)
        ).sum()

        if total_amount == 0:
            return None

        ratio = net_main / total_amount * 100
        # 归一化到0~100（假设主力净流入占比 -20% ~ +20% 映射到0~100）
        score = np.clip((ratio + 20) / 40 * 100, 0, 100)
        return round(score, 2)

    except Exception:
        return None


# 资金流向替代指标：缩量上涨
def calc_volume_control_alt(ts_code: str, days: int = 60) -> float:
    """
    替代资金流向的指标：缩量上涨天数占比
    主力控盘时往往呈现"上涨缩量"特征
    """
    try:
        df = dm.get_daily_prices(ts_code, days)
        if df.empty or len(df) < 20:
            return 50

        df = df.sort_values('trade_date')
        df['price_up'] = df['close'] > df['close'].shift(1)
        df['vol_shrink'] = df['vol'] < df['vol'].shift(1)
        shrink_up_days = ((df['price_up']) & (df['vol_shrink'])).sum()
        total_up_days = df['price_up'].sum()

        if total_up_days == 0:
            return 50

        ratio = shrink_up_days / total_up_days * 100
        return np.clip(ratio, 0, 100)

    except Exception:
        return 50


# 2. 筹码集中度维度（权重25%）
def calc_chip_score(ts_code: str, lookback: int = 120) -> float:
    """
    筹码集中度：集中度越低（离散度小）得分越高
    用价格加权标准差模拟筹码分布
    """
    try:
        df = dm.get_daily_prices(ts_code, lookback)
        if df.empty or len(df) < 60:
            return 50

        df = df.sort_values('trade_date').tail(lookback)
        close = df['close'].values
        volume = df['vol'].values

        # 估算筹码集中度：价格加权离散度
        weighted_price = np.average(close, weights=volume)
        weighted_std = np.sqrt(np.average((close - weighted_price)**2, weights=volume))
        concentration = weighted_std / weighted_price * 100

        # 集中度越低分越高（假设10%浓度为满分）
        score = max(0, 100 - concentration * 5)
        return np.clip(score, 0, 100)

    except Exception:
        return 50


# 3. 量价形态维度（脉冲放量，权重15%）
def calc_volume_pulse_score(ts_code: str, days: int = 60, threshold: float = 2.0) -> float:
    """
    脉冲放量次数得分：每出现一次脉冲放量得5分，上限100分
    """
    try:
        df = dm.get_daily_prices(ts_code, days)
        if df.empty or len(df) < 30:
            return 0

        df = df.sort_values('trade_date')
        df['vol_ma20'] = df['vol'].rolling(20).mean()
        df['vol_ratio'] = df['vol'] / df['vol_ma20']
        pulse_count = (df['vol_ratio'] > threshold).sum()

        score = min(pulse_count * 5, 100)
        return score

    except Exception:
        return 0


# 4. 逆势抗跌维度（权重15%）
def calc_anti_dip_score(ts_code: str, index_code: str = '000001.SH', lookback: int = 20) -> float:
    """
    逆势抗跌得分：大盘下跌时个股跌幅小于0.5%的天数占比
    """
    try:
        stock_df = dm.get_daily_prices(ts_code, lookback + 10)
        index_df = dm.get_index_daily(index_code, lookback + 10)

        if stock_df.empty or index_df.empty:
            return 50

        stock_df = stock_df.sort_values('trade_date').tail(lookback)
        index_df = index_df.sort_values('trade_date').tail(lookback)

        stock_ret = stock_df['close'].pct_change().values
        index_ret = index_df['close'].pct_change().values

        index_down = index_ret < -0.01  # 大盘跌幅超过1%
        stock_anti = stock_ret > -0.005  # 个股跌幅小于0.5%

        anti_days = np.sum(index_down & stock_anti)
        total_down = np.sum(index_down)

        if total_down == 0:
            return 50

        ratio = anti_days / total_down * 100
        return np.clip(ratio, 0, 100)

    except Exception:
        return 50


# 5. 成本涨幅维度（新增，权重15%）
def calc_cost_gain_score(ts_code: str, lookback: int = 60) -> tuple[float | None, float | None, float | None]:
    """
    主力成本涨幅评估

    原理：主力吸筹后平均持仓成本形成一个"成本线"
    当前股价相对于成本线的涨幅反映安全边际

    计算：
    - 成本线 = 成交量加权均价（估算主力成本）
    - 涨幅 = (当前价 - 成本线) / 成本线 × 100%

    得分映射：
    - 涨幅 < 10%：满分100（成本线附近，安全）
    - 涨幅 10-20%：75分（轻度盈利）
    - 涨幅 20-30%：50分
    - 涨幅 > 30%：0分（主力已大幅盈利，风险增加）

    :return: (score, cost_line, gain_pct)
    """
    try:
        df = dm.get_daily_prices(ts_code, lookback)
        if df.empty or len(df) < 30:
            return None, None, None

        df = df.sort_values('trade_date')

        # 成交量加权均价 ≈ 主力估算成本
        close = df['close'].values
        volume = df['vol'].values
        cost_line = np.average(close, weights=volume)

        # 当前价格
        current_price = close[-1]

        # 涨幅百分比
        gain_pct = (current_price - cost_line) / cost_line * 100

        # 得分映射：涨幅越低得分越高
        if gain_pct < 10:
            score = 100
        elif gain_pct < 20:
            score = 75
        elif gain_pct < 30:
            score = 50
        else:
            score = max(0, 100 - gain_pct * 2)

        return round(score, 2), round(cost_line, 2), round(gain_pct, 2)

    except Exception:
        return None, None, None


# ==================== 综合评分 ====================

def calc_main_control_score(ts_code: str, use_moneyflow: bool = True) -> dict:
    """
    综合多维度计算主力控盘总分（0-100）

    权重分配（新）：
    - 资金流向 30%
    - 筹码集中度 25%
    - 脉冲放量 15%
    - 逆势抗跌 15%
    - 成本涨幅 15%

    :return: 包含各项得分和总分的字典
    """
    # 资金流向（如果可用，否则用替代指标）
    if use_moneyflow:
        money_score = calc_moneyflow_score(ts_code)
        if money_score is None:
            money_score = calc_volume_control_alt(ts_code)
    else:
        money_score = calc_volume_control_alt(ts_code)

    chip_score = calc_chip_score(ts_code)
    pulse_score = calc_volume_pulse_score(ts_code)
    anti_score = calc_anti_dip_score(ts_code)
    cost_score, cost_line, gain_pct = calc_cost_gain_score(ts_code)

    # 成本涨幅得分缺失时给中性分
    if cost_score is None:
        cost_score = 50
        cost_line = 0
        gain_pct = 0

    # 加权汇总
    total = (
        money_score * 0.30 +
        chip_score * 0.25 +
        pulse_score * 0.15 +
        anti_score * 0.15 +
        cost_score * 0.15
    )

    return {
        'ts_code': ts_code,
        'money_score': round(money_score, 2),
        'chip_score': round(chip_score, 2),
        'pulse_score': round(pulse_score, 2),
        'anti_score': round(anti_score, 2),
        'cost_score': round(cost_score, 2),
        'cost_line': cost_line,
        'gain_pct': gain_pct,
        'total_score': round(total, 2)
    }


# ==================== 安全等级判定 ====================

def get_safety_level(gain_pct: float) -> str:
    """
    根据成本涨幅判定安全等级

    - A级：涨幅 < 10%，高安全（成本线附近）
    - B级：涨幅 10-20%，中安全
    - C级：涨幅 20-30%，低安全
    - D级：涨幅 > 30%，谨慎（主力已大幅盈利）
    """
    if gain_pct < 10:
        return 'A'
    elif gain_pct < 20:
        return 'B'
    elif gain_pct < 30:
        return 'C'
    else:
        return 'D'


# ==================== 主筛选流程 ====================

def screen_main_control_stocks(
    top_n: int = 50,
    min_score: float = 60,
    max_cost_gain: float = 20,
    safety_level: str = 'B',
    use_moneyflow: bool = True
) -> pd.DataFrame:
    """
    批量筛选主力控盘 + 低涨幅股票

    :param top_n: 返回前N只
    :param min_score: 最低总分阈值
    :param max_cost_gain: 成本涨幅上限（%）
    :param safety_level: 安全等级阈值（A/B/C/D，返回该等级及以上的）
    :param use_moneyflow: 是否使用资金流向接口
    :return: 筛选结果DataFrame
    """
    # 获取股票列表
    stocks = dm.get_all_stocks()
    if stocks.empty:
        print("本地无股票数据，请先运行 refresh_data.py 初始化数据")
        return pd.DataFrame()

    # 过滤ST股
    stocks = stocks[~stocks['name'].str.contains('ST|退', na=False)]

    results = []
    total = len(stocks)
    print(f"开始筛选，共 {total} 只股票...")

    # 安全等级映射
    safety_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    min_safety = safety_order.get(safety_level, 1)

    for idx, row in stocks.iterrows():
        ts_code = row['ts_code']
        name = row['name']

        # 进度提示
        if (idx + 1) % 100 == 0:
            print(f"进度: {idx+1}/{total}")

        # 计算评分
        scores = calc_main_control_score(ts_code, use_moneyflow=use_moneyflow)

        # 筛选条件
        if scores['total_score'] < min_score:
            continue

        if scores['gain_pct'] > max_cost_gain:
            continue

        safety = get_safety_level(scores['gain_pct'])
        if safety_order.get(safety, 3) > min_safety:
            continue

        results.append({
            'ts_code': ts_code,
            'name': name,
            'score': scores['total_score'],
            'cost_line': scores['cost_line'],
            'current_price': dm.get_daily_prices(ts_code, 1)['close'].iloc[-1] if not dm.get_daily_prices(ts_code, 1).empty else 0,
            'gain_pct': scores['gain_pct'],
            'safety_level': safety,
            'money_score': scores['money_score'],
            'chip_score': scores['chip_score'],
            'pulse_score': scores['pulse_score'],
            'anti_score': scores['anti_score'],
            'cost_score': scores['cost_score']
        })

        # 控制频率
        if (idx + 1) % 10 == 0:
            time.sleep(0.5)

    if not results:
        print(f"未筛选出符合条件的股票")
        print(f"建议：降低 min_score 或提高 max_cost_gain")
        return pd.DataFrame()

    result_df = pd.DataFrame(results).sort_values('score', ascending=False)

    print(f"\n✓ 筛选完成，共 {len(result_df)} 只股票符合条件")
    print(f"\n===== 主力控盘 + 低涨幅 Top{top_n} =====")
    print(result_df.head(top_n)[['ts_code', 'name', 'score', 'gain_pct', 'safety_level']].to_string(index=False))

    return result_df


# ==================== 命令行入口 ====================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A股主力控盘 + 低涨幅选股工具')
    parser.add_argument('--top', type=int, default=50, help='输出前N只股票')
    parser.add_argument('--min-score', type=float, default=60, help='最低得分阈值')
    parser.add_argument('--max-gain', type=float, default=20, help='成本涨幅上限(%)')
    parser.add_argument('--safety', type=str, default='B', choices=['A', 'B', 'C', 'D'], help='安全等级阈值')
    parser.add_argument('--use-moneyflow', type=bool, default=False, help='是否使用资金流向接口(需高积分)')
    parser.add_argument('--output', type=str, default='low_gain_stocks.csv', help='输出文件名')

    args = parser.parse_args()

    # 先检查数据状态
    dm.print_status()

    # 执行筛选
    result = screen_main_control_stocks(
        top_n=args.top,
        min_score=args.min_score,
        max_cost_gain=args.max_gain,
        safety_level=args.safety,
        use_moneyflow=args.use_moneyflow
    )

    # 保存结果
    if not result.empty:
        result.to_csv(args.output, index=False, encoding='utf-8-sig')
        print(f"\n结果已保存至 {args.output}")