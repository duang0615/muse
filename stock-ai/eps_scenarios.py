#!/usr/bin/env python3
"""用 FinMind 實績資料,計算個股「未來兩年 EPS」的機械式情境(保守/基準/樂觀)。

這不是法人預估,而是用公開、可調整的假設推出的區間,給自家 AI 當「自有數字」:
- 已公布財報的季度:直接用實際 EPS
- 已公布月營收、但財報未出的季度:實際月營收 × 淨利率假設 ÷ 股數
- 尚未公布的月份:近 3 個月平均月營收 × 營收係數
- Y2 營收:Y1 營收 × (1 + Y1 營收年增率 × 成長係數),成長係數預設遞減
- 淨利率假設:近 4 季歸屬母公司淨利率的 最低/平均/最高
- 財報結算日之後才除權的股票股利:所有 EPS 換算成新股數(例如 6669 緯穎)
- 有虧損季或業外占比大:另外輸出本業 EPS(營業利益 × (1 − 稅率) ÷ 股數)

用法:
  python3 eps_scenarios.py 2330
  python3 eps_scenarios.py 2330 --name 台積電 --today 2026-11-02
環境變數 FINMIND_TOKEN:有 token 時帶入,額度較高。
僅用標準函式庫。
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date

API = "https://api.finmindtrade.com/api/v4/data"

# 情境參數:要調整就改這裡,並在回答裡揭露
SCENARIOS = ("保守", "基準", "樂觀")
REVENUE_FACTOR = {"保守": 0.95, "基準": 1.00, "樂觀": 1.05}  # 未公布月份 vs 近 3 月平均
GROWTH_FACTOR = {"保守": 0.50, "基準": 0.75, "樂觀": 1.00}   # Y2 成長率 = Y1 年增率 × 係數
TAX_RATE = 0.20               # 本業 EPS 用的稅率假設(台灣營所稅 20%)
PAR_VALUE = 10.0              # 面額假設;配股 X 元 = 每股配 X / 10 股
NONOP_WARN_RATIO = 0.30       # 業外占稅前淨利超過此比例 → 要分本業與業外
SHARE_CHANGE_WARN = 0.05      # 近 4 季推算股數變動超過此比例 → 股數基礎提醒

QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def fetch(dataset, stock_id, start_date):
    q = urllib.parse.urlencode({"dataset": dataset, "data_id": stock_id, "start_date": start_date})
    req = urllib.request.Request(f"{API}?{q}")
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset} 失敗:{body.get('msg')}")
    return body["data"]


def parse_quarters(statements):
    """FinMind 綜合損益表(單季)→ {(年, 季): {欄位: 值}}。"""
    out = {}
    for r in statements:
        y, m, _ = r["date"].split("-")
        out.setdefault((int(y), (int(m) - 1) // 3 + 1), {})[r["type"]] = r["value"]
    return out


def parse_months(monthly):
    """FinMind 月營收 → {(年, 月): 營收}。"""
    return {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in monthly}


def share_events_after(dividends, after, until):
    """財報結算日之後、今天以前除權的股票股利 → [(除權日, 每股配股元, 股數倍數)]。"""
    events = []
    for r in dividends or []:
        ex = r.get("StockExDividendTradingDate") or ""
        if not ex:
            continue
        ex_date = date.fromisoformat(ex)
        per_share = (r.get("StockEarningsDistribution") or 0) + (r.get("StockStatutorySurplus") or 0)
        if per_share > 0 and after < ex_date <= until:
            events.append((ex, per_share, 1 + per_share / PAR_VALUE))
    return sorted(events)


def quarter_stats(q):
    rev = q.get("Revenue")
    ni = q.get("EquityAttributableToOwnersOfParent")
    eps = q.get("EPS")
    pre = q.get("PreTaxIncome")
    nonop = q.get("TotalNonoperatingIncomeAndExpense")
    op = q.get("OperatingIncome")
    shares = ni / eps if ni and eps else None
    return {
        "revenue": rev,
        "eps": eps,
        "net_margin": ni / rev if rev and ni is not None else None,
        "business_margin": op * (1 - TAX_RATE) / rev if rev and op is not None else None,
        "shares": shares,
        "nonop_ratio": nonop / pre if pre and nonop is not None else None,
    }


def build(stock_id, stock_name, quarters, months, today, dividends=None):
    y1, y2 = today.year, today.year + 1
    reported = sorted(quarters)
    if len(reported) < 4:
        raise ValueError("財報少於 4 季,無法計算")
    stats = {k: quarter_stats(quarters[k]) for k in reported}
    last4 = [stats[k] for k in reported[-4:]]
    margins = [s["net_margin"] for s in last4 if s["net_margin"] is not None]
    biz_margins = [s["business_margin"] for s in last4 if s["business_margin"] is not None]
    old_shares = last4[-1]["shares"]
    if not margins or not old_shares:
        raise ValueError("缺淨利或 EPS,無法推算淨利率與股數")
    margin = {"保守": min(margins), "基準": sum(margins) / len(margins), "樂觀": max(margins)}
    biz_margin = sum(biz_margins) / len(biz_margins) if biz_margins else None

    month_keys = sorted(months)
    last_month = month_keys[-1]
    recent3 = [months[k] for k in month_keys[-3:]]
    recent_avg = sum(recent3) / len(recent3)

    y0_months = [v for (yy, _), v in months.items() if yy == y1 - 1]
    if len(y0_months) != 12:
        raise ValueError(f"{y1 - 1} 年月營收只有 {len(y0_months)} 個月,無法算年增率")
    y0_rev = sum(y0_months)

    # 股數基礎:財報結算日之後的配股,全部 EPS 換算成新股數
    last_q = reported[-1]
    stmt_end = date(last_q[0], *QUARTER_END[last_q[1]])
    events = share_events_after(dividends, stmt_end, today)
    mult = 1.0
    for _, _, m in events:
        mult *= m
    shares = old_shares * mult

    flags, notes = [], []
    if events:
        flags.append("share_base_changed")
        desc = "、".join(f"{ex} 除權每股配股 {ps:.2f} 元" for ex, ps, _ in events)
        notes.append(f"已依 {desc},把所有 EPS 換算成新股數(倍數 {mult:.3f});舊股數基礎的預估不可直接比較。")
    share_list = [s["shares"] for s in last4 if s["shares"]]
    if (max(share_list) - min(share_list)) / max(share_list) > SHARE_CHANGE_WARN:
        flags.append("share_drift_in_statements")
        notes.append("近 4 季推算股數變動超過 5%,可能有增資或配股,請確認股數基礎。")
    nonop = [s["nonop_ratio"] for s in last4 if s["nonop_ratio"] is not None]
    if nonop and max(abs(x) for x in nonop) > NONOP_WARN_RATIO:
        flags.append("nonoperating_large")
        notes.append("近 4 季業外占稅前淨利超過 30%,本業與業外要分開看。")
    if min(margins) < 0:
        flags.append("loss_quarters")
        notes.append("近 4 季有虧損季度,情境 EPS 可能為負。")

    def y1_calc(net_margin, rev_factor, business=False):
        eps_total, rev_total = 0.0, 0.0
        for qn in range(1, 5):
            if (y1, qn) in stats:
                st = stats[(y1, qn)]
                if business:
                    eps_total += st["business_margin"] * st["revenue"] / st["shares"] / mult
                else:
                    eps_total += st["eps"] / mult
                rev_total += st["revenue"]
                continue
            q_rev = sum(months.get((y1, m), recent_avg * rev_factor) for m in range(qn * 3 - 2, qn * 3 + 1))
            rev_total += q_rev
            eps_total += q_rev * net_margin / shares
        return eps_total, rev_total

    g = y1_calc(margin["基準"], REVENUE_FACTOR["基準"])[1] / y0_rev - 1

    y1_eps, y2_eps = {}, {}
    for sc in SCENARIOS:
        eps1, rev1 = y1_calc(margin[sc], REVENUE_FACTOR[sc])
        y1_eps[sc] = eps1
        y2_eps[sc] = rev1 * (1 + g * GROWTH_FACTOR[sc]) * margin[sc] / shares

    business = {}
    if biz_margin is not None and ({"nonoperating_large", "loss_quarters"} & set(flags)):
        b1, rev1 = y1_calc(biz_margin, REVENUE_FACTOR["基準"], business=True)
        business = {y1: b1, y2: rev1 * (1 + g * GROWTH_FACTOR["基準"]) * biz_margin / shares}

    y0_full = all((y1 - 1, qn) in stats for qn in range(1, 5))
    y0_eps = sum(stats[(y1 - 1, qn)]["eps"] for qn in range(1, 5)) / mult if y0_full else None
    ttm_eps = sum(s["eps"] for s in last4) / mult

    def entry(year, rel, eps_map, yoy_base):
        base = eps_map["基準"]
        note = "保守/基準/樂觀 = " + " / ".join(f"{eps_map[k]:.2f}" for k in SCENARIOS)
        if events:
            note += f";換算前(舊股數)基準約 {base * mult:.2f} 元"
        return {
            "absolute_year": year,
            "relative_year": rel,
            "is_actual": False,
            "range_low": round(min(eps_map.values()), 2),
            "range_high": round(max(eps_map.values()), 2),
            "representative": round(base, 2),
            "median": None,
            "average": None,
            "yoy": f"{(base / yoy_base - 1) * 100:.1f}%" if yoy_base and yoy_base > 0 else None,
            "eps_note": note,
            "eps_business": round(business[year], 2) if year in business else None,
            "share_base_note": notes[0] if events else None,
        }

    return {
        "stock_code": stock_id,
        "stock_name": stock_name,
        "method": "機械式情境(FinMind 實績 + 公開假設),非法人預估",
        "as_of": today.isoformat(),
        "data_through": {
            "statements": f"{last_q[0]}Q{last_q[1]}",
            "monthly_revenue": f"{last_month[0]}-{last_month[1]:02d}",
        },
        "sources": [
            {"title": "FinMind 綜合損益表 TaiwanStockFinancialStatements", "url": API},
            {"title": "FinMind 月營收 TaiwanStockMonthRevenue", "url": API},
            {"title": "FinMind 股利政策表 TaiwanStockDividend", "url": API},
        ],
        "actuals": {
            "ttm_eps": round(ttm_eps, 2),
            f"eps_{y1 - 1}": round(y0_eps, 2) if y0_eps is not None else None,
            f"revenue_{y1 - 1}": y0_rev,
            "last4_quarters": [
                {"quarter": f"{k[0]}Q{k[1]}",
                 **{kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in stats[k].items()}}
                for k in reported[-4:]
            ],
        },
        "assumptions": {
            "net_margin": {k: round(v, 4) for k, v in margin.items()},
            "business_margin_avg": round(biz_margin, 4) if biz_margin is not None else None,
            "revenue_factor_unreported_months": REVENUE_FACTOR,
            "recent_3m_avg_revenue": round(recent_avg),
            f"revenue_yoy_{y1}_base": f"{g * 100:.1f}%",
            "y2_growth_factor": GROWTH_FACTOR,
            "shares": round(shares),
            "share_multiplier": round(mult, 4),
            "tax_rate_for_business_eps": TAX_RATE,
        },
        "table_years": [y1, y2],
        "eps_by_year": [
            entry(y1, "Y1", y1_eps, y0_eps),
            entry(y2, "Y2", y2_eps, y1_eps["基準"]),
        ],
        "flags": flags,
        "notes": notes,
    }


def run(stock_id, name="", today=None):
    today = today or date.today()
    start = f"{today.year - 2}-01-01"
    quarters = parse_quarters(fetch("TaiwanStockFinancialStatements", stock_id, start))
    months = parse_months(fetch("TaiwanStockMonthRevenue", stock_id, start))
    dividends = fetch("TaiwanStockDividend", stock_id, start)
    return build(stock_id, name, quarters, months, today, dividends)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stock_id")
    ap.add_argument("--name", default="")
    ap.add_argument("--today", help="YYYY-MM-DD,預設今天")
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else None
    json.dump(run(args.stock_id, args.name, today), sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
