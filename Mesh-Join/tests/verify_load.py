"""Verifies a finished load independently of the loader code.

usage (env DWH_* as for main.py):  python verify_load.py "<raw file>"
Re-cleans the raw file and compares it, row by row, with fact_sales + dimensions.
"""
import os
import sys
from collections import Counter
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import database as db                                                        # noqa: E402
from data_cleaning import classify, clean, load_raw                          # noqa: E402
from master_data import build_customer_master, build_product_master, md5_int, store_id_for   # noqa: E402

creds = {"host": os.environ.get("DWH_HOST", "localhost"), "port": int(os.environ.get("DWH_PORT", 3306)),
         "database": os.environ["DWH_DB"], "user": os.environ["DWH_USER"], "password": os.environ["DWH_PASSWORD"]}
conn = db.connect(creds, autocommit=True)
cur = conn.cursor()
bad = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")
    if not cond:
        bad.append(name)


cleaned, st = clean(load_raw(sys.argv[1]))
cleaned = classify(cleaned)
n = len(cleaned)

cur.execute("SELECT COUNT(*), COUNT(DISTINCT source_key), SUM(revenue) FROM fact_sales")
cnt, dist, rev = cur.fetchone()
check("fact rows == cleaned rows", cnt == n, f"({cnt:,} vs {n:,})")
check("no duplicate facts (distinct source_key)", dist == cnt)
exp_rev = sum(Decimal(p) * q for p, q in zip(cleaned["unit_price_text"], cleaned["quantity"]))
check("SUM(revenue) == SUM(quantity x unit_price) from the source", rev == exp_rev, f"({rev} vs {exp_rev})")

cur.execute("SELECT COUNT(*) FROM fact_sales WHERE revenue <> quantity * unit_price")
check("revenue = quantity x unit_price on every fact row", cur.fetchone()[0] == 0)

cur.execute("SELECT sales_mode, COUNT(*) FROM fact_sales GROUP BY sales_mode")
by_mode = dict(cur.fetchall())
exp_mode = cleaned["sales_mode"].value_counts().to_dict()
check("online / periodic counts match the MD5 rule", by_mode == exp_mode, f"{by_mode}")
cur.execute("SELECT processing_mode, sales_mode, COUNT(*) FROM fact_sales GROUP BY 1,2")
print("        processing_mode x sales_mode:", cur.fetchall())

cur.execute("SELECT f.transaction_id, p.product_id, f.quantity, f.unit_price FROM fact_sales f "
            "JOIN dim_product p ON p.product_key = f.product_key")
got = Counter((a, b, int(c), Decimal(d)) for a, b, c, d in cur.fetchall())
exp = Counter((a, b, int(c), Decimal(d)) for a, b, c, d in
              zip(cleaned["transaction_id"], cleaned["product_id"], cleaned["quantity"], cleaned["unit_price_text"]))
check("(transaction, product, quantity, price) multiset identical to the cleaned file", got == exp)

cm = build_customer_master(cleaned).set_index("customer_id")
pm = build_product_master(cleaned).set_index("product_id")
cur.execute("SELECT customer_id, gender, age_group, occupation, city_category, stay_in_current_city_years, "
            "marital_status FROM dim_customer")
rows = cur.fetchall()
check("dim_customer == deterministic customer master", len(rows) == len(cm) and all(
    tuple(cm.loc[r[0], ["gender", "age_group", "occupation", "city_category",
                        "stay_in_current_city_years", "marital_status"]]) == r[1:] for r in rows))
cur.execute("SELECT product_id, product_name, product_category, supplier_id FROM dim_product")
rows = cur.fetchall()
check("dim_product == deterministic product master", len(rows) == len(pm) and all(
    tuple(pm.loc[r[0], ["product_name", "product_category", "supplier_id"]]) == r[1:] for r in rows))

cur.execute("SELECT f.transaction_id, s.store_id, f.sales_mode, c.customer_id, t.event_time FROM fact_sales f "
            "JOIN dim_store s ON s.store_key=f.store_key JOIN dim_customer c ON c.customer_key=f.customer_key "
            "JOIN dim_time t ON t.time_key=f.time_key")
sbad = cbad = tbad = 0
src = cleaned.drop_duplicates("transaction_id").set_index("transaction_id")
for tid, sid, mode, cid, et in cur.fetchall():
    sbad += (sid != store_id_for(tid, mode))
    r = src.loc[tid]
    cbad += (cid != r["customer_id"])
    tbad += (et != r["event_time"].to_pydatetime())
check("store_id follows the MD5 store rule (all rows)", sbad == 0)
check("customer and event_time on the fact equal the source", cbad == 0 and tbad == 0)

cur.execute("SELECT * FROM dim_time")
tb = 0
for (_, et, d, h, dom, mo, q, y, dow, dt, season, half) in cur.fetchall():
    exp_season = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring", 6: "Summer",
                  7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}[et.month]
    tb += not (d == et.date() and h == et.hour and dom == et.day and mo == et.month and q == (et.month - 1) // 3 + 1
               and y == et.year and dow == et.strftime("%A") and dt == ("Weekend" if et.weekday() >= 5 else "Weekday")
               and season == exp_season and half == ("H1" if et.month <= 6 else "H2"))
check("dim_time parts (quarter, season, half, weekday/weekend ...) correct", tb == 0)

cur.execute("SELECT COUNT(*) FROM dim_store")
check("exactly five stores", cur.fetchone()[0] == 5)
cur.execute("SELECT COUNT(*), MIN(c), MAX(c) FROM (SELECT release_hour, COUNT(*) c FROM periodic_sales_data GROUP BY 1) g")
g = cur.fetchone()
check("48 periodic groups in staging", g[0] == 48, f"(min {g[1]}, max {g[2]})")
cur.execute("SELECT COUNT(*) FROM periodic_sales_data WHERE released = 0")
check("all 48 groups released exactly once", cur.fetchone()[0] == 0)
cur.execute("SELECT COUNT(*) FROM live_sales_cache")
cache_left = cur.fetchone()[0]
check("live_sales_cache empty (all copied)", cache_left == 0)
cur.execute("SELECT COUNT(*), SUM(replayed) FROM shed_table")
a, b = cur.fetchone()
check("every shed transaction was loaded", (a or 0) == int(b or 0), f"(shed {a:,}, loaded {int(b or 0):,})")
cur.execute("SELECT shed_speed, COUNT(*) FROM shed_table GROUP BY 1")
print("        shed rows by speed:", cur.fetchall())
cur.execute("SELECT COUNT(*) FROM fact_sales f JOIN shed_table s ON s.source_key = f.source_key "
            "WHERE f.processing_mode <> 'Shed-Replay' OR f.sales_mode <> 'Periodic'")
check("only periodic transactions were ever shed", cur.fetchone()[0] == 0)
print("\nRESULT:", "ALL CHECKS PASSED" if not bad else f"FAILED: {bad}")
sys.exit(1 if bad else 0)
