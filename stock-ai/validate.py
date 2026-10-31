#!/usr/bin/env python3
"""檢查自家 AI 的結構化輸出是否符合 財報狗規則.md。

用法:python3 -I validate.py output.json
結束碼:0 = 無錯誤(可有警告);1 = 有錯誤。
僅用標準函式庫。
"""
import json
import sys
from datetime import date

FIXED_QUESTIONS = [
    "公司主要做什麼", "產業前景概況", "市場在看哪些題材", "主要客戶是誰",
    "產品營收佔比", "競爭對手與優劣勢", "公司後續展望", "未來兩年預估 EPS",
]
STAT_KEYS = ("median", "average", "representative")


def validate(d, today=None):
    """回傳 (errors, warnings)。errors 為違反硬規則,warnings 為需人工確認。"""
    errors, warns = [], []
    today = today or date.today()

    # H4 追問 3 個
    followups = d.get("followups") or []
    if len(followups) != 3:
        errors.append(f"H4 追問必須 3 個,實際 {len(followups)} 個")

    # H5 / H6 更多問題固定 8 題,且不與追問混合
    fq = d.get("fixed_questions") or []
    if fq != FIXED_QUESTIONS:
        errors.append("H5 fixed_questions 必須是固定 8 題且順序相同")
    followup_texts = {f.get("text") for f in followups if isinstance(f, dict)}
    if followup_texts & set(fq):
        errors.append("H6 追問與固定問題混在一起")

    # H2 參考資料:公開資訊要有標題與連結;不要用內部數量補
    refs = d.get("references") or {}
    public = refs.get("public") or []
    if not public:
        warns.append("H2 公開資訊為空,應標 public_references_missing")
    for i, r in enumerate(public):
        if not r.get("title") or not r.get("url"):
            errors.append(f"H2 公開資訊第 {i + 1} 筆缺標題或連結")

    # 免責
    if not (d.get("disclaimer") or "").strip():
        errors.append("缺免責與資料來源標示(財報狗原文沒有,自家 AI 要加)")

    # C2 年份:eps_by_year 要與 table_years 一致
    years = d.get("table_years") or []
    eps = d.get("eps_by_year") or []
    eps_years = [e.get("absolute_year") for e in eps]
    if years != eps_years:
        errors.append(f"C2 table_years {years} 與 eps_by_year {eps_years} 不一致")
    for y in years:
        if not (today.year - 1 <= y <= today.year + 4):
            warns.append(f"年份 {y} 離今年過遠,請確認")

    # relative_year 須為 Y1..Yn,且依序(實績列不計)
    forecast = [e for e in eps if not e.get("is_actual")]
    for i, e in enumerate(forecast, start=1):
        if e.get("relative_year") != f"Y{i}":
            errors.append(f"{e.get('absolute_year')} relative_year 應為 Y{i}")

    # 追問年份必須屬於主表年份(財報狗的 bug,自家不可重現)
    for f in followups:
        fy = f.get("year") if isinstance(f, dict) else None
        if fy is not None and fy not in years:
            errors.append(f"追問年份 {fy} 不在主表年份 {years}(followup_year_mismatch)")

    # C3 / C4 每年要有區間 + 中位/代表,不收斂成單點
    for e in forecast:
        y = e.get("absolute_year")
        lo, hi = e.get("range_low"), e.get("range_high")
        has_range = lo is not None and hi is not None
        has_stat = any(e.get(k) is not None for k in STAT_KEYS)
        if not has_range:
            errors.append(f"C3 {y} 缺區間")
        if not has_stat:
            errors.append(f"C3 {y} 缺中位數/平均/代表值")
        if has_range and lo > hi:
            errors.append(f"{y} range_low {lo} > range_high {hi}")
        if has_range:
            for k in STAT_KEYS:
                v = e.get(k)
                if v is not None and not (lo <= v <= hi):
                    warns.append(f"{y} {k}={v} 不在區間 [{lo}, {hi}]")

    # C9 虧損:要分本業與業外
    for e in forecast:
        vals = [e.get(k) for k in STAT_KEYS + ("range_low", "range_high")]
        if any(v is not None and v < 0 for v in vals):
            if e.get("eps_business") is None:
                errors.append(f"C9 {e.get('absolute_year')} 為虧損預估,缺 eps_business")
            if e.get("eps_with_disposal") is not None and not e.get("disposal_note"):
                warns.append(f"C9 {e.get('absolute_year')} 有業外收益數字但缺 disposal_note")

    return errors, warns


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    errors, warns = validate(data)
    for w in warns:
        print("警告:", w)
    for e in errors:
        print("錯誤:", e)
    print(f"{len(errors)} 錯誤,{len(warns)} 警告")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
