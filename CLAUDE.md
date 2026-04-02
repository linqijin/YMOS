# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 项目概述

YMOS V3（勇麦投资操作系统）—— 自然语言驱动的人机协作投资研究系统。核心不是代码，而是投资思路的结构化表达：轻脚本 + 重 SOP + 软路由。

## 三模块架构

```
Eyes（眼睛）→ Brain（大脑）→ 持仓与关注（状态层）
```

| 模块 | 职责 | 关键产出 |
|:---|:---|:---|
| `Eyes/` | 盯市场：市场洞察 + 投资雷达 | 市场日报 + 桥接报告 |
| `Brain/` | 做分析：策略路由 + P系列提示词 | 策略报告 + 日志 |
| `持仓与关注/` | 管状态：状态机 + 个股文件夹 | 状态真相源 |

## 运行命令

**无构建/测试命令** — 这是 Markdown + Python 脚本项目，非传统代码项目。

### 数据脚本（Eyes/scripts/）

```bash
# RSS 市场数据抓取
python Eyes/scripts/fetch_rss.py 1 --output output.json

# 价格路由（三源分流：Finnhub → Tushare → Yahoo）
python Eyes/scripts/fetch_price_router.py --symbols AAPL,NIO,0700.HK --output-dir ./out

# A股价格（需 TUSHARE_TOKEN，无 Token 自动用 efinance）
python Eyes/scripts/fetch_price_tushare.py --symbols 688008.SS --token YOUR_TOKEN --output out.json

# Yahoo 价格（兜底，无需 Key）
python Eyes/scripts/fetch_price_yahoo.py --symbols AAPL,0700.HK --output out.json

# Finnhub 新闻（需 FINNHUB_API_KEY）
python Eyes/scripts/fetch_finnhub_news.py --hours 24 --output out.json

# efinance A股价格（无需 Token，备选方案）
python -c "import efinance as ef; print(ef.stock.get_quote_history('002549'))"
```

### 环境变量

复制 `.env.example` 为 `.env`，填入 API Key（均为可选，无 Key 自动降级）：

| 变量 | 用途 | 降级方案 |
|:---|:---|:---|
| `FINNHUB_API_KEY` | 美股/Crypto 实时报价 + 新闻 | Yahoo Finance |
| `TUSHARE_TOKEN` | A股日线价格 | efinance → Yahoo |
| `YMOS_MARKET_API_KEY` | 结构化市场事件 API | RSS 免费源 |

**无需 Token 的数据源**：
- efinance（A股）：`pip install efinance`，直接调用东方财富数据
- Yahoo Finance：美股/港股/部分A股，无需注册
- RSS：财经新闻聚合，开箱即用

## 暗号触发系统

用户通过自然语言「暗号」触发 SOP 工作流：

| 暗号 | 功能 | SOP |
|:---|:---|:---|
| `开始使用` | 入职引导 | `持仓与关注/SOP_入职引导.md` |
| `跑一下市场洞察` | 市场日报 | `Eyes/SOP_市场洞察.md` |
| `跑一下投资雷达` | 桥接报告 | `Eyes/SOP_投资雷达.md` |
| `跑一下策略分析` | 策略路由 | `Brain/SOP_策略分析.md` |
| `调研一下 [ticker]` | 深度调研 | `Brain/SOP_初始调研.md` |
| `关注/建仓/清仓 [ticker]` | 标的管理 | `持仓与关注/SOP_标的管理.md` |
| `收口一下` | 持仓视图刷新 | `持仓与关注/SOP_持仓收口.md` |

**入口文件**：`总入口暗号.md`

## 核心文件

### 灵魂文件：`持仓与关注/当前关注方向与投资偏好.md`

整个系统最重要的上下文锚点。包含：
- 投资者画像（风险承受度、周期、心理弱点）
- 仓位配置框架（PVE/PVP/现金）
- 板块关联逻辑
- 核心心法与禁忌

**每次新会话必须读取**。Agent 不得静默修改此文件，所有修改需用户确认。

### 状态机

- `持仓与关注/持仓_状态机.md` — 持仓真相源
- `持仓与关注/Watchlist_状态机.md` — 关注真相源

写回规则：每次更新必须同时完成 → 更新时间戳 + 更新对应行 + 追加变更日志。

## P 系列提示词

`Brain/references/` 下的 P1-P16 是投资决策框架：

| 编号 | 名称 | 用途 |
|:---|:---|:---|
| P1 | Genesis | 个股建档 |
| P2 | Phase Check | 阶段判断 |
| P4 | Radar | 个股雷达 |
| P5 | FOMO Killer | FOMO 审计 |
| P6 | Profit Keeper | 利润守门员 |
| P9 | Valuation | 估值分析 |
| P12 | Referee | 纪律审查 |
| P13 | Market Scanner | 市场扫描 |

## 文件权限

### 只读
- `Eyes/SOP_*.md`、`Brain/SOP_*.md`、`持仓与关注/SOP_*.md`
- `Brain/references/*.md`
- `Eyes/scripts/*.py`

### Human-in-the-Loop（需用户确认）
- `持仓与关注/当前关注方向与投资偏好.md`

### 可写（遵循 SOP 规则）
- `Eyes/市场洞察/`、`Eyes/投资雷达/`
- `Brain/策略分析/`
- `持仓与关注/*_状态机.md`、`持仓与关注/*/个股基础知识库.md`

## 价格路由器

三源自动分流逻辑：

```
美股/Crypto → Finnhub（有 Key）→ Yahoo（无 Key 兜底）
A股 (.SS/.SZ) → Tushare（有 Token）→ efinance（无 Token 兜底）→ Yahoo（最终兜底）
港股 (.HK) → Yahoo（固定）
```

Crypto 符号归一化：状态机写裸符号（BTC/ETH），路由器自动转换为数据源格式（BINANCE:BTCUSDT 或 BTC-USD）。

### A股数据源优先级

1. **Tushare Pro**（首选）— 需 Token，数据最完整
2. **efinance**（备选）— 无需 Token，基于东方财富，开箱即用
   ```python
   import efinance as ef
   df = ef.stock.get_quote_history('002549')  # 获取历史K线
   ```
3. **Yahoo Finance**（最终兜底）— 科创板数据可能不稳定

## 日常运行

```
每日收盘后：
1. 跑一下市场洞察 → Eyes 扫描市场
2. 跑一下投资雷达 → 桥接报告（趋势+价格+建议）
3. 跑一下策略分析 → Brain 处理雷达建议
4. 收口一下 → 刷新持仓备忘录视图
```

## 关键原则

1. **Human in the Loop**：AI 永远不替用户做最终决策，只给建议
2. **P12 纪律审查不可绕过**：买卖建议必须经过 P12
3. **不跳过 P2 就进 P5/P6**：必须先判断当前阶段
4. **个股分析读整个文件夹**：个股文件夹下所有文件都是上下文
5. **同日报告覆盖**：不加 `_v2` 后缀

## 扩展指南

新增数据源流程：
1. 数据脚本 → `Eyes/scripts/fetch_xxx.py`（输出 JSON）
2. 处理提示词 → `Brain/references/xxx.md`（如需要）
3. 更新 `.env.example` + `进阶指南.md` + `README.md`

### 添加 efinance A股价格脚本示例

```python
#!/usr/bin/env python3
"""efinance A股价格拉取（Tushare 备选方案）"""
import efinance as ef
import json
from pathlib import Path

def fetch_aStock_price(codes: list[str]) -> dict:
    """批量获取A股价格，codes 为纯数字代码如 ['002549', '600744']"""
    results = []
    for code in codes:
        df = ef.stock.get_quote_history(code, klt=101)  # 101=日K
        if not df.empty:
            latest = df.iloc[-1]
            results.append({
                "symbol": code,
                "last_close": float(latest['收盘']),
                "pct_chg": float(latest['涨跌幅']),
                "trade_date": latest['日期'],
            })
    return {"source": "efinance", "data": results}
```