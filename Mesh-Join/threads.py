"""The three independent worker threads (+ the command console).

OnlineThread    : reads online_sales_stream.csv and feeds the Stream buffer at the configured rate.
PeriodicThread  : once per simulated hour extracts the next group from periodic_sales_data.
MeshJoinThread  : MESHJOIN iterations, priority, load shedding, loading into the DW.
Each thread that touches MySQL owns its own connection (connections are not shared)."""
import csv
import threading
import time
import traceback
from datetime import datetime
from decimal import Decimal

import database as db
from config import CHUNK_W, PAGE_SIZE
from dw_loader import DWLoader, ResultRouter
from meshjoin import MeshJoin, Txn
from scheduler import ShedStore


class OnlineThread(threading.Thread):
    def __init__(self, path, buffer, control, stats, skip=0):
        super().__init__(name="OnlineThread", daemon=True)
        self.path, self.buffer, self.control, self.stats, self.skip = path, buffer, control, stats, skip

    def run(self):
        try:
            ctl = self.control
            with open(self.path, newline="", encoding="utf-8") as f:
                next_t = time.monotonic()
                for n, row in enumerate(csv.DictReader(f), start=1):
                    if n <= self.skip:                               # resume after an interrupted run
                        continue
                    while not ctl.online_enabled():                  # 'periodic' mode pauses the reader
                        if ctl.stop.is_set():
                            return
                        ctl.stop.wait(0.1)
                        next_t = time.monotonic()
                    if ctl.stop.is_set():
                        return
                    txn = Txn(f"O-{n}", row["transaction_id"], row["customer_id"], row["product_id"],
                              int(row["quantity"]), Decimal(row["unit_price"]),
                              datetime.fromisoformat(row["event_time"]), row["sales_mode"], "online")
                    if not self.buffer.put_online(txn):              # blocks while the buffer is full
                        return
                    self.stats.inc("online_read")
                    next_t += 1.0 / ctl.rate()                       # pacing: peak / non-peak tx per second
                    delay = next_t - time.monotonic()
                    if delay > 0.002:
                        ctl.stop.wait(delay)
                    elif delay < -1.0:
                        next_t = time.monotonic()
            self.stats.online_done = True
        except Exception as exc:                                     # noqa: BLE001
            traceback.print_exc()
            self.control.fail(exc)


class PeriodicThread(threading.Thread):
    def __init__(self, creds, buffer, outbox, control, stats):
        super().__init__(name="PeriodicThread", daemon=True)
        self.creds, self.buffer, self.outbox, self.control, self.stats = creds, buffer, outbox, control, stats

    def _extract_next_group(self, conn):
        """Return (group_no, [Txn]) for the lowest unreleased group and flag it released (never twice)."""
        cur = conn.cursor()
        cur.execute("SELECT MIN(release_hour) FROM periodic_sales_data WHERE released = 0")
        g = cur.fetchone()[0]
        if g is None:
            conn.commit()
            return None, []
        cur.execute("SELECT staging_id, transaction_id, customer_id, product_id, quantity, unit_price, "
                    "event_time, sales_mode FROM periodic_sales_data WHERE release_hour = %s AND released = 0 "
                    "ORDER BY event_time, staging_id FOR UPDATE", (g,))
        rows = cur.fetchall()
        cur.execute("UPDATE periodic_sales_data SET released = 1, released_time = NOW(3) "
                    "WHERE release_hour = %s AND released = 0", (g,))
        conn.commit()
        cur.close()
        return g, [Txn(f"P-{r[0]}", r[1], r[2], r[3], r[4], r[5], r[6], r[7], "periodic") for r in rows]

    def _push(self, txns):
        """Put a group into the Stream buffer; in peak mode what does not fit is shed to disk."""
        ctl = self.control
        for i, t in enumerate(txns):
            placed = False
            while not ctl.stop.is_set():
                if self.buffer.try_put_periodic(t):
                    placed = True
                    break
                if ctl.shedding_allowed():
                    self.outbox.add([t], "peak")
                    placed = True
                    break
                self.buffer.wait_room(0.1)                           # non-peak: wait (back-pressure)
            if not placed:                                           # shutting down: keep the rest safe
                self.outbox.add(txns[i:], "shutdown")
                return

    def run(self):
        conn = None
        try:
            conn = db.connect(self.creds)
            ctl = self.control
            next_due = time.monotonic()                              # group 0 may go out immediately
            while not ctl.stop.is_set():
                if ctl.periodic_enabled() and time.monotonic() >= next_due:
                    g, txns = self._extract_next_group(conn)
                    if g is None:
                        self.stats.periodic_done = True
                        return
                    self.stats.inc("groups_released")
                    self.stats.inc("periodic_released", len(txns))
                    print(f"[PeriodicThread] hour {g + 1}/48: released group {g} ({len(txns)} transactions)")
                    next_due = time.monotonic() + ctl.hour_seconds
                    self._push(txns)
                else:
                    ctl.stop.wait(0.05)
        except Exception as exc:                                     # noqa: BLE001
            traceback.print_exc()
            self.control.fail(exc)
        finally:
            if conn:
                conn.close()


class MeshJoinThread(threading.Thread):
    def __init__(self, creds, buffer, outbox, control, stats, settings):
        super().__init__(name="MeshJoinThread", daemon=True)
        self.creds, self.buffer, self.outbox = creds, buffer, outbox
        self.control, self.stats, self.settings = control, stats, settings
        self.engine = None
        self.ready = threading.Event()
        self._last_refresh = 0.0
        self.drained = 0                 # cache rows copied into fact_sales

    # --- load shedding module ---------------------------------------------
    def _flush_outbox(self, shed):
        items = self.outbox.take_all()
        if items:
            counts = shed.write(items)
            for speed, n in counts.items():
                self.stats.inc("shed_written_peak" if speed == "peak" else "shed_written_other", n)

    def _refresh_stats(self, shed, force=False):
        now = time.monotonic()
        if not force and now - self._last_refresh < 0.5:
            return
        self._last_refresh = now
        self.stats.shed_pending = shed.pending_count() + len(self.outbox)
        cur = self.loader.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM live_sales_cache")
        self.stats.cache_pending = cur.fetchone()[0]
        cur.close()
        self.loader.conn.commit()
        self.stats.h_size = self.engine.h_size
        self.stats.queue_len = len(self.engine.queue)

    def run(self):
        rd = wr = None
        try:
            rd = db.connect(self.creds, autocommit=True)             # Master Data page reads
            wr = db.connect(self.creds)                              # shed table + DW loading
            self.engine = MeshJoin(rd, PAGE_SIZE)
            self.loader = DWLoader(wr)
            router = ResultRouter(self.loader, self.settings.bonus_cache)
            shed = ShedStore(wr)
            self._refresh_stats(shed, force=True)
            self.ready.set()
            ctl, buf, eng = self.control, self.buffer, self.engine
            while True:
                draining = ctl.drain.is_set()
                self._flush_outbox(shed)
                chunk = buf.take_chunk(CHUNK_W)                      # 1) online, 2) periodic
                if len(chunk) < CHUNK_W and ctl.replay_allowed() and not draining:
                    replay = shed.fetch_for_replay(CHUNK_W - len(chunk))   # 3) previously shed
                    if replay:
                        self.stats.inc("shed_replayed_nonpeak", len(replay))
                    chunk += replay
                if not chunk and eng.idle:
                    router.maybe_flush(force=True)
                    if ctl.speed == "non-peak" and self.settings.bonus_cache and self.stats.cache_pending:
                        self.drained += self.loader.drain_cache(1000)   # slow period: cache -> fact_sales
                        self.stats.set("facts_loaded", router.loaded_facts + self.drained)
                        self._refresh_stats(shed, force=True)
                        continue
                    if draining and len(buf) == 0 and not len(self.outbox):
                        break
                    self._refresh_stats(shed)
                    buf.wait_for_items(0.05)
                    continue
                done = eng.iterate(chunk)
                self.stats.inc("iterations")
                if done:
                    router.add(done)
                router.maybe_flush()
                self.stats.set("facts_loaded", router.loaded_facts + self.drained)
                self.stats.set("cached_total", router.cached_total)
                self._refresh_stats(shed)
            router.maybe_flush(force=True)
            self._refresh_stats(shed, force=True)
        except Exception as exc:                                     # noqa: BLE001
            traceback.print_exc()
            self.control.fail(exc)
        finally:
            self.ready.set()
            for c in (rd, wr):
                if c:
                    c.close()


class CommandThread(threading.Thread):
    HELP = ("commands: online | periodic | both | peak | non-peak | status | quit   "
            "(extra: set-peak N, set-nonpeak N, help)")

    def __init__(self, control, stats, buffer, mj_thread, settings):
        super().__init__(name="CommandThread", daemon=True)
        self.control, self.stats, self.buffer, self.mj, self.settings = control, stats, buffer, mj_thread, settings

    def status(self):
        c, s, b = self.control, self.stats, self.buffer
        n_on, n_per = b.counts()
        e = self.mj.engine
        print(f"  Mode            : {c.mode}")
        print(f"  Speed           : {c.speed}  ({c.rate():g} tx/s now; peak={c.peak_rate:g}, non-peak={c.nonpeak_rate:g})")
        print(f"  Stream buffer   : {n_on + n_per}/{b.limit}  (online {n_on}, periodic {n_per})")
        print(f"  Hash table H    : {s.h_size} transactions in {s.queue_len} queued chunks"
              f"  [customer pages {e.cust_pages}, product pages {e.prod_pages}]")
        print(f"  Shed disk       : {s.shed_pending} periodic transactions still on the shed disk")
        print(f"  Live cache      : {s.cache_pending} cached online results waiting"
              f"  (bonus {'ON' if self.settings.bonus_cache else 'OFF'})")
        with s.lock:
            k = dict(s.c)
        print(f"  Progress        : online read {k['online_read']:,} | periodic groups {k['groups_released']}/48 "
              f"| facts loaded {k['facts_loaded']:,} | cached {k['cached_total']:,} "
              f"| shed(peak) {k['shed_written_peak']:,} | replayed {k['shed_replayed_nonpeak']:,}")

    def run(self):
        print(self.HELP)
        while not self.control.stop.is_set():
            try:
                line = input("dw> ").strip().lower()
            except EOFError:
                return
            if not line:
                continue
            cmd, *arg = line.split()
            if cmd in ("online", "periodic", "both"):
                self.control.set_mode(cmd)
                print(f"mode -> {cmd}")
            elif cmd in ("peak", "non-peak"):
                self.control.set_speed(cmd)
                print(f"speed -> {cmd} ({self.control.rate():g} tx/s)")
            elif cmd in ("set-peak", "set-nonpeak") and arg:
                try:
                    self.control.set_rate("peak" if cmd == "set-peak" else "non-peak", float(arg[0]))
                    print(f"{cmd} rate -> {float(arg[0]):g} tx/s")
                except ValueError:
                    print("usage: set-peak N | set-nonpeak N")
            elif cmd == "status":
                self.status()
            elif cmd == "quit":
                print("stopping threads ...")
                self.control.stop.set()
                return
            else:
                print(self.HELP)
