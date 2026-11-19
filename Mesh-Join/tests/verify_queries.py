"""Cross-checks the trickiest OLAP queries against an independent pandas computation.

usage (env DWH_* set):  python verify_queries.py "<raw file used for the load>"
"""
import itertools
import os
import re
import sys
from collections import Counter
from decimal import Decimal

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import database as db                                                       # noqa: E402
from data_cleaning import classify, clean, load_raw                         # noqa: E402
from master_data import build_customer_master, build_product_master, store_id_for   # noqa: E402

creds = {"host": os.environ.get("DWH_HOST", "localhost"), "port": int(os.environ.get("DWH_PORT", 3306)),
         "database": os.environ["DWH_DB"], "user": os.environ["DWH_USER"], "password": os.environ["DWH_PASSWORD"]}
conn = db.connect(creds, autocommit=True)
cur = conn.cursor()
sql_text = re.sub(r"--[^\n]*", "", open(os.path.join(os.path.dirname(__file__), "..", "..", "Queries-DW",
                                                    "olap_queries.sql")).read())
stmts = [s.strip() for s in sql_text.split(";") if s.strip()]


def find(marker):
    return next(s for s in stmts if marker in s)


def run(stmt):
    cur.execute(stmt)
    return cur.fetchall()


bad = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        bad.append(name)


c, _ = clean(load_raw(sys.argv[1]))
c = classify(c)
cm = build_customer_master(c).set_index("customer_id")
pm = build_product_master(c).set_index("product_id")
c["revenue"] = [Decimal(p) * q for p, q in zip(c["unit_price_text"], c["quantity"])]
c["store_id"] = [store_id_for(t, m) for t, m in zip(c["transaction_id"], c["sales_mode"])]
for col in ["gender", "age_group", "occupation", "city_category", "stay_in_current_city_years", "marital_status"]:
    c[col] = c["customer_id"].map(cm[col])
for col in ["product_category", "supplier_id", "product_name"]:
    c[col + "_m"] = c["product_id"].map(pm[col])
t = c["event_time"]
c["year"], c["month"], c["quarter"], c["date"] = t.dt.year, t.dt.month, t.dt.quarter, t.dt.date
c["daytype"] = ["Weekend" if d >= 5 else "Weekday" for d in t.dt.weekday]
Y = c["year"].max()
cy = c[c["year"] == Y]

# Q16 product pairs: distinct carts
pairs = Counter()
for _, g in c.groupby("transaction_id")["product_id"]:
    for a, b in itertools.combinations(sorted(set(g)), 2):
        pairs[(a, b)] += 1
exp = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
got = [((r[0], r[2]), r[4]) for r in run(find("cart_items"))]
# ids sort identically only if the SQL tie-break equals python's; compare counts and membership
check("Q16 top-5 pair counts equal pandas", [v for _, v in exp] == [v for _, v in got])
check("Q16 pair ids are correct pairs (order inside pair normalised)",
      all(pairs[tuple(sorted(k))] == v for k, v in got))

# Q9 monthly growth, category
r = run(find("mom_growth_pct"))
m = cy.groupby(["product_category_m", "month"])["revenue"].sum().reset_index().sort_values(["product_category_m", "month"])
m["prev"] = m.groupby("product_category_m")["revenue"].shift()
exp9 = {(a, b): (None if pd.isna(p) else round(100 * float(v - p) / float(p), 2))
        for a, b, v, p in zip(m["product_category_m"], m["month"], m["revenue"], m["prev"])}
ok9 = all((exp9[(a, b)] is None and g is None) or (g is not None and abs(float(g) - exp9[(a, b)]) < 0.011)
          for a, b, _, _, g in r) and len(r) == len(exp9)
check("Q9 month-over-month growth % equals pandas", ok9)

# Q12 quarterly store growth
r = run(find("qoq_growth_pct\nFROM q") if any("qoq_growth_pct\nFROM q" in s and "store_id" in s for s in stmts)
        else next(s for s in stmts if "store_name" in s and "previous_quarter_revenue" in s))
q = cy.groupby(["store_id", "quarter"])["revenue"].sum().reset_index().sort_values(["store_id", "quarter"])
q["prev"] = q.groupby("store_id")["revenue"].shift()
exp12 = {(a, b): (None if pd.isna(p) else round(100 * float(v - p) / float(p), 2))
         for a, b, v, p in zip(q["store_id"], q["quarter"], q["revenue"], q["prev"])}
ok12 = len(r) == len(exp12) and all((exp12[(a, qn)] is None and g is None) or
                                    (g is not None and abs(float(g) - exp12[(a, qn)]) < 0.011)
                                    for a, _, qn, _, _, g in r)
check("Q12 quarterly store growth % equals pandas", ok12)

# Q1 / Q11 top-5 sets
top1 = {}
for dtp, g in cy.groupby("daytype"):
    s = g.groupby("product_id")["revenue"].sum().sort_values(ascending=False, kind="stable")
    top1[dtp] = set(s.head(5).index)
r1 = run(find("monthly_revenue"))
got1 = {}
for d, rk, pid, _, mo, mrev, yrev in r1:
    got1.setdefault(d, set()).add(pid)
check("Q1 top-5 products per day type equal pandas (revenue ties aside)",
      all(len(got1[d] ^ top1[d]) == 0 for d in top1))
r11 = run(find("month_num, r.day_type"))
ok11 = True
for (mo, dtp), g in cy.groupby(["month", "daytype"]):
    s = g.groupby("product_id")["revenue"].sum().sort_values(ascending=False, kind="stable").head(5)
    sqlset = {x[3] for x in r11 if x[0] == mo and x[1] == dtp}
    ok11 &= (sqlset == set(s.index))
check("Q11 monthly top-5 per day type equal pandas", ok11)

# Q5 top-5 occupations per category
r5 = run(find("ROW_NUMBER() OVER (PARTITION BY product_category ORDER BY total_sales"))
ok5 = True
for cat, g in c.groupby("product_category_m"):
    s = g.groupby("occupation")["revenue"].sum().sort_values(ascending=False, kind="stable").head(5)
    ok5 &= {x[2] for x in r5 if x[0] == cat} == set(s.index)
check("Q5 top-5 occupations per category equal pandas", ok5)

# Q17 ROLLUP totals
r17 = run(find("WITH ROLLUP"))
grand = [x for x in r17 if x[6] == "GRAND TOTAL"]
check("Q17 grand total == SUM(revenue)", len(grand) == 1 and grand[0][5] == c["revenue"].sum())
yr = {x[0]: x[5] for x in r17 if x[6] == "YEAR SUBTOTAL"}
check("Q17 year subtotals equal pandas", all(yr[y] == c[c["year"] == y]["revenue"].sum() for y in yr))
st = {(x[0], x[1]): x[5] for x in r17 if x[6] == "STORE SUBTOTAL"}
exp_st = c.groupby(["year", "store_id"])["revenue"].sum()
check("Q17 number of store subtotals = year x store combinations", len(st) == len(exp_st))

# Q19 spikes
r19 = run(find("times_average\nFROM d JOIN dim_product") if False else next(s for s in stmts if "times_average" in s))
d = c.groupby(["product_id", "date"])["revenue"].sum().reset_index()
d["avg"] = d.groupby("product_id")["revenue"].transform(lambda s: sum(s) / len(s))
exp19 = d[d["revenue"] > 2 * d["avg"]]
check(f"Q19 spike days count equals pandas ({len(r19)})", len(r19) == len(exp19))

# Q6 latest six months
r6 = run(find("- 6"))
months = sorted({(y, mo) for y, mo in zip(c["year"], c["month"])})[-6:]
exp6 = c[[(y, mo) in months for y, mo in zip(c["year"], c["month"])]].groupby(
    ["year", "month", "city_category", "marital_status"])["revenue"].sum()
check("Q6 latest six months rows/total equal pandas",
      len(r6) == len(exp6) and sum(x[4] for x in r6) == exp6.sum())

# Q15 volatility
r15 = run(next(s for s in stmts if "volatility_pct" in s))
sup = c.assign(sid=c["supplier_id_m"]).groupby(["store_id", "sid", "year", "month"])["revenue"].sum().reset_index()
check("Q15 row count equals distinct (store, supplier, month) groups", len(r15) == len(sup))
run(find("CREATE OR REPLACE VIEW"))
check("Q20 view returns one row per store x quarter",
      len(run("SELECT * FROM STORE_QUARTERLY_SALES")) == c.groupby(["store_id", "year", "quarter"]).ngroups)
print("\nRESULT:", "ALL QUERY CHECKS PASSED" if not bad else f"FAILED: {bad}")
sys.exit(1 if bad else 0)
