#!/usr/bin/env python3
"""
Tushare A股/ETF 日线价格拉取脚本（YMOS 专用）

定位：专门负责 A股（上交所 .SS/.SH、深交所 .SZ）的价格数据拉取。
     Yahoo Finance 对科创板等 A股数据质量不稳定，Tushare 是更可靠的替代方案。

接口说明：
  - 接口：Tushare Pro HTTP REST API  POST http://api.tushare.pro
  - 使用接口：daily（日线行情）
  - 无第三方库依赖，纯 urllib + json 标准库
  - 免费注册即可使用，基础积分：500 calls/min，单次 6000 条

Ticker 格式转换（内部自动处理）：
  状态机中的 Ticker   →   Tushare ts_code
  688981.SS           →   688981.SH   (上交所：.SS → .SH)
  688981.SH           →   688981.SH   (已是 Tushare 格式，直接用)
  000001.SZ           →   000001.SZ   (深交所：不变)
  600519.SS           →   600519.SH

用法：
  python3 scripts/fetch_price_tushare.py \
    --symbols 688981.SS,000001.SZ \
    --trade-date 20260316 \
    --output Report/投资雷达/Raw_Data/tushare_price_YYYYMMDD.json

  # 不指定 --trade-date 则默认拉最新交易日（range=最近5日取最后一条）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import io
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parents[1]  # Eyes/scripts → Eyes → YMOS

sys.path.insert(0, str(SCRIPTS_DIR))
from env_loader import load_dotenv

TUSHARE_API_URL = "http://lianghua.nanyangqiankun.top"


# ── Ticker 格式转换 ────────────────────────────────────────────────────────

def to_tushare_code(ticker: str) -> str | None:
    """
    将状态机中的 Ticker 转换为 Tushare ts_code 格式。
    只处理 A股（.SS → .SH，.SZ 保持不变）。
    非 A股（.HK / 纯字母美股）返回 None，由调用方过滤。
    """
    t = ticker.strip().upper()
    if t.endswith(".SS"):
        return t[:-3] + ".SH"
    if t.endswith(".SH") or t.endswith(".SZ"):
        return t
    # 非 A股，跳过
    return None


def from_tushare_code(ts_code: str) -> str:
    """Tushare ts_code → 状态机 Ticker（反向，用于输出 key 对齐）。"""
    if ts_code.endswith(".SH"):
        return ts_code[:-3] + ".SS"
    return ts_code


# ── API 调用 ───────────────────────────────────────────────────────────────

def tushare_post(api_name: str, token: str, params: dict, fields: str) -> dict:
    payload = json.dumps({
        "api_name": api_name,
        "token":    token,
        "params":   params,
        "fields":   fields,
    }).encode("utf-8")

    req = urllib.request.Request(
        TUSHARE_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"code": -1, "msg": str(e), "data": None}


def fetch_daily(ts_codes: list[str], token: str, trade_date: str | None,
                start_date: str | None, end_date: str | None) -> list[dict]:
    """
    批量拉取 A股日线价格。
    返回每个标的最近一条记录（字段与 fetch_price_yahoo.py 输出对齐）。
    """
    params: dict = {"ts_code": ",".join(ts_codes)}
    if trade_date:
        params["trade_date"] = trade_date
    elif start_date and end_date:
        params["start_date"] = start_date
        params["end_date"]   = end_date
    else:
        # 默认：拉最近 7 个自然日，确保覆盖最后一个交易日
        today     = datetime.now(timezone.utc)
        params["start_date"] = (today - timedelta(days=7)).strftime("%Y%m%d")
        params["end_date"]   = today.strftime("%Y%m%d")

    result = tushare_post(
        api_name="daily",
        token=token,
        params=params,
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )

    if result.get("code") != 0:
        print(f"  ❌ Tushare API 错误: {result.get('msg')}")
        return []

    raw_items = result["data"].get("items", [])
    raw_fields = result["data"].get("fields", [])

    # 按 ts_code 分组，每个取最近一条（API 返回按日期降序）
    latest: dict[str, dict] = {}
    for row in raw_items:
        d = dict(zip(raw_fields, row))
        code = d["ts_code"]
        if code not in latest:          # 第一条即最新
            latest[code] = d

    return list(latest.values())


# ── 输出格式化 ────────────────────────────────────────────────────────────

def format_result(ts_code: str, row: dict | None) -> dict:
    """统一输出格式，与 fetch_price_yahoo.py 的 data[] 结构对齐。"""
    original_ticker = from_tushare_code(ts_code)
    if row is None:
        return {"symbol": original_ticker, "ts_code": ts_code, "ok": False, "error": "no_data"}

    return {
        "symbol":      original_ticker,         # 状态机 Ticker（.SS 格式）
        "ts_code":     ts_code,                 # Tushare 格式（.SH）
        "ok":          True,
        "trade_date":  row.get("trade_date", ""),
        "last_close":  float(row.get("close")   or 0),
        "last_open":   float(row.get("open")    or 0),
        "last_high":   float(row.get("high")    or 0),
        "last_low":    float(row.get("low")     or 0),
        "last_volume": float(row.get("vol")     or 0),
        "pre_close":   float(row.get("pre_close") or 0),
        "change":      float(row.get("change")  or 0),
        "pct_chg":     float(row.get("pct_chg") or 0),
        "amount_wan":  float(row.get("amount")  or 0),   # 万元
    }


# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Tushare A股日线价格拉取（持仓/Watchlist 专用）"
    )
    parser.add_argument(
        "--symbols", default="",
        help="逗号分隔 Ticker，支持 .SS/.SH/.SZ 格式，如 688981.SS,000001.SZ",
    )
    parser.add_argument(
        "--trade-date", default="",
        help="指定交易日 YYYYMMDD（不填则取最近交易日）",
    )
    parser.add_argument("--start-date", default="", help="历史起始日 YYYYMMDD")
    parser.add_argument("--end-date",   default="", help="历史截止日 YYYYMMDD")
    parser.add_argument(
        "--output", default="tushare_price.json",
        help="输出 JSON 路径",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TUSHARE_TOKEN", ""),
        help="Tushare Token，也可通过环境变量 TUSHARE_TOKEN 传入",
    )
    args = parser.parse_args()

    if not args.token:
        print("⚠️ 未提供 Tushare Token，跳过 A股价格拉取。")
        print("   请在 .env 中配置 TUSHARE_TOKEN 或通过 --token 传入。")
        sys.exit(0)

    raw_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not raw_symbols:
        print("⚠️ 未提供 --symbols，退出。")
        sys.exit(0)

    # 转换为 Tushare ts_code，过滤非 A股
    code_map: dict[str, str] = {}   # ts_code → original_ticker
    skipped: list[str] = []
    for sym in raw_symbols:
        tc = to_tushare_code(sym)
        if tc:
            code_map[tc] = sym
        else:
            skipped.append(sym)

    if skipped:
        print(f"⏭️  跳过非A股 Ticker（由 Finnhub/Yahoo 负责）: {', '.join(skipped)}")

    if not code_map:
        print("⚠️ 没有可查询的 A股标的，退出。")
        sys.exit(0)

    print(f"📡 Tushare 拉取 A股价格: {', '.join(code_map.keys())}")

    rows = fetch_daily(
        ts_codes=list(code_map.keys()),
        token=args.token,
        trade_date=args.trade_date or None,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
    )

    # 索引化
    rows_by_code = {r["ts_code"]: r for r in rows}

    data: list[dict] = []
    for ts_code in code_map:
        raw_row = rows_by_code.get(ts_code)
        item = format_result(ts_code, raw_row)
        data.append(item)
        status = "✅" if item["ok"] else "❌"
        if item["ok"]:
            print(
                f"  {status} {item['symbol']:16s} "
                f"¥{item['last_close']:.2f}  "
                f"({item['pct_chg']:+.2f}%)  "
                f"[{item['trade_date']}]"
            )
        else:
            print(f"  {status} {item['symbol']:16s} ERROR: {item.get('error')}")

    output = {
        "source":     "Tushare Pro daily API",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count":      len(data),
        "symbols":    raw_symbols,
        "skipped":    skipped,
        "data":       data,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for x in data if x.get("ok"))
    print(f"\n💾 已保存：{out_path}  ({ok}/{len(data)} 成功)")


if __name__ == "__main__":
    main()
