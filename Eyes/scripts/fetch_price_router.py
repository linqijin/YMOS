#!/usr/bin/env python3
"""
价格路由器（四源三级分流版）

路由规则（优先级顺序）：
  美股 / Crypto（无后缀）→ Finnhub（若有 FINNHUB_API_KEY，否则 Yahoo 兜底）
  A股（.SS / .SZ）       → Tushare（若有 TUSHARE_TOKEN）→ efinance（备选）→ Yahoo（最终兜底）
  港股（.HK）            → Yahoo（固定，无需 Key）

设计原则：
  - Yahoo 是零配置开箱即用的最终兜底
  - A股采用三级分流：Tushare（有Token）→ efinance（免注册）→ Yahoo
  - efinance 基于东方财富，A股数据质量优于 Yahoo

2026-03-16 重构：新增 Tushare A股分支，A股不再走 Yahoo（科创板数据质量问题）
2026-03-17 增强：Crypto 符号归一化（BTC→BINANCE:BTCUSDT / BTC-USD），避免裸符号返回股票语义价格
2026-03-31 增强：新增 efinance 作为 A股二级备选（Tushare 失败时自动回退）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

SCRIPTS_DIR = Path(__file__).resolve().parent
YMOS_ROOT = SCRIPTS_DIR.parents[1]   # Eyes/scripts → Eyes → YMOS
ROOT = SCRIPTS_DIR.parents[2]        # parent of YMOS（subprocess cwd）

sys.path.insert(0, str(SCRIPTS_DIR))
from env_loader import load_dotenv


# ── Crypto 符号归一化 ──────────────────────────────────────────────────────
# 状态机中统一写裸符号（BTC / ETH），路由器在调用数据源前自动转换
# - Finnhub crypto endpoint 需要交易所前缀（BINANCE:BTCUSDT）
# - Yahoo 需要 -USD 后缀（BTC-USD）
CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "DOT"}

CRYPTO_MAP_FINNHUB = {
    "BTC": "BINANCE:BTCUSDT",
    "ETH": "BINANCE:ETHUSDT",
    "SOL": "BINANCE:SOLUSDT",
    "DOGE": "BINANCE:DOGEUSDT",
    "XRP": "BINANCE:XRPUSDT",
    "ADA": "BINANCE:ADAUSDT",
    "AVAX": "BINANCE:AVAXUSDT",
    "DOT": "BINANCE:DOTUSDT",
}

CRYPTO_MAP_YAHOO = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "DOGE": "DOGE-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
    "AVAX": "AVAX-USD",
    "DOT": "DOT-USD",
}


def is_crypto(symbol: str) -> bool:
    return symbol.upper() in CRYPTO_SYMBOLS


def normalize_for_source(symbol: str, source: str) -> str:
    """将状态机中的 crypto 裸符号转换为数据源需要的格式。非 crypto 原样返回。"""
    upper = symbol.upper()
    if upper not in CRYPTO_SYMBOLS:
        return symbol
    if source == "finnhub":
        return CRYPTO_MAP_FINNHUB.get(upper, f"BINANCE:{upper}USDT")
    if source == "yahoo":
        return CRYPTO_MAP_YAHOO.get(upper, f"{upper}-USD")
    return symbol


def parse_symbols(raw: str) -> list[str]:
    if not raw:
        return []
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def classify(symbol: str) -> str:
    """
    返回该 Ticker 优先走哪个数据源（不考虑 Key 是否存在）。
      'finnhub' → 美股 / Crypto
      'tushare' → A股（上交所 .SS / 深交所 .SZ）
      'yahoo'   → 港股（.HK），以及所有市场的兜底
    """
    if symbol.endswith((".SS", ".SZ")):
        return "tushare"
    if symbol.endswith(".HK"):
        return "yahoo"
    # BTC/ETH 等 Crypto 及纯字母美股
    return "finnhub"


def run(cmd: list[str]) -> int:
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> None:
    load_dotenv()

    p = argparse.ArgumentParser(description="YMOS 价格路由器（四源三级分流）")
    p.add_argument("--symbols", required=True,
                   help="逗号分隔，如 AAPL,NIO,688008.SS,0700.HK")
    p.add_argument("--output-dir", default="Report/投资雷达/Raw_Data", help="输出目录")
    p.add_argument("--date-tag", default="", help="日期标签，如 20260316")
    p.add_argument("--finnhub-token", default="",
                   help="Finnhub Key（可选，也可通过 FINNHUB_API_KEY 环境变量传入）")
    p.add_argument("--tushare-token", default="",
                   help="Tushare Token（可选，也可通过 TUSHARE_TOKEN 环境变量传入）")
    args = p.parse_args()

    symbols = parse_symbols(args.symbols)
    if not symbols:
        raise SystemExit("symbols 不能为空")

    finnhub_key   = args.finnhub_token  or os.getenv("FINNHUB_API_KEY", "")
    tushare_token = args.tushare_token  or os.getenv("TUSHARE_TOKEN", "")

    # ── 分流 ────────────────────────────────────────────────────────────────
    finnhub_syms: list[str] = []
    tushare_syms: list[str] = []   # 有 Token 的 A股
    efinance_syms: list[str] = []  # 无 Token 或 Tushare 失败后的 A股
    yahoo_syms:   list[str] = []

    for s in symbols:
        bucket = classify(s)
        if bucket == "finnhub":
            if finnhub_key:
                finnhub_syms.append(s)
            else:
                yahoo_syms.append(s)          # 无 Key → Yahoo 兜底
        elif bucket == "tushare":
            if tushare_token:
                tushare_syms.append(s)        # 有 Token → Tushare 优先
            else:
                efinance_syms.append(s)       # 无 Token → efinance 备选
        else:  # "yahoo"
            yahoo_syms.append(s)

    out_dir = Path(args.output_dir).resolve()   # 转绝对路径，避免子进程 CWD 不一致
    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = args.date_tag or "latest"

    print(f"📡 价格路由分流结果：")
    print(f"   Finnhub  ({len(finnhub_syms)}): {finnhub_syms or '—'}")
    print(f"   Tushare  ({len(tushare_syms)}): {tushare_syms or '—'}")
    print(f"   efinance ({len(efinance_syms)}): {efinance_syms or '—'}")
    print(f"   Yahoo    ({len(yahoo_syms)}): {yahoo_syms or '—'}")
    print()

    # ── Crypto 归一化：裸符号 → 数据源专用格式 ────────────────────────────
    finnhub_syms_norm = [normalize_for_source(s, "finnhub") for s in finnhub_syms]

    if finnhub_syms_norm != finnhub_syms:
        print(f"🔄 Crypto 归一化：")
        print(f"   Finnhub: {finnhub_syms} → {finnhub_syms_norm}")
        print()

    # ── Finnhub ─────────────────────────────────────────────────────────────
    if finnhub_syms_norm:
        out = out_dir / f"price_scan_finnhub_{date_tag}.json"
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_price_api.py"),
            "--quotes-only",
            "--symbols", ",".join(finnhub_syms_norm),
            "--output", str(out),
            "--token", finnhub_key,
        ]
        code = run(cmd)
        if code != 0:
            print(f"⚠️ Finnhub 调用失败（exit {code}），对应 ticker 可能无价格数据")

    # ── Tushare（A股）───────────────────────────────────────────────────────
    tushare_failed_syms: list[str] = []
    if tushare_syms:
        out = out_dir / f"price_scan_tushare_{date_tag}.json"
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_price_tushare.py"),
            "--symbols", ",".join(tushare_syms),
            "--token", tushare_token,
            "--output", str(out),
        ]
        code = run(cmd)

        # 检查输出文件的成功率（而不只是 exit code）
        success_count = 0
        if out.exists():
            try:
                with open(out, "r", encoding="utf-8") as f:
                    result = json.load(f)
                data = result.get("data", [])
                success_count = sum(1 for x in data if x.get("ok"))
            except Exception:
                pass

        if code != 0 or success_count == 0:
            print(f"⚠️ Tushare 调用失败（exit {code}, 成功 {success_count}/{len(tushare_syms)}），将尝试 efinance 兜底")
            tushare_failed_syms = tushare_syms  # 全部回退到 efinance
        elif success_count < len(tushare_syms):
            # 部分成功：失败的 ticker 回退到 efinance
            failed = [s for s in tushare_syms if s not in [x.get("symbol") for x in data if x.get("ok")]]
            if failed:
                print(f"⚠️ Tushare 部分失败，{len(failed)} 个 ticker 将尝试 efinance: {failed}")
                tushare_failed_syms = failed

    # ── efinance（A股备选）─────────────────────────────────────────────────
    # 合并：无 Token 的 A股 + Tushare 失败的 A股
    all_efinance_syms = efinance_syms + tushare_failed_syms
    efinance_failed_syms: list[str] = []
    if all_efinance_syms:
        out = out_dir / f"price_scan_efinance_{date_tag}.json"
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_price_efinance.py"),
            "--symbols", ",".join(all_efinance_syms),
            "--output", str(out),
        ]
        code = run(cmd)
        if code != 0:
            print(f"⚠️ efinance 调用失败（exit {code}），将尝试 Yahoo 兜底")
            efinance_failed_syms = all_efinance_syms
            yahoo_syms.extend(efinance_failed_syms)

    # ── Yahoo（港股 + 兜底）─────────────────────────────────────────────────
    # 注意：yahoo_syms 可能在运行时被修改（添加 efinance 失败的 A股）
    # A股格式 .SS/.SZ 需要转换为 Yahoo 格式：.SS → .SS, .SZ → .SZ（Yahoo 支持）
    if yahoo_syms:
        yahoo_syms_norm = [normalize_for_source(s, "yahoo") for s in yahoo_syms]
        out = out_dir / f"price_scan_yahoo_{date_tag}.json"
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_price_yahoo.py"),
            "--symbols", ",".join(yahoo_syms_norm),
            "--output", str(out),
        ]
        code = run(cmd)
        if code != 0:
            print(f"⚠️ Yahoo 调用失败（exit {code}），对应 ticker 可能无价格数据")

    print("✅ 路由完成")


if __name__ == "__main__":
    main()
