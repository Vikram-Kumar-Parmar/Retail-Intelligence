# Retail Intelligence — local analytics dashboard

A polished, interactive **Streamlit + Plotly** extension of the submitted Retail Data Warehouse project. This adds a dashboard, **not** changes to the original ETL/database scripts or graded submission.

## Instant start on Windows (recommended)

1. Keep `Dashboard/` inside the **project folder**, alongside `Data/`, `Create-DW/`, etc.
2. Install **Python 3.11+**. Internet is needed **once** to install Python packages.
3. Double-click **`Dashboard/START_DASHBOARD.bat`**. It creates `.venv`, installs dependencies, and opens Streamlit (usually `http://localhost:8501`).
4. In the sidebar, select **CSV** (the default). This works **without MySQL** and uses the three CSVs already inside `Data/`, but only **278,740 Online** sales lines are in that CSV. The Periodic sales rows are available from the loaded MySQL DW, not this zip.
5. Explore Overview, Sales & Stores, Products, Customers, MESHJOIN Operations, and Ask the Warehouse.

Alternative terminal commands from the **Dashboard** directory:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

## Optional: use the loaded MySQL warehouse

Ensure MySQL is running and the original pipeline has populated `retail_dw` (or your database name). The dashboard **only SELECTs**; it does not recreate or mutate database tables.

Double-click `START_MYSQL_DASHBOARD.bat`, enter the database credentials, and choose **MySQL** in the sidebar after the dashboard starts. You can also set these as **environment variables** before starting the app:

```cmd
set DWH_HOST=127.0.0.1
set DWH_PORT=3306
set DWH_DB=retail_dw
set DWH_USER=root
set DWH_PASSWORD=your_local_password
python -m streamlit run Dashboard\app.py
```

**Security:** Do not commit passwords. Prefer MySQL read-only user credentials for a presentation. The supplied scripts contain NO credentials; the demo launcher temporarily keeps the password in its process environment. Do not reuse a compromised database password.

## Source provenance and limits

- **CSV mode (ONLINE ONLY, 278,740 of 397,884 total facts):** `Data/online_sales_stream.csv`, `Data/customer_master_data.csv`, `Data/product_master_data.csv`; store IDs derive from the original invoice-MD5 rule in `Mesh-Join/config.py`.
- **MySQL mode:** the actual `fact_sales` star-schema joins from `Create-DW/create_dw.sql`, plus SELECT queries on `shed_table`, `periodic_sales_data` and `live_sales_cache`.
- **Processing modes:** not present in CSV, and neither are the 119,144 Periodic lines. CSV mode labels the processing-mode visual as a **historical snapshot** using `Project-Report/run_statistics.md` instead of fabricating row-level assignments. MySQL mode uses actual `fact_sales.processing_mode`.
- **Operational dashboard:** reflects **completed-run totals**, NOT true live queue telemetry. Click **Refresh source** to re-query; this is not second-by-second monitoring.
- **Derived demographics:** the original source does not provide age, gender or occupation; the project deterministically synthesized these fields for its warehouse model. Never interpret them as real-world customer demographics.
- **Date coverage:** 2010-12-01 to 2011-12-09. December 2011 is partial. Prices are displayed in GBP (£), consistent with the UK Online Retail dataset; currency conversion is not performed.
- **Rows vs orders:** sales lines are rows; invoices are distinct `transaction_id`. Revenue = `quantity × unit_price`.
- **Submission validation:** prior `verify_load.py` reported an invoice-level event-time mismatch; `verify_rows.py` passed all 397,884 rows. The dashboard does not assert all verification checks passed.
- **Submission is unchanged:** dashboard is an optional portfolio/demo addition after grading.

## Demo script (2–3 minutes)

1. **Overview:** show revenue, invoices and revenue trends, then change date filter.
2. **Sales & Stores:** compare stores and sales channel revenue.
3. **Products/Customers:** show category rankings and explain synthetic enrichment.
4. **MESHJOIN Operations:** demonstrate peak shedding, replay and caching; distinguish static report from live DB.
5. **Ask the Warehouse:** answer likely judge questions using filter-aware, deterministic computed answers.

## Troubleshooting

- **CSV files missing:** keep the original `Data/` directory at the same level as `Dashboard/`; don't extract `Dashboard` alone to an unrelated location.
- **`py` not found:** install Python 3.11+, enable the Python launcher, or replace `py -3` with `python` in the batch file.
- **No browser opens:** visit `http://localhost:8501` manually.
- **MySQL connection refused:** check MySQL service, host/port, schema name and credentials; switch sidebar to CSV for a no-DB demo.
- **A page takes a few seconds:** the first CSV load joins ~279k rows; subsequent interactions are cached for 15 minutes.

## Test (optional)

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```
