"""MySQL helpers. Credentials are asked at start-up and never stored in code."""
import getpass
import os
import re
from pathlib import Path

import mysql.connector

from config import STORES


def prompt_credentials() -> dict:
    """Ask for host, port, database, user, password (env vars DWH_* skip a prompt, for batch runs)."""
    def ask(env, label, default=None):
        v = os.environ.get(env)
        if v:
            return v
        s = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
        return s or default

    host = ask("DWH_HOST", "MySQL host", "localhost")
    port = ask("DWH_PORT", "MySQL port", "3306")
    database = ask("DWH_DB", "Database name")
    user = ask("DWH_USER", "User name", "root")
    password = os.environ.get("DWH_PASSWORD")
    if password is None:
        password = getpass.getpass("Password (hidden): ")
    if not database:
        raise SystemExit("A database name is required.")
    return {"host": host, "port": int(port), "database": database, "user": user, "password": password}


def connect(creds: dict, autocommit: bool = False):
    conn = mysql.connector.connect(host=creds["host"], port=creds["port"], database=creds["database"],
                                   user=creds["user"], password=creds["password"],
                                   autocommit=autocommit, charset="utf8mb4", connection_timeout=15)
    cur = conn.cursor()
    cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
    cur.close()
    return conn


def run_script(conn, path) -> int:
    """Execute a plain SQL script (statements separated by ';', '--' comments allowed)."""
    text = re.sub(r"--[^\n]*", "", Path(path).read_text(encoding="utf-8"))
    cur = conn.cursor()
    n = 0
    for stmt in text.split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
            n += 1
    conn.commit()
    cur.close()
    return n


def _batches(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def load_master_tables(conn, cust_df, prod_df):
    cur = conn.cursor()
    cur.execute("DELETE FROM customer_master_data")
    cur.execute("DELETE FROM product_master_data")
    csql = ("INSERT INTO customer_master_data (customer_id, gender, age_group, occupation, city_category, "
            "stay_in_current_city_years, marital_status, page_no) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)")
    psql = ("INSERT INTO product_master_data (product_id, product_name, product_category, supplier_id, "
            "supplier_name, page_no) VALUES (%s,%s,%s,%s,%s,%s)")
    crow = [tuple(map(_py, r)) for r in cust_df.itertuples(index=False, name=None)]
    prow = [tuple(map(_py, r)) for r in prod_df.itertuples(index=False, name=None)]
    for b in _batches(crow, 2000):
        cur.executemany(csql, b)
    for b in _batches(prow, 2000):
        cur.executemany(psql, b)
    conn.commit()
    cur.close()


def load_staging(conn, periodic_df):
    """periodic_sales_data: sorted by event_time, release_hour = group 0..47, released = 0."""
    cur = conn.cursor()
    cur.execute("DELETE FROM periodic_sales_data")
    sql = ("INSERT INTO periodic_sales_data (transaction_id, customer_id, product_id, quantity, unit_price, "
           "event_time, sales_mode, release_hour, released) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)")
    rows = [tuple(map(_py, r)) for r in periodic_df.itertuples(index=False, name=None)]
    for b in _batches(rows, 5000):
        cur.executemany(sql, b)
    conn.commit()
    cur.close()


def _py(v):
    """numpy scalars -> plain Python (mysql-connector cannot bind numpy types)."""
    return v.item() if hasattr(v, "item") else v


def scalar(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or ())
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def store_rows():
    return list(STORES)
