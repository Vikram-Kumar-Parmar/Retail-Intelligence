"""Data consolidation (SALE = quantity x price) and loading into the star schema.

processing_mode values (the PDF does not define them; documented interpretation):
  Live         - online transaction joined straight from the stream
  Hourly       - periodic transaction extracted by the hourly PeriodicThread and joined at once
  Shed-Replay  - periodic transaction that was shed to disk under load and processed in a slow period
"""
import time

from config import STORE_BY_ID, STORES
from master_data import store_id_for

PROCESSING_MODE = {"online": "Live", "periodic": "Hourly", "shed": "Shed-Replay"}

SEASON = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
          6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}


def time_parts(dt):
    wd = dt.weekday()
    return (dt, dt.date(), dt.hour, dt.day, dt.month, (dt.month - 1) // 3 + 1, dt.year,
            dt.strftime("%A"), "Weekend" if wd >= 5 else "Weekday", SEASON[dt.month],
            "H1" if dt.month <= 6 else "H2")


def consolidate(t):
    """Fully joined Txn -> flat record. Store is added here (Store_list is in memory, outside MESHJOIN)."""
    sid = store_id_for(t.transaction_id, t.sales_mode)
    c, p = t.cust, t.prod
    return {
        "source_key": t.source_key, "transaction_id": t.transaction_id,
        "customer_id": c[0], "gender": c[1], "age_group": c[2], "occupation": c[3],
        "city_category": c[4], "stay_years": c[5], "marital_status": c[6],
        "product_id": p[0], "product_name": p[1], "product_category": p[2],
        "supplier_id": p[3], "supplier_name": p[4],
        "store_id": sid, "event_time": t.event_time, "quantity": t.quantity,
        "unit_price": t.unit_price, "revenue": t.quantity * t.unit_price,
        "sales_mode": t.sales_mode, "processing_mode": PROCESSING_MODE[t.origin], "shed_id": t.shed_id,
    }


FACT_SQL = ("INSERT INTO fact_sales (source_key, transaction_id, customer_key, product_key, supplier_key, "
            "store_key, time_key, quantity, unit_price, revenue, sales_mode, processing_mode) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
CACHE_SQL = ("INSERT INTO live_sales_cache (source_key, transaction_id, customer_id, gender, age_group, "
             "occupation, city_category, stay_in_current_city_years, marital_status, product_id, product_name, "
             "product_category, supplier_id, supplier_name, store_id, event_time, quantity, unit_price, revenue, "
             "sales_mode, processing_mode, cached_time) VALUES "
             "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(3))")


class DWLoader:
    """All methods must be called from ONE thread (it owns its connection)."""

    def __init__(self, conn):
        self.conn = conn
        self.keys = {"customer": {}, "product": {}, "supplier": {}, "store": {}, "time": {}}

    # ---------------- dimensions ---------------------------------------
    def _ensure(self, cur, table, kind, id_col, key_col, wanted, insert_sql, new):
        """Insert dimension rows that are missing, then fetch surrogate keys (committed later)."""
        missing = [i for i in wanted if i not in self.keys[kind]]
        if not missing:
            return
        cur.executemany(insert_sql, [wanted[i] for i in missing])            # INSERT IGNORE
        for s in range(0, len(missing), 500):
            part = missing[s:s + 500]
            cur.execute(f"SELECT {id_col}, {key_col} FROM {table} WHERE {id_col} IN "
                        f"({','.join(['%s'] * len(part))})", part)
            for natural, key in cur.fetchall():
                new[kind][natural] = key

    def _ensure_dims(self, cur, recs):
        new = {k: {} for k in self.keys}
        cust, prod, sup, store, tm = {}, {}, {}, {}, {}
        for r in recs:
            cust[r["customer_id"]] = (r["customer_id"], r["gender"], r["age_group"], r["occupation"],
                                      r["city_category"], r["stay_years"], r["marital_status"])
            prod[r["product_id"]] = (r["product_id"], r["product_name"], r["product_category"], r["supplier_id"])
            sup[r["supplier_id"]] = (r["supplier_id"], r["supplier_name"])
            name, city = STORE_BY_ID[r["store_id"]]
            store[r["store_id"]] = (r["store_id"], name, city)
            tm[r["event_time"]] = time_parts(r["event_time"])
        self._ensure(cur, "dim_customer", "customer", "customer_id", "customer_key", cust,
                     "INSERT IGNORE INTO dim_customer (customer_id, gender, age_group, occupation, city_category, "
                     "stay_in_current_city_years, marital_status) VALUES (%s,%s,%s,%s,%s,%s,%s)", new)
        self._ensure(cur, "dim_product", "product", "product_id", "product_key", prod,
                     "INSERT IGNORE INTO dim_product (product_id, product_name, product_category, supplier_id) "
                     "VALUES (%s,%s,%s,%s)", new)
        self._ensure(cur, "dim_supplier", "supplier", "supplier_id", "supplier_key", sup,
                     "INSERT IGNORE INTO dim_supplier (supplier_id, supplier_name) VALUES (%s,%s)", new)
        self._ensure(cur, "dim_store", "store", "store_id", "store_key", store,
                     "INSERT IGNORE INTO dim_store (store_id, store_name, city) VALUES (%s,%s,%s)", new)
        self._ensure(cur, "dim_time", "time", "event_time", "time_key", tm,
                     "INSERT IGNORE INTO dim_time (event_time, date_value, hour_of_day, day_of_month, month_num, "
                     "quarter_num, year_num, day_of_week, day_type, season, half_year) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", new)
        return new

    # ---------------- facts ----------------------------------------------
    def load_records(self, recs, cache_keys=None):
        """One DB transaction: missing dimension rows -> fact rows -> mark shed rows loaded ->
        delete copied cache rows.  All-or-nothing; caches of keys are updated only after COMMIT."""
        if not recs:
            return 0
        cur = self.conn.cursor()
        try:
            new = self._ensure_dims(cur, recs)
            k = self.keys
            rows = []
            for r in recs:
                rows.append((
                    r["source_key"], r["transaction_id"],
                    new["customer"].get(r["customer_id"]) or k["customer"][r["customer_id"]],
                    new["product"].get(r["product_id"]) or k["product"][r["product_id"]],
                    new["supplier"].get(r["supplier_id"]) or k["supplier"][r["supplier_id"]],
                    new["store"].get(r["store_id"]) or k["store"][r["store_id"]],
                    new["time"].get(r["event_time"]) or k["time"][r["event_time"]],
                    r["quantity"], r["unit_price"], r["revenue"], r["sales_mode"], r["processing_mode"]))
            cur.executemany(FACT_SQL, rows)
            shed_ids = [r["shed_id"] for r in recs if r["shed_id"]]
            for s in range(0, len(shed_ids), 500):
                part = shed_ids[s:s + 500]
                cur.execute("UPDATE shed_table SET replayed = 1, replayed_time = NOW(3) WHERE shed_id IN "
                            f"({','.join(['%s'] * len(part))})", part)
            if cache_keys:
                for s in range(0, len(cache_keys), 500):
                    part = cache_keys[s:s + 500]
                    cur.execute("DELETE FROM live_sales_cache WHERE source_key IN "
                                f"({','.join(['%s'] * len(part))})", part)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()
        for kind, d in new.items():
            self.keys[kind].update(d)
        return len(recs)

    # ---------------- bonus: live sales cache -----------------------------
    def cache_records(self, recs):
        if not recs:
            return 0
        cur = self.conn.cursor()
        try:
            cur.executemany(CACHE_SQL, [(
                r["source_key"], r["transaction_id"], r["customer_id"], r["gender"], r["age_group"],
                r["occupation"], r["city_category"], r["stay_years"], r["marital_status"], r["product_id"],
                r["product_name"], r["product_category"], r["supplier_id"], r["supplier_name"], r["store_id"],
                r["event_time"], r["quantity"], r["unit_price"], r["revenue"], r["sales_mode"],
                r["processing_mode"]) for r in recs])
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()
        return len(recs)

    def drain_cache(self, limit=1000):
        """Copy cached Live results into the DW and delete exactly the rows copied (one transaction)."""
        cur = self.conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM live_sales_cache ORDER BY cache_seq LIMIT %s", (limit,))
        rows = cur.fetchall()
        cur.close()
        self.conn.commit()                       # end the read snapshot
        if not rows:
            return 0
        recs = [{
            "source_key": c["source_key"], "transaction_id": c["transaction_id"],
            "customer_id": c["customer_id"], "gender": c["gender"], "age_group": c["age_group"],
            "occupation": c["occupation"], "city_category": c["city_category"],
            "stay_years": c["stay_in_current_city_years"], "marital_status": c["marital_status"],
            "product_id": c["product_id"], "product_name": c["product_name"],
            "product_category": c["product_category"], "supplier_id": c["supplier_id"],
            "supplier_name": c["supplier_name"], "store_id": c["store_id"], "event_time": c["event_time"],
            "quantity": c["quantity"], "unit_price": c["unit_price"], "revenue": c["revenue"],
            "sales_mode": c["sales_mode"], "processing_mode": c["processing_mode"], "shed_id": None,
        } for c in rows]
        return self.load_records(recs, cache_keys=[c["source_key"] for c in rows])


class ResultRouter:
    """Collects completed transactions and writes them in batches (near-real-time, low latency).

    Online results go to live_sales_cache when the bonus is on, otherwise straight into fact_sales.
    Periodic results (hourly or shed-replayed) always go straight into fact_sales."""

    def __init__(self, loader, use_cache, flush_rows=500, flush_age=0.25):
        self.loader = loader
        self.use_cache = use_cache
        self.flush_rows = flush_rows
        self.flush_age = flush_age
        self.facts, self.cache = [], []
        self.first_ts = None
        self.loaded_facts = 0
        self.cached_total = 0

    def add(self, done_txns):
        for t in done_txns:
            r = consolidate(t)
            (self.cache if (self.use_cache and t.sales_mode == "Online") else self.facts).append(r)
        if done_txns and self.first_ts is None:
            self.first_ts = time.monotonic()

    def pending(self):
        return len(self.facts) + len(self.cache)

    def maybe_flush(self, force=False):
        if not self.pending():
            return
        if force or self.pending() >= self.flush_rows or time.monotonic() - self.first_ts >= self.flush_age:
            self.flush()

    def flush(self):
        # Retry a few times on a transient failure; the batch stays in memory until it succeeded.
        for attempt in range(3):
            try:
                if self.facts:
                    self.loaded_facts += self.loader.load_records(self.facts)
                    self.facts = []
                if self.cache:
                    self.cached_total += self.loader.cache_records(self.cache)
                    self.cache = []
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.5)
        self.first_ts = None
