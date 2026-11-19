"""Read-only analytics adapters for the submitted Retail Data Warehouse.

CSV mode works from the included cleaned stream + deterministic master data.
MySQL mode reads the actual completed star schema and operational tables.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'Data'
REPORT = ROOT / 'Project-Report' / 'run_statistics.md'
STORES = {1: 'Online', 2: 'London Bridge', 3: 'Manchester Arndale',
          4: 'Birmingham Bullring', 5: 'Edinburgh Princes'}


def store_from_invoice(invoice: str, mode: str) -> int:
    if mode == 'Online':
        return 1
    return 2 + (int(hashlib.md5(str(invoice).encode('utf-8')).hexdigest()[:8], 16) % 4)


def required_files():
    return [DATA / 'online_sales_stream.csv', DATA / 'customer_master_data.csv',
            DATA / 'product_master_data.csv']


def load_csv():
    missing = [str(p) for p in required_files() if not p.exists()]
    if missing:
        raise FileNotFoundError('Required CSV files missing: ' + ', '.join(missing))
    d = pd.read_csv(DATA / 'online_sales_stream.csv', dtype={'transaction_id': str,
                     'customer_id': str, 'product_id': str}, parse_dates=['event_time'])
    c = pd.read_csv(DATA / 'customer_master_data.csv', dtype={'customer_id': str})
    p = pd.read_csv(DATA / 'product_master_data.csv', dtype={'product_id': str})
    d = d.merge(c, on='customer_id', how='left', validate='many_to_one')
    d = d.merge(p, on='product_id', how='left', validate='many_to_one')
    # Invoice-level consistent store mapping, identical to Mesh-Join/config.py.
    invoices = d[['transaction_id', 'sales_mode']].drop_duplicates('transaction_id').copy()
    invoices['store_id'] = [store_from_invoice(i, m) for i, m in zip(invoices.transaction_id, invoices.sales_mode)]
    d = d.merge(invoices[['transaction_id', 'store_id']], on='transaction_id', how='left', validate='many_to_one')
    d['store_name'] = d['store_id'].map(STORES)
    d['revenue'] = d['quantity'] * d['unit_price']
    d['processing_mode'] = 'Not recorded in CSV'
    return normalize(d), load_snapshot(), 'CSV'


def mysql_settings():
    return dict(host=os.getenv('DWH_HOST', '127.0.0.1'),
                port=int(os.getenv('DWH_PORT', '3306')),
                database=os.getenv('DWH_DB', 'retail_dw'),
                user=os.getenv('DWH_USER', 'root'),
                password=os.getenv('DWH_PASSWORD', ''))


def load_mysql():
    import mysql.connector
    cfg = mysql_settings()
    db = mysql.connector.connect(**cfg, connection_timeout=12)
    try:
        sql = """
        SELECT f.transaction_id, c.customer_id, p.product_id, f.quantity,
               f.unit_price, f.revenue, t.event_time, f.sales_mode, f.processing_mode,
               s.store_id, s.store_name, c.gender, c.age_group, c.occupation,
               c.city_category, c.stay_in_current_city_years, c.marital_status,
               p.product_name, p.product_category, p.supplier_id
        FROM fact_sales f
        JOIN dim_customer c ON c.customer_key=f.customer_key
        JOIN dim_product p ON p.product_key=f.product_key
        JOIN dim_store s ON s.store_key=f.store_key
        JOIN dim_time t ON t.time_key=f.time_key
        """
        cur = db.cursor(dictionary=True)
        cur.execute(sql)
        cols = cur.column_names
        rows = cur.fetchall()
        df = pd.DataFrame.from_records(rows, columns=cols)
        operational = {}
        queries = {
            'shed_total': 'SELECT COUNT(*) FROM shed_table',
            'shed_replayed': 'SELECT COUNT(*) FROM shed_table WHERE replayed=1',
            'shed_pending': 'SELECT COUNT(*) FROM shed_table WHERE replayed=0',
            'shed_peak': "SELECT COUNT(*) FROM shed_table WHERE shed_speed='peak'",
            'cache_pending': 'SELECT COUNT(*) FROM live_sales_cache',
            'periodic_groups': 'SELECT COUNT(DISTINCT release_hour) FROM periodic_sales_data',
            'periodic_released': 'SELECT COUNT(DISTINCT release_hour) FROM periodic_sales_data WHERE released=1',
        }
        for name, query in queries.items():
            try:
                cur.execute(query)
                operational[name] = int(cur.fetchone()[0])
            except mysql.connector.Error:
                operational[name] = None
        cur.close()
    finally:
        db.close()
    if df.empty:
        raise ValueError('fact_sales is empty; load the warehouse first or use CSV mode.')
    return normalize(df), operational, 'MySQL'


def load_snapshot():
    result = {}
    if (DATA / 'prepare_stats.json').exists():
        result.update(json.loads((DATA / 'prepare_stats.json').read_text(encoding='utf-8')))
    if REPORT.exists():
        import re
        content = REPORT.read_text(encoding='utf-8')
        for key, phrase in [('shed_total', 'Shed to disk during PEAK'),
                            ('shed_replayed', 'Replayed (processed) afterwards'),
                            ('shed_pending', 'Still on the shed disk'),
                            ('cache_pending', 'Rows left in live_sales_cache')]:
            match = re.search(r'^\|\s*' + re.escape(phrase) + r'\s*\|\s*([\d,]+)', content, re.M)
            if match:
                result[key] = int(match.group(1).replace(',', ''))
        result['shed_peak'] = result.get('shed_total')
        result['periodic_released'] = 48  # reported final completed run; snapshot only
    result['periodic_groups'] = result.get('periodic_groups', 48)
    return result


def normalize(d):
    d['event_time'] = pd.to_datetime(d['event_time'])
    for c in ['quantity', 'unit_price', 'revenue']:
        d[c] = pd.to_numeric(d[c])
    for c in ['transaction_id', 'customer_id', 'product_id', 'store_name',
              'sales_mode', 'processing_mode', 'product_name', 'product_category',
              'gender', 'age_group', 'occupation', 'city_category', 'supplier_id']:
        d[c] = d[c].fillna('Unknown').astype(str)
    d['date'] = d.event_time.dt.normalize()
    d['month'] = d.event_time.dt.to_period('M').astype(str)
    d['quarter'] = d.event_time.dt.to_period('Q').astype(str)
    d['weekday'] = d.event_time.dt.day_name()
    d['day_type'] = d.event_time.dt.dayofweek.map(lambda x: 'Weekend' if x >= 5 else 'Weekday')
    d['hour'] = d.event_time.dt.hour
    return d


def safe_pct(part, whole):
    return 100 * float(part) / float(whole) if whole else 0.0
