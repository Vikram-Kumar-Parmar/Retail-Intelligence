import os, sys
from collections import Counter
from decimal import Decimal
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import database as db
from data_cleaning import clean, load_raw

conn = db.connect({"host": "localhost", "port": 3306, "database": os.environ["DWH_DB"],
                     "user": os.environ["DWH_USER"], "password": os.environ["DWH_PASSWORD"]}, autocommit=True)
cur = conn.cursor()
c, _ = clean(load_raw(sys.argv[1]))
g = c.groupby("transaction_id")
print("invoices with more than one event_time:", int((g["event_time"].nunique() > 1).sum()))
print("invoices with more than one customer_id:", int((g["customer_id"].nunique() > 1).sum()))
exp = Counter(zip(c["transaction_id"].tolist(), c["product_id"].tolist(), c["quantity"].tolist(),
                    [Decimal(p) for p in c["unit_price_text"]], c["customer_id"].tolist(),
                    [t.to_pydatetime() for t in c["event_time"]]))
cur.execute("SELECT f.transaction_id, p.product_id, f.quantity, f.unit_price, cu.customer_id, t.event_time "
              "FROM fact_sales f JOIN dim_product p ON p.product_key=f.product_key "
              "JOIN dim_customer cu ON cu.customer_key=f.customer_key JOIN dim_time t ON t.time_key=f.time_key")
got = Counter((a, b, int(q), Decimal(pr), cid, et) for a, b, q, pr, cid, et in cur.fetchall())
print("ROW-LEVEL MATCH:", got == exp, "| rows compared:", sum(got.values()))