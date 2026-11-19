"""Shared run-time state, the bounded priority Stream buffer and the Shed_table access."""
import threading
from collections import deque

from meshjoin import Txn


class Control:
    """Mode (online|periodic|both) and speed (peak|non-peak) switches shared by all threads."""

    def __init__(self, settings):
        self.lock = threading.Lock()
        self.mode = settings.start_mode
        self.speed = settings.start_speed
        self.peak_rate = settings.peak_rate
        self.nonpeak_rate = settings.nonpeak_rate
        self.hour_seconds = settings.hour_seconds
        self.stop = threading.Event()      # stop the producer threads (OnlineThread, PeriodicThread)
        self.drain = threading.Event()     # MeshJoinThread: finish all in-memory work, then exit
        self.error = None

    def set_mode(self, mode):
        with self.lock:
            self.mode = mode

    def set_speed(self, speed):
        with self.lock:
            self.speed = speed

    def set_rate(self, which, value):
        with self.lock:
            if which == "peak":
                self.peak_rate = value
            else:
                self.nonpeak_rate = value

    def rate(self):
        with self.lock:
            return self.peak_rate if self.speed == "peak" else self.nonpeak_rate

    def online_enabled(self):
        return self.mode in ("online", "both")

    def periodic_enabled(self):
        return self.mode in ("periodic", "both")

    def shedding_allowed(self):                 # peak: periodic transactions may be shed to disk
        return self.speed == "peak"

    def replay_allowed(self):                   # non-peak: shed transactions may be processed
        return self.speed == "non-peak" and self.periodic_enabled()

    def fail(self, exc):
        self.error = exc
        self.stop.set()
        self.drain.set()


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.c = {"online_read": 0, "periodic_released": 0, "groups_released": 0,
                  "shed_written_peak": 0, "shed_written_other": 0, "shed_replayed_nonpeak": 0,
                  "facts_loaded": 0, "cached_total": 0, "iterations": 0}
        self.online_done = False
        self.periodic_done = False
        self.shed_pending = 0          # shed rows on disk not yet loaded (+ outbox)
        self.cache_pending = 0         # rows waiting in live_sales_cache
        self.h_size = 0
        self.queue_len = 0
        self.mj_idle = True

    def inc(self, name, n=1):
        with self.lock:
            self.c[name] += n

    def set(self, name, value):
        with self.lock:
            self.c[name] = value


class ShedOutbox:
    """Periodic transactions waiting to be written to the Shed_table by the MeshJoinThread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = []

    def add(self, txns, speed):
        with self.lock:
            self.items.extend((t, speed) for t in txns)

    def take_all(self):
        with self.lock:
            items, self.items = self.items, []
        return items

    def __len__(self):
        with self.lock:
            return len(self.items)


class StreamBuffer:
    """Bounded (1,000) buffer between the producers and MESHJOIN.

    Two internal FIFOs give priority: online first, periodic second.  Back-pressure: a full buffer
    blocks the producer (nothing is dropped).  Only in peak mode may periodic transactions be shed:
    an arriving online transaction then displaces the newest buffered periodic one to the outbox."""

    def __init__(self, limit, control, outbox):
        self.limit = limit
        self.control = control
        self.outbox = outbox
        self.online = deque()
        self.periodic = deque()
        self.cond = threading.Condition()

    def __len__(self):
        with self.cond:
            return len(self.online) + len(self.periodic)

    def counts(self):
        with self.cond:
            return len(self.online), len(self.periodic)

    def put_online(self, txn):
        with self.cond:
            while len(self.online) + len(self.periodic) >= self.limit:
                if self.control.stop.is_set():
                    return False
                if self.control.shedding_allowed() and self.periodic:
                    self.outbox.add([self.periodic.pop()], "peak")      # make room for Live data
                    break
                self.cond.wait(0.1)
            self.online.append(txn)
            self.cond.notify_all()
            return True

    def try_put_periodic(self, txn):
        with self.cond:
            if len(self.online) + len(self.periodic) >= self.limit:
                return False
            self.periodic.append(txn)
            self.cond.notify_all()
            return True

    def wait_room(self, timeout):
        with self.cond:
            if len(self.online) + len(self.periodic) >= self.limit:
                self.cond.wait(timeout)

    def take_chunk(self, w):
        """Up to w transactions: all waiting online ones first, then periodic ones."""
        with self.cond:
            out = []
            while self.online and len(out) < w:
                out.append(self.online.popleft())
            while self.periodic and len(out) < w:
                out.append(self.periodic.popleft())
            if out:
                self.cond.notify_all()
            return out

    def wait_for_items(self, timeout):
        with self.cond:
            if not self.online and not self.periodic:
                self.cond.wait(timeout)

    def wake(self):
        with self.cond:
            self.cond.notify_all()


class ShedStore:
    """Shed_table access. Used only by the MeshJoinThread (its own connection)."""

    def __init__(self, conn):
        self.conn = conn
        self.cursor_id = 0                     # highest shed_id already handed to MESHJOIN

    def write(self, items):
        if not items:
            return {}
        cur = self.conn.cursor()
        rows = [(t.source_key, t.transaction_id, t.customer_id, t.product_id, t.quantity, t.unit_price,
                 t.event_time, t.sales_mode, speed) for t, speed in items]
        try:
            cur.executemany(
                "INSERT INTO shed_table (source_key, transaction_id, customer_id, product_id, quantity, "
                "unit_price, event_time, sales_mode, flag, shed_time, shed_speed) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'Periodic',NOW(3),%s)", rows)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()
        counts = {}
        for _, speed in items:
            counts[speed] = counts.get(speed, 0) + 1
        return counts

    def fetch_for_replay(self, limit):
        cur = self.conn.cursor()
        cur.execute("SELECT shed_id, source_key, transaction_id, customer_id, product_id, quantity, unit_price, "
                    "event_time, sales_mode FROM shed_table WHERE replayed = 0 AND shed_id > %s "
                    "ORDER BY shed_id LIMIT %s", (self.cursor_id, limit))
        rows = cur.fetchall()
        cur.close()
        self.conn.commit()
        txns = []
        for sid, skey, tid, cid, pid, q, price, et, mode in rows:
            txns.append(Txn(skey, tid, cid, pid, q, price, et, mode, "shed", shed_id=sid))
            self.cursor_id = max(self.cursor_id, sid)
        return txns

    def pending_count(self):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM shed_table WHERE replayed = 0")
        n = cur.fetchone()[0]
        cur.close()
        self.conn.commit()
        return n
