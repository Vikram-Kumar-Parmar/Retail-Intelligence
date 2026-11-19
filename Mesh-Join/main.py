"""Near-real-time + periodic Retail Data Warehouse: preparation, MESHJOIN ETL and command console.

Steps (Section 9): 1 create working tables + DW (drops old copies)  2 clean / prepare / load Master Data
and staging  3 start the three threads  4 load the DW  5 accept commands until quit.
"""
import argparse
import os
import sys
import time

import database as db
from config import (CHUNK_W, CREATE_DW_SQL, PAGE_SIZE, STREAM_BUFFER_LIMIT, Settings)
from data_cleaning import prepare_data
from scheduler import Control, ShedOutbox, Stats, StreamBuffer
from threads import CommandThread, MeshJoinThread, OnlineThread, PeriodicThread


def parse_args():
    ap = argparse.ArgumentParser(description="Retail DW: MESHJOIN near-real-time ETL")
    ap.add_argument("--raw-file", help="path of the UCI 'Online Retail.xlsx' (asked at start-up if omitted)")
    ap.add_argument("--online-file", help="path of online_sales_stream.csv (asked at start-up if omitted)")
    ap.add_argument("--peak-rate", type=float, default=80.0, help="online transactions/second in peak mode")
    ap.add_argument("--nonpeak-rate", type=float, default=10.0, help="online transactions/second in non-peak mode")
    ap.add_argument("--hour-seconds", type=float, default=60.0, help="real seconds per simulated hour")
    ap.add_argument("--speed", choices=["peak", "non-peak"], default="peak", help="initial arrival mode")
    ap.add_argument("--mode", choices=["online", "periodic", "both"], default="both", help="initial mode")
    ap.add_argument("--no-cache", action="store_true", help="disable the bonus: Live results go straight to fact_sales")
    ap.add_argument("--resume", action="store_true", help="skip preparation and continue the previous run")
    ap.add_argument("--exit-when-done", action="store_true", help="shut down automatically when everything is loaded")
    ap.add_argument("--download-date", default="", help="date the UCI file was downloaded (goes into the data note)")
    return ap.parse_args()


def ask_path(label, default):
    v = input(f"{label} [{default}]: ").strip()
    return v or default


def main():
    args = parse_args()
    st = Settings(peak_rate=args.peak_rate, nonpeak_rate=args.nonpeak_rate, hour_seconds=args.hour_seconds,
                  start_speed=args.speed, start_mode=args.mode, bonus_cache=not args.no_cache,
                  exit_when_done=args.exit_when_done, resume=args.resume, download_date=args.download_date)
    creds = db.prompt_credentials()
    st.online_file = args.online_file or os.environ.get("DWH_ONLINE_FILE") or ask_path("Path of the online file", st.online_file)
    if not st.resume:
        st.raw_file = args.raw_file or os.environ.get("DWH_RAW_FILE") or ask_path("Path of the UCI dataset", st.raw_file)

    conn = db.connect(creds)
    skip = 0
    if not st.resume:
        print("[step 1] creating working tables and the DW (old copies are dropped) ...")
        db.run_script(conn, CREATE_DW_SQL)
        print("[step 2] cleaning, preparing and loading Master Data / staging ...")
        cust, prod, per, _ = prepare_data(st.raw_file, st.online_file, st.download_date)
        db.load_master_tables(conn, cust, prod)
        db.load_staging(conn, per)
    else:
        skip = (db.scalar(conn, "SELECT COUNT(*) FROM fact_sales WHERE source_key LIKE 'O-%'") or 0) + \
               (db.scalar(conn, "SELECT COUNT(*) FROM live_sales_cache WHERE source_key LIKE 'O-%'") or 0)
        print(f"[resume] online lines already loaded or cached: {skip:,} (they will be skipped)")
    conn.close()

    control, stats, outbox = Control(st), Stats(), ShedOutbox()
    buffer = StreamBuffer(STREAM_BUFFER_LIMIT, control, outbox)
    mj = MeshJoinThread(creds, buffer, outbox, control, stats, st)
    online = OnlineThread(st.online_file, buffer, control, stats, skip=skip)
    periodic = PeriodicThread(creds, buffer, outbox, control, stats)
    mj.start()
    mj.ready.wait(30)
    if control.error:
        sys.exit(f"MeshJoinThread failed to start: {control.error}")

    print("=" * 72)
    print(f"Stream arrival mode : {control.speed}   (peak = {st.peak_rate:g} tx/s, non-peak = {st.nonpeak_rate:g} tx/s)")
    print(f"One simulated hour  : {st.hour_seconds:g} seconds of real time")
    print(f"Stream buffer limit : {STREAM_BUFFER_LIMIT} transactions")
    print(f"Master Data page    : {PAGE_SIZE} tuples | stream chunk w = {CHUNK_W} transactions")
    print(f"Master Data pages   : customer = {mj.engine.cust_pages}, product = {mj.engine.prod_pages}")
    print(f"Initial mode        : {control.mode} | Live cache (bonus): {'ON' if st.bonus_cache else 'OFF'}")
    print("=" * 72)

    online.start()
    periodic.start()
    cmd = CommandThread(control, stats, buffer, mj, st)
    cmd.start()

    quiet = 0
    try:
        while not control.stop.is_set():
            time.sleep(0.2)
            if st.exit_when_done and stats.online_done and stats.periodic_done:
                if control.speed == "peak":
                    control.set_speed("non-peak")
                    print("[auto] all sources finished -> switching to non-peak so shed/cached data can drain")
                if control.mode != "both":
                    control.set_mode("both")
                idle = (len(buffer) == 0 and mj.engine.idle and len(outbox) == 0
                        and stats.shed_pending == 0 and stats.cache_pending == 0)
                quiet = quiet + 1 if idle else 0
                if quiet >= 5:
                    print("[auto] everything is loaded -> shutting down")
                    break
    except KeyboardInterrupt:
        print("\ninterrupted -> shutting down")

    control.stop.set()
    for t in (online, periodic):
        t.join(timeout=15)
    control.drain.set()
    buffer.wake()
    mj.join(timeout=300)

    final = db.connect(creds)
    try:
        facts = db.scalar(final, "SELECT COUNT(*) FROM fact_sales")
        cache = db.scalar(final, "SELECT COUNT(*) FROM live_sales_cache")
        shed = db.scalar(final, "SELECT COUNT(*) FROM shed_table WHERE replayed = 0")
        print(f"[summary] fact_sales rows: {facts:,} | cache rows left: {cache:,} | shed rows not loaded: {shed:,}")
        if cache:
            print("  note: cached Live results remain in live_sales_cache (they are copied in a slow period "
                  "- run again with --resume, switch to non-peak and wait until status shows 0).")
        if shed:
            print("  note: shed periodic transactions remain on disk and will be replayed in non-peak mode.")
    finally:
        final.close()
    if control.error:
        sys.exit(f"stopped because of an error: {control.error}")


if __name__ == "__main__":
    main()
