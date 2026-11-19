"""Read the UCI file, clean it with code (never by hand), classify and prepare the data."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA_DIR, N_GROUPS
from master_data import build_customer_master, build_product_master, sales_mode_for

RAW_TO_PROJECT = {
    "InvoiceNo": "transaction_id", "StockCode": "product_id", "Description": "product_name",
    "Quantity": "quantity", "InvoiceDate": "event_time", "UnitPrice": "unit_price",
    "CustomerID": "customer_id", "Country": "country",
}
ONLINE_COLUMNS = ["transaction_id", "customer_id", "product_id", "quantity",
                  "unit_price", "event_time", "sales_mode"]


def load_raw(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Dataset not found: {p}  (download 'Online Retail.xlsx' from UCI first)")
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p, dtype={"InvoiceNo": str, "StockCode": str}, engine="openpyxl")
    else:
        df = pd.read_csv(p, dtype={"InvoiceNo": str, "StockCode": str})
    missing = set(RAW_TO_PROJECT) - set(df.columns)
    if missing:
        raise ValueError(f"Dataset is missing columns: {sorted(missing)}")
    return df.rename(columns=RAW_TO_PROJECT)[list(RAW_TO_PROJECT.values())]


def clean(df: pd.DataFrame):
    """Keep quantity>0, unit_price>0, customer present; drop cancellations (invoice starts with C)."""
    before = len(df)
    df = df.copy()
    df["transaction_id"] = df["transaction_id"].astype(str).str.strip()
    df["product_id"] = df["product_id"].astype(str).str.strip()
    df["country"] = df["country"].astype(str).str.strip()
    qty = pd.to_numeric(df["quantity"], errors="coerce")
    price = pd.to_numeric(df["unit_price"], errors="coerce")
    has_cust = df["customer_id"].notna()
    is_cancel = df["transaction_id"].str.upper().str.startswith("C")
    keep = (qty > 0) & (price > 0) & has_cust & ~is_cancel
    stats = {
        "records_before_cleaning": int(before),
        "removed_quantity_not_positive": int((~(qty > 0)).sum()),
        "removed_unit_price_not_positive": int((~(price > 0)).sum()),
        "removed_missing_customer_id": int((~has_cust).sum()),
        "removed_cancellations": int(is_cancel.sum()),
        "note_overlap": "per-rule counts overlap; the total below is the union of all rules",
        "records_after_cleaning": int(keep.sum()),
        "records_removed_total": int(before - keep.sum()),
    }
    out = df[keep].copy()
    out["quantity"] = qty[keep].astype("int64")
    out["unit_price_text"] = [repr(float(v)) for v in price[keep]]      # shortest exact text
    out["customer_id"] = out["customer_id"].astype("int64").astype(str)
    out["event_time"] = pd.to_datetime(out["event_time"])
    return out.reset_index(drop=True), stats


def classify(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Same shopping cart -> same sales_mode (rule applied once per invoice number)."""
    modes = {t: sales_mode_for(t) for t in cleaned["transaction_id"].unique()}
    cleaned["sales_mode"] = cleaned["transaction_id"].map(modes)
    return cleaned


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["transaction_id", "customer_id", "product_id", "quantity"]].copy()
    out["unit_price"] = df["unit_price_text"]
    out["event_time"] = df["event_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    out["sales_mode"] = df["sales_mode"]
    return out[ONLINE_COLUMNS]


def prepare_data(raw_path: str, online_file: str, download_date: str = ""):
    """Steps 2 of Section 9 up to (not including) the database load.

    Returns (customer_master, product_master, periodic_df, stats).
    """
    print(f"[prepare] reading {raw_path} ...")
    raw = load_raw(raw_path)
    cleaned, stats = clean(raw)
    print(f"[prepare] transactions BEFORE cleaning tests: {stats['records_before_cleaning']:,}")
    print(f"[prepare] transactions AFTER  cleaning tests: {stats['records_after_cleaning']:,}")
    cleaned = classify(cleaned)

    cust = build_customer_master(cleaned)
    prod = build_product_master(cleaned)

    online = _fmt(cleaned[cleaned["sales_mode"] == "Online"])
    Path(online_file).parent.mkdir(parents=True, exist_ok=True)
    online.to_csv(online_file, index=False)

    per = cleaned[cleaned["sales_mode"] == "Periodic"].copy()
    per = per.sort_values("event_time", kind="mergesort").reset_index(drop=True)   # stable sort
    n = len(per)
    per["release_hour"] = (np.arange(n) * N_GROUPS) // max(n, 1)                  # 48 near-equal groups
    sizes = per.groupby("release_hour").size()
    stats.update({
        "online_transactions": int(len(online)),
        "periodic_transactions": int(n),
        "online_share_pct": round(100.0 * len(online) / max(len(cleaned), 1), 2),
        "customer_master_tuples": int(len(cust)),
        "customer_master_pages": int(cust["page_no"].max() + 1) if len(cust) else 0,
        "product_master_tuples": int(len(prod)),
        "product_master_pages": int(prod["page_no"].max() + 1) if len(prod) else 0,
        "periodic_groups": int(sizes.size),
        "periodic_group_min": int(sizes.min()) if n else 0,
        "periodic_group_max": int(sizes.max()) if n else 0,
        "first_event_time": str(cleaned["event_time"].min()),
        "last_event_time": str(cleaned["event_time"].max()),
        "distinct_customers": int(len(cust)),
        "distinct_products": int(len(prod)),
    })
    periodic_df = _fmt(per)
    periodic_df["release_hour"] = per["release_hour"].astype(int).values

    cust.drop(columns="page_no").to_csv(DATA_DIR / "customer_master_data.csv", index=False)
    prod.drop(columns="page_no").to_csv(DATA_DIR / "product_master_data.csv", index=False)
    write_data_note(stats, raw_path, download_date)
    (DATA_DIR / "prepare_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"[prepare] online={stats['online_transactions']:,} periodic={n:,}; "
          f"customer pages={stats['customer_master_pages']} product pages={stats['product_master_pages']}; "
          f"periodic group sizes min={stats['periodic_group_min']} max={stats['periodic_group_max']}")
    return cust, prod, periodic_df, stats


def write_data_note(stats: dict, raw_path: str, download_date: str):
    dl = download_date or "NOT RECORDED - fill in the date you downloaded the file"
    text = f"""DATA NOTE (generated by Mesh-Join/data_cleaning.py from the real run)

Source : Chen, D., Sain, S. L., and Guo, K. Online Retail. UCI Machine Learning Repository.
         https://archive.ics.uci.edu/dataset/352/online+retail   (file used: {Path(raw_path).name})
Downloaded on: {dl}

Cleaning rules (in code, nothing edited by hand): keep quantity > 0, unit_price > 0 and a present
customer_id; drop cancellations (transaction_id starting with C).
  Transactions before cleaning : {stats['records_before_cleaning']:,}
  Transactions after cleaning  : {stats['records_after_cleaning']:,}
  Removed in total             : {stats['records_removed_total']:,}

sales_mode rule: int(MD5(transaction_id)[:8],16) % 10 -> 0..6 Online, 7..9 Periodic (whole invoice).
store rule     : Online -> store 1; Periodic -> 2 + (int(MD5(transaction_id)[:8],16) % 4).
Master Data    : stable(text,n) = int(MD5(text.encode('utf-8'))[:8],16) % n; no random numbers.
Online rows    : {stats['online_transactions']:,}   Periodic rows: {stats['periodic_transactions']:,}
Customers      : {stats['customer_master_tuples']:,} ({stats['customer_master_pages']} pages of 500)
Products       : {stats['product_master_tuples']:,} ({stats['product_master_pages']} pages of 500)
Periodic groups: {stats['periodic_groups']} (smallest {stats['periodic_group_min']}, largest {stats['periodic_group_max']})
Dates covered  : {stats['first_event_time']}  ..  {stats['last_event_time']}
"""
    (DATA_DIR / "data_source_note.txt").write_text(text, encoding="utf-8")
