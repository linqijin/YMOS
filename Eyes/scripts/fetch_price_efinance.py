#!/usr/bin/env python3
"""
efinance A股价格拉取脚本（Tushare 备选方案）

定位：当 Tushare 不可用时，作为 A股价格数据的备选数据源。
     基于东方财富数据，无需注册/Token，开箱即用。

接口说明：
  - 数据源：东方财富
  - 使用 efinance 库：pip install efinance
  - 支持 A股（沪深主板、科创板、创业板）

Ticker 格式：
  - 状态机中的 Ticker   →   efinance 代码
  - 002549.SZ           →   002549（去掉后缀）
  - 600744.SS           →   600744（去掉后缀）

用法：
  python3 Eyes/scripts/fetch_price_efinance.py \
    --symbols 002549.SZ,600744.SS \
    --output out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import io
from datetime import datetime, timezone
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parents[1]  # Eyes/scripts → Eyes → YMOS

# 尝试导入 efinance
try:
    import efinance as ef
    EFINANCE_AVAILABLE = True
except ImportError:
    EFINANCE_AVAILABLE = False
    print("⚠️ efinance 未安装，请运行: pip install efinance")


# ── Ticker 格式转换 ────────────────────────────────────────────────────────

def to_efinance_code(ticker: str) -> str | None:
    """
    将状态机中的 Ticker 转换为 efinance 代码格式。
    只处理 A股（.SS/.SZ/.SH），返回纯数字代码。
    非 A股返回 None。
    """
    t = ticker.strip().upper()
    if t.endswith((".SS", ".SZ", ".SH")):
        return t[:-3]
    # 纯数字（6位）直接返回
    if len(t) == 6 and t.isdigit():
        return t
    return None


def from_efinance_code(code: str, original_suffix: str = ".SZ") -> str:
    """efinance 代码 → 状态机 Ticker（反向）"""
    return f"{code}{original_suffix}"


# ── 价格获取 ───────────────────────────────────────────────────────────────

def fetch_price_single(code: str, days: int = 7) -> dict | None:
    """
    获取单个 A股的最新价格。
    返回格式与 fetch_price_tushare.py 对齐。
    """
    if not EFINANCE_AVAILABLE:
        return None

    try:
        # klt=101 表示日K线，limt 限制返回条数
        df = ef.stock.get_quote_history(code, klt=101, limit=days)
        if df is None or df.empty:
            return None

        # 取最后一行（最新交易日）
        latest = df.iloc[-1]

        return {
            "symbol": code,
            "ok": True,
            "trade_date": str(latest.get("日期", "")),
            "last_close": float(latest.get("收盘", 0)),
            "last_open": float(latest.get("开盘", 0)),
            "last_high": float(latest.get("最高", 0)),
            "last_low": float(latest.get("最低", 0)),
            "last_volume": float(latest.get("成交量", 0)),
            "pct_chg": float(latest.get("涨跌幅", 0)),
            "change": float(latest.get("涨跌额", 0)),
            "source": "efinance",
        }
    except Exception as e:
        print(f"  ❌ {code} 获取失败: {e}")
        return None


def fetch_prices(codes: list[str]) -> list[dict]:
    """批量获取 A股价格"""
    results = []
    for code in codes:
        item = fetch_price_single(code)
        if item:
            results.append(item)
            print(f"  ✅ {item['symbol']:10s} ¥{item['last_close']:.2f} ({item['pct_chg']:+.2f}%) [{item['trade_date']}]")
        else:
            results.append({"symbol": code, "ok": False, "error": "no_data", "source": "efinance"})
            print(f"  ❌ {code:10s} ERROR: no_data")
    return results


# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    if not EFINANCE_AVAILABLE:
        print("❌ efinance 未安装，退出。请运行: pip install efinance")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="efinance A股价格拉取（Tushare 备选方案）"
    )
    parser.add_argument(
        "--symbols", default="",
        help="逗号分隔 Ticker，支持 .SS/.SZ 格式，如 002549.SZ,600744.SS",
    )
    parser.add_argument(
        "--output", default="efinance_price.json",
        help="输出 JSON 路径",
    )
    args = parser.parse_args()

    raw_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not raw_symbols:
        print("⚠️ 未提供 --symbols，退出。")
        sys.exit(0)

    # 转换为 efinance 代码，过滤非 A股
    code_map: dict[str, str] = {}   # efinance_code → original_ticker
    skipped: list[str] = []

    for sym in raw_symbols:
        code = to_efinance_code(sym)
        if code:
            code_map[code] = sym
        else:
            skipped.append(sym)

    if skipped:
        print(f"⏭️ 跳过非A股 Ticker: {', '.join(skipped)}")

    if not code_map:
        print("⚠️ 没有可查询的 A股标的，退出。")
        sys.exit(0)

    print(f"📡 efinance 拉取 A股价格: {', '.join(code_map.keys())}")

    results = fetch_prices(list(code_map.keys()))

    # 恢复原始 Ticker 格式
    for item in results:
        code = item.get("symbol", "")
        if code in code_map:
            item["symbol"] = code_map[code]

    output = {
        "source": "efinance (东方财富)",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "symbols": raw_symbols,
        "skipped": skipped,
        "data": results,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for x in results if x.get("ok"))
    print(f"\n💾 已保存：{out_path}  ({ok}/{len(results)} 成功)")


if __name__ == "__main__":
    main()