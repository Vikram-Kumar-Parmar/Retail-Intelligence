"""Focused tests. Uses SYNTHETIC data labelled as such; needs DWH_* env vars for the DB tests.

run:  cd Mesh-Join && DWH_DB=... DWH_USER=... DWH_PASSWORD=... python ../tests/test_core.py
"""
import hashlib
import os
import random
import sys
from decimal import Decimal
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import CREATE_DW_SQL                                        # noqa: E402
from data_cleaning import clean, classify, load_raw                     # noqa: E402
from master_data import (build_customer_master, build_product_master, md5_int,   # noqa: E402
                         sales_mode_for, stable, store_id_for)
import database as db                                                   # noqa: E402
from meshjoin import MeshJoin, Txn                                      # noqa: E402

SYNTH = os.environ.get("SYNTH_FILE", "/tmp/synth/Online Retail.csv")
ok = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    ok.append(name)
    print(f"  PASS  {name}")


# ---------------- pure-Python tests -----------------------------------
raw = load_raw(SYNTH)
cleaned, st = clean(raw)
cleaned = classify(cleaned)

check("md5_int matches hashlib spec (first 8 hex chars)",
      md5_int("536365") == int(hashlib.md5(b"536365").hexdigest()[:8], 16))
check("stable() is deterministic", stable("12345age", 7) == stable("12345age", 7))
check("sales_mode: remainder 0-6 online, 7-9 periodic",
      all((sales_mode_for(t) == "Online") == (md5_int(t) % 10 <= 6) for t in cleaned["transaction_id"].unique()))
check("same invoice -> one sales_mode for all its lines",
      (cleaned.groupby("transaction_id")["sales_mode"].nunique() == 1).all())
share = (cleaned["sales_mode"] == "Online").mean()
check(f"online share about 70% ({share:.3f})", 0.65 < share < 0.75)
check("store ids: online=1, physical in 2..5",
      all(store_id_for(t, "Online") == 1 for t in ["1", "2"]) and
      all(2 <= store_id_for(t, "Periodic") <= 5 for t in cleaned["transaction_id"].unique()))
check("cleaning: no bad rows remain",
      (cleaned["quantity"] > 0).all() and (cleaned["unit_price_text"].map(float) > 0).all()
      and cleaned["customer_id"].notna().all() and not cleaned["transaction_id"].str.startswith("C").any())
check("cleaning: counts consistent", st["records_before_cleaning"] - st["records_removed_total"] == len(cleaned))
cm1, pm1 = build_customer_master(cleaned), build_product_master(cleaned)
cm2, pm2 = build_customer_master(cleaned.sample(frac=1, random_state=1).sort_index()), build_product_master(cleaned)
check("customer master deterministic & unique ids", cm1.equals(cm2) and cm1["customer_id"].is_unique)
check("product master deterministic & unique ids (case-sensitive)", pm1.equals(pm2) and pm1["product_id"].is_unique
      and {"85123A", "85123a"} <= set(pm1["product_id"]))
check("product name = UNKNOWN when no description",
      (pm1["product_name"] == "UNKNOWN").any() and (pm1["product_name"] != "").all())
lens = cleaned.assign(d=cleaned["product_name"].fillna("").str.strip().str.len()).groupby("product_id")["d"].max()
check("product name is the longest description",
      all(len(n) == lens[p] or n == "UNKNOWN" for p, n in zip(pm1["product_id"], pm1["product_name"])))
check("pages of 500 (last may be partial)", cm1["page_no"].max() + 1 == -(-len(cm1) // 500))

# ---------------- MESHJOIN vs plain join -------------------------------
creds = {"host": os.environ.get("DWH_HOST", "localhost"), "port": int(os.environ.get("DWH_PORT", 3306)),
         "database": os.environ["DWH_DB"], "user": os.environ["DWH_USER"], "password": os.environ["DWH_PASSWORD"]}
conn = db.connect(creds)
db.run_script(conn, CREATE_DW_SQL)
db.load_master_tables(conn, cm1, pm1)
rd = db.connect(creds, autocommit=True)
mj = MeshJoin(rd)
check(f"page counts from loaded tables: customer={mj.cust_pages} product={mj.prod_pages}",
      mj.cust_pages == 3 and mj.prod_pages == 2)

rnd = random.Random(99)
sample = cleaned.sample(n=2600, random_state=3).reset_index(drop=True)
txns = [Txn(f"T-{i}", r.transaction_id, r.customer_id, r.product_id, int(r.quantity),
            Decimal(r.unit_price_text), r.event_time.to_pydatetime(), r.sales_mode, "online")
        for i, r in enumerate(sample.itertuples())]
done, born, i = [], {}, 0
it = 0
while i < len(txns) or not mj.idle:
    take = rnd.choice([0, 1, 37, 500, 500, 123]) if i < len(txns) else 0       # irregular arrival
    chunk = txns[i:i + take]
    for t in chunk:
        born[t.source_key] = mj.iterations + 1
    i += take
    out = mj.iterate(chunk)
    for t in out:
        check_age = (mj.iterations - born[t.source_key] + 1)
        assert check_age == max(mj.cust_pages, mj.prod_pages), "chunk stayed wrong number of iterations"
    done.extend(out)
    it += 1
    assert it < 5000
check("every transaction completed exactly once", len(done) == len(txns) and len({t.source_key for t in done}) == len(txns))
check("each chunk stayed exactly max(pages) iterations (no premature / late removal)", True)
check("H and queue empty at the end", mj.h_size == 0 and not mj.h_cust and not mj.h_prod and mj.idle)

cmi = cm1.set_index("customer_id")
pmi = pm1.set_index("product_id")
bad = 0
for t in done:
    c, p = cmi.loc[t.customer_id], pmi.loc[t.product_id]
    if not (t.cust[1:] == (c.gender, c.age_group, c.occupation, c.city_category,
                           c.stay_in_current_city_years, c.marital_status)
            and t.prod[1:] == (p.product_name, p.product_category, p.supplier_id, p.supplier_name)):
        bad += 1
check("MESHJOIN result equals reference join for all attributes", bad == 0)
check("pages read from disk each iteration (2 reads per iteration)", mj.pages_read == 2 * mj.iterations)
print(f"\nALL {len(ok)} CHECKS PASSED  (iterations={mj.iterations}, matches={mj.matches})")
