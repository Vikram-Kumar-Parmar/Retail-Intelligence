"""Deterministic rules: sales_mode, store, customer and product Master Data.

No random numbers are used anywhere: every value is a function of the id.
"""
import hashlib

import pandas as pd

from config import (AGE_GROUPS, CATEGORIES, CITY_CATEGORIES, GENDERS, MARITAL,
                    OCCUPATIONS, PAGE_SIZE, STAY_YEARS, SUPPLIERS)


def md5_int(text: str) -> int:
    """First 8 hex characters of MD5(text, UTF-8) as an integer."""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def stable(text: str, n: int) -> int:
    return md5_int(text) % n


def sales_mode_for(transaction_id: str) -> str:
    """Remainder 0..6 -> Online, 7..9 -> Periodic (whole invoice, Section 5.4.1)."""
    return "Online" if md5_int(transaction_id) % 10 <= 6 else "Periodic"


def store_id_for(transaction_id: str, sales_mode: str) -> int:
    """Online -> store 1; physical -> 2 + (MD5 number % 4) (Section 5.4.2)."""
    if sales_mode == "Online":
        return 1
    return 2 + (md5_int(transaction_id) % 4)


def build_customer_master(cleaned: pd.DataFrame) -> pd.DataFrame:
    """One row per customer_id; country = country of the first cleaned transaction."""
    first = cleaned.drop_duplicates("customer_id", keep="first")[["customer_id", "country"]]
    rows = []
    for cid, country in zip(first["customer_id"], first["country"]):
        rows.append((
            cid,
            GENDERS[stable(cid, 2)],
            AGE_GROUPS[stable(cid + "age", 7)],
            OCCUPATIONS[stable(cid + "occ", 10)],
            CITY_CATEGORIES[stable(cid + country, 3)],
            STAY_YEARS[stable(cid + "stay", 5)],
            MARITAL[stable(cid + "mar", 2)],
        ))
    df = pd.DataFrame(rows, columns=[
        "customer_id", "gender", "age_group", "occupation", "city_category",
        "stay_in_current_city_years", "marital_status"])
    df = df.sort_values("customer_id", key=lambda s: s.astype("int64"), kind="stable").reset_index(drop=True)
    df["page_no"] = df.index // PAGE_SIZE
    return df


def build_product_master(cleaned: pd.DataFrame) -> pd.DataFrame:
    """One row per product_id; name = longest non-empty Description (first one on ties)."""
    desc = cleaned["product_name"].fillna("").astype(str).str.strip()
    tmp = pd.DataFrame({"product_id": cleaned["product_id"].values,
                        "desc": desc.values,
                        "dlen": desc.str.len().values})
    best_idx = tmp.groupby("product_id", sort=False)["dlen"].idxmax()   # first max = file order
    best = tmp.loc[best_idx.values, ["product_id", "desc"]]
    rows = []
    for pid, d in zip(best["product_id"], best["desc"]):
        sup_id, sup_name = SUPPLIERS[stable(pid, 5)]
        rows.append((pid, d if d else "UNKNOWN", CATEGORIES[stable(pid + "cat", 10)], sup_id, sup_name))
    df = pd.DataFrame(rows, columns=["product_id", "product_name", "product_category",
                                     "supplier_id", "supplier_name"])
    df = df.sort_values("product_id", kind="stable").reset_index(drop=True)
    df["page_no"] = df.index // PAGE_SIZE
    return df
