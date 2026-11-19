"""SYNTHETIC TEST DATA ONLY.

Generates a small file in the exact column layout of UCI 'Online Retail' (with deliberately dirty
rows: cancellations, zero/negative quantity, zero price, missing CustomerID, duplicate lines, mixed-case
stock codes, missing descriptions).  It exists purely so the pipeline can be tested where the real UCI
file is unavailable.  Results from this file must never be put in the report.

usage: python make_synthetic_data.py OUT.csv [n_invoices] [seed]
"""
import random
import sys
from datetime import datetime, timedelta

import pandas as pd

out = sys.argv[1]
n_inv = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
rnd = random.Random(seed)                       # seeded: test data is reproducible

customers = [10000 + i * 3 for i in range(1300)]            # 1300 -> 3 customer pages
products = [str(20000 + i) for i in range(880)] + ["85123A", "85123a", "POST", "M"]   # ~2 product pages
descs = {p: ("SYNTH ITEM " + p + " " + "X" * rnd.randint(0, 12)) for p in products}
for p in rnd.sample(products, 40):
    descs[p] = None                                          # missing description
countries = ["United Kingdom", "France", "Germany", "EIRE", "Spain"]
cust_country = {c: rnd.choice(countries) for c in customers}

rows = []
t0 = datetime(2010, 12, 1, 8, 26)
for i in range(n_inv):
    inv = str(536365 + i)
    when = t0 + timedelta(minutes=int(i * (525600 + 9 * 1440) / n_inv))
    cust = rnd.choice(customers) if rnd.random() > 0.08 else None
    cancel = rnd.random() < 0.05
    inv_no = ("C" + inv) if cancel else inv
    for _ in range(rnd.randint(1, 12)):
        p = rnd.choice(products)
        qty = rnd.randint(1, 24) * (-1 if cancel else 1)
        price = round(rnd.choice([0.42, 0.85, 1.25, 2.55, 3.75, 4.95, 7.95, 12.5]), 2)
        d = descs[p]
        if rnd.random() < 0.03:
            qty = rnd.choice([0, -3])                        # bad quantity
        if rnd.random() < 0.02:
            price = 0.0                                      # bad price
        rows.append([inv_no, p, d, qty, when, price, cust,
                     cust_country[cust] if cust else rnd.choice(countries)])
        if rnd.random() < 0.05:                              # duplicate line, same product in same cart
            rows.append(list(rows[-1]))

df = pd.DataFrame(rows, columns=["InvoiceNo", "StockCode", "Description", "Quantity",
                                 "InvoiceDate", "UnitPrice", "CustomerID", "Country"])
df.to_csv(out, index=False)
print(f"SYNTHETIC file written: {out}  rows={len(df)}")
