"""MESHJOIN (Polyzotis et al.) for the Stream-Relation join of the transformation phase.

Components (names as in the specification):
  Stream buffer  - lives in scheduler.StreamBuffer (1,000 transactions)
  Hash table H   - two multi-map indexes (customer_id, product_id) over the SAME Txn objects
  Queue          - FIFO of chunk nodes; each node points to the txns of one chunk (w <= 500)
  Disk buffer    - one 500-tuple partition of each Master Data table, read from MySQL every iteration
  Master Data R  - customer_master_data and product_master_data, scanned cyclically

One iteration = the four steps of Section 4.2, executed for BOTH Master Data tables:
  1. read the next partition of customer R and of product R into the disk buffer (wrap-around)
  2. move up to w transactions (the chunk) into H and append the chunk pointer to the queue
  3. probe every Master Data tuple of the disk buffer in H; each match enriches a transaction
  4. remove the oldest chunk(s) only when it has met EVERY customer page and EVERY product page
The two Master Data tables have different page counts, so every chunk carries its own progress
counters; a chunk leaves after max(customer_pages, product_pages) iterations.
"""
from collections import deque

CUST_SQL = ("SELECT customer_id, gender, age_group, occupation, city_category, "
            "stay_in_current_city_years, marital_status FROM customer_master_data WHERE page_no = %s")
PROD_SQL = ("SELECT product_id, product_name, product_category, supplier_id, supplier_name "
            "FROM product_master_data WHERE page_no = %s")


class Txn:
    """One transaction tuple. Shared by both hash indexes (never copied)."""
    __slots__ = ("source_key", "transaction_id", "customer_id", "product_id", "quantity",
                 "unit_price", "event_time", "sales_mode", "origin", "shed_id", "cust", "prod")

    def __init__(self, source_key, transaction_id, customer_id, product_id, quantity,
                 unit_price, event_time, sales_mode, origin, shed_id=None):
        self.source_key = source_key
        self.transaction_id = transaction_id
        self.customer_id = customer_id
        self.product_id = product_id
        self.quantity = quantity
        self.unit_price = unit_price
        self.event_time = event_time
        self.sales_mode = sales_mode      # Online / Periodic
        self.origin = origin              # online / periodic / shed
        self.shed_id = shed_id
        self.cust = None                  # filled by the customer pass
        self.prod = None                  # filled by the product pass


class Chunk:
    """Queue node: pointer to the transactions of one chunk + its own scan progress."""
    __slots__ = ("txns", "cust_seen", "prod_seen")

    def __init__(self, txns):
        self.txns = txns
        self.cust_seen = 0
        self.prod_seen = 0


class MeshJoin:
    def __init__(self, conn, page_size=500):
        """conn must be an autocommit connection (reads Master Data pages only)."""
        self.conn = conn
        self.page_size = page_size
        self.cust_pages = self._count_pages("customer_master_data")
        self.prod_pages = self._count_pages("product_master_data")
        self.cust_ptr = 0
        self.prod_ptr = 0
        self.h_cust = {}          # H, customer index : customer_id -> [Txn, ...]
        self.h_prod = {}          # H, product index  : product_id  -> [Txn, ...]
        self.queue = deque()      # oldest chunk at the LEFT end (leaves first)
        self.h_size = 0
        self.disk_buffer_cust = []
        self.disk_buffer_prod = []
        self.iterations = 0
        self.pages_read = 0
        self.matches = 0
        self.completed = 0

    def _count_pages(self, table):
        cur = self.conn.cursor()
        cur.execute(f"SELECT COALESCE(MAX(page_no) + 1, 0) FROM {table}")
        n = cur.fetchone()[0]
        cur.close()
        return int(n)

    def _read_page(self, sql, page_no):
        cur = self.conn.cursor()
        cur.execute(sql, (page_no,))
        rows = cur.fetchall()                    # <= 500 tuples (last page may be shorter)
        cur.close()
        self.pages_read += 1
        return rows

    @property
    def idle(self):
        return not self.queue

    # ------------------------------------------------------------------
    def iterate(self, new_txns):
        """Run one MESHJOIN iteration. Returns the list of fully enriched transactions."""
        self.iterations += 1
        # Step 1: next partition of each Master Data table -> disk buffer (cyclic scan)
        if self.cust_pages:
            self.disk_buffer_cust = self._read_page(CUST_SQL, self.cust_ptr)
            self.cust_ptr = (self.cust_ptr + 1) % self.cust_pages
        if self.prod_pages:
            self.disk_buffer_prod = self._read_page(PROD_SQL, self.prod_ptr)
            self.prod_ptr = (self.prod_ptr + 1) % self.prod_pages

        # Step 2: new chunk into H, chunk pointer into the queue
        if new_txns:
            chunk = Chunk(new_txns)
            for t in new_txns:
                self.h_cust.setdefault(t.customer_id, []).append(t)
                self.h_prod.setdefault(t.product_id, []).append(t)
            self.queue.append(chunk)
            self.h_size += len(new_txns)

        # Step 3: probe the join attribute of every Master Data tuple in H
        hc, hp = self.h_cust, self.h_prod
        for row in self.disk_buffer_cust:                    # customer pass
            hits = hc.get(row[0])
            if hits:
                for t in hits:
                    if t.cust is None:                       # enrich exactly once
                        t.cust = row
                        self.matches += 1
        for row in self.disk_buffer_prod:                    # product pass
            hits = hp.get(row[0])
            if hits:
                for t in hits:
                    if t.prod is None:
                        t.prod = row
                        self.matches += 1
        for ch in self.queue:                                # every queued chunk met this page
            ch.cust_seen += 1
            ch.prod_seen += 1

        # Step 4: the oldest chunk leaves only after BOTH full Master Data cycles
        done = []
        q = self.queue
        while q and q[0].cust_seen >= self.cust_pages and q[0].prod_seen >= self.prod_pages:
            ch = q.popleft()
            for t in ch.txns:
                self._remove(t)
                if t.cust is None or t.prod is None:
                    raise RuntimeError(f"MESHJOIN: no Master Data match for transaction {t.source_key} "
                                       f"(customer {t.customer_id}, product {t.product_id})")
            self.h_size -= len(ch.txns)
            self.completed += len(ch.txns)
            done.extend(ch.txns)
        return done

    def _remove(self, t):
        for index, key in ((self.h_cust, t.customer_id), (self.h_prod, t.product_id)):
            lst = index[key]
            lst.remove(t)                       # identity comparison (Txn defines no __eq__)
            if not lst:
                del index[key]

    def in_flight(self):
        """All transactions currently inside H (for status / orderly shutdown)."""
        return [t for ch in self.queue for t in ch.txns]
