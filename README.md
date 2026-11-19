# Retail Data Warehouse - near-real-time + periodic ETL with MESHJOIN

DS3003 & DS3004, Data Warehousing & Business Intelligence, Fall 2026, FAST-NUCES.
Author: Vikram Kumar (23K-2062).

**Which load is used?  The BONUS load (Section 16) is ON by default:** completed *online* (Live) join results are
written to `live_sales_cache` first and copied into `fact_sales` during a slow (non-peak) period; the copied rows
are deleted from the cache. Periodic results always load straight into `fact_sales`. Start with `--no-cache` to
switch the bonus off (Live results are then inserted directly into `fact_sales`).

## 1. Requirements
* Python 3.11+  (`pip install -r Mesh-Join/requirements.txt` -> pandas, openpyxl, mysql-connector-python)
* MySQL 8.x (tested on 8.0) and an empty schema you can write to, e.g. `retail_dw`
* The UCI file `Online Retail.xlsx` (see section 3)

## 2. Folder layout
| Folder | Content |
|---|---|
| `Create-DW/create_dw.sql` | drops every old object (also the view) and creates the star schema + working tables |
| `Mesh-Join/` | Python project (`main.py` is the entry point) and `tests/` |
| `Queries-DW/olap_queries.sql` | Q1 - Q20 and the view `STORE_QUARTERLY_SALES` |
| `Data/` | `online_sales_stream.csv`, `customer_master_data.csv`, `product_master_data.csv`, `data_source_note.txt` |
| `Project-Report/` | `project_report.md`, `collect_report_stats.py` (real numbers for the report) |

## 3. Dataset
1. Download `Online Retail.xlsx` from <https://archive.ics.uci.edu/dataset/352/online+retail>.
2. Put it at `Data/Raw/Online Retail.xlsx` (or give another path when asked). Do **not** edit it by hand - the program cleans it.
3. Note the download date (pass it with `--download-date YYYY-MM-DD` so it lands in `Data/data_source_note.txt`).

Citation (also in the report): Chen, D., Sain, S. L., and Guo, K. *Online Retail.* UCI Machine Learning Repository.
https://archive.ics.uci.edu/dataset/352/online+retail - downloaded on: 12 November 2026.

## 4. Create the database
Create an empty schema once (`CREATE DATABASE retail_dw CHARACTER SET utf8mb4;`). `main.py` runs
`Create-DW/create_dw.sql` itself on every normal start. To run it by hand: open the script in MySQL Workbench,
select the schema (`USE retail_dw;`) and execute it. It is safe to run repeatedly because it drops the old copy first.

## 5. Prepare the data and start the program
```
cd Mesh-Join
python main.py --download-date 2026-11-09
```
The program asks for: host, port, database name, user name, **password (hidden, never stored)**, the path of the online
file (default `Data/online_sales_stream.csv`, it is created there) and the path of the UCI dataset.
Then it: (1) creates the working tables and the DW, (2) reads and cleans the file, prints the transaction counts before and
after the cleaning tests, writes `online_sales_stream.csv`, builds both Master Data tables and loads them, loads the
48 periodic groups into the staging table `periodic_sales_data`, (3) starts the three threads, (4) loads the DW,
(5) accepts commands until `quit`.
At start-up it prints the peak / non-peak speeds, the arrival mode, seconds per simulated hour, the stream-buffer limit,
the page size, the chunk size and the real page counts of both Master Data tables.

Options (all optional):

| Flag | Default | Meaning |
|---|---|---|
| `--peak-rate N` / `--nonpeak-rate N` | 80 / 10 | online transactions per second (can also be changed live with `set-peak N` / `set-nonpeak N`) |
| `--hour-seconds S` | 60 | real seconds per simulated hour (the 48 periodic groups need 48 x S seconds) |
| `--speed peak\|non-peak` | peak | arrival mode at start |
| `--mode online\|periodic\|both` | both | mode at start |
| `--no-cache` | cache ON | disable the bonus cache |
| `--exit-when-done` | off | shut down automatically once everything is loaded (batch runs) |
| `--resume` | off | skip preparation and continue an interrupted run (online lines already loaded or cached are skipped) |
| `--raw-file`, `--online-file` | prompt | paths, to skip the prompts |

Environment variables `DWH_HOST, DWH_PORT, DWH_DB, DWH_USER, DWH_PASSWORD, DWH_RAW_FILE, DWH_ONLINE_FILE` can answer the
prompts for unattended runs; the password is never written into any file.

**Run times.** The online file has ~70 % of the cleaned transactions. With the specified settings (peak 80 tx/s,
60 s per hour) a complete run takes about an hour (online stream and the 48 periodic hours run side by side).
For a quick complete load use e.g. `python main.py --peak-rate 3000 --nonpeak-rate 3000 --hour-seconds 2 --exit-when-done`.
Report the settings you actually used.

## 6. Commands (type while the program runs)
| Command | Effect |
|---|---|
| `online` | online form only; periodic groups are **not** extracted |
| `periodic` | periodic groups only; the stream reader is paused |
| `both` | both forms; Live transactions are taken first |
| `peak` | fast speed; periodic transactions may be shed to disk |
| `non-peak` | slow speed; shed transactions are processed; cached Live results are copied into the DW |
| `status` | mode, speed, stream-buffer count, transactions in H, transactions still on the shed disk (+ cache and progress) |
| `quit` | stop the threads, finish everything that is in memory, close all connections |

## 7. Finishing a load (important before running the queries)
Type `non-peak` and watch `status` until **Shed disk = 0** and **Live cache = 0** and all 48 periodic groups are
released. (`--exit-when-done` does all of this by itself.) `quit` before that leaves cached/shed rows in their tables;
start again with `--resume` and finish in non-peak mode.

## 8. Run the 20 queries
Open `Queries-DW/olap_queries.sql` in MySQL Workbench, select the schema and run it (or
`mysql -u USER -p retail_dw < Queries-DW/olap_queries.sql`). Each question is labelled `-- Q1.` ... `-- Q20.`; the
first statement prints the loaded date range. Q20 creates `STORE_QUARTERLY_SALES` and reads it with `ORDER BY`.
Q16 (product pairs) is the heaviest query and can take a few minutes on the full data set.
The "loaded year" is 2011 (the only year with twelve months); it is taken from the data, not hard-coded.

## 9. Real statistics for the report
After the final run: `cd Project-Report && python collect_report_stats.py` writes `run_statistics.md` (cleaning counts,
partition counts, group sizes, shed counts, spikes) - paste it into the report.

## 10. Tests (synthetic data only - never used for results)
```
python Mesh-Join/tests/make_synthetic_data.py "/tmp/synth/Online Retail.csv" 3500
cd Mesh-Join && python tests/test_core.py          # MD5 rules, cleaning, MESHJOIN == reference join
python main.py --raw-file "/tmp/synth/Online Retail.csv" ... --exit-when-done
python tests/verify_load.py "/tmp/synth/Online Retail.csv"   # fact table vs. source, row by row
python tests/verify_queries.py "/tmp/synth/Online Retail.csv" # key queries vs. pandas
```


## 11. Design decisions and interpretations (documented ambiguities)
* **processing_mode** is not defined in the PDF. Interpretation: `Live` (online, joined straight from the stream),
  `Hourly` (periodic, extracted by the hourly group), `Shed-Replay` (periodic, shed under load and processed later).
* **Materialized view.** Task 4 mentions a materialized view, Q20 asks for a MySQL view. MySQL has no materialized views:
  the view `STORE_QUARTERLY_SALES` is created as asked, plus a stored copy `mv_store_quarterly_sales` (rebuilt on demand).
* **Store enrichment** is done outside MESHJOIN from the in-memory Store_list (Section 5.4); the product pass adds
  product, category and supplier.
* **MESHJOIN with two Master Data tables:** two hash indexes (customer_id, product_id) point to the same transaction objects;
  every chunk has its own customer/product page counters and leaves after both full cycles; each attribute is filled exactly once.
* **Load shedding:** the Stream buffer (1,000) is bounded. Full buffer = back-pressure for the producers. Only in *peak*
  mode periodic transactions that do not fit - or that are displaced by an arriving online transaction - go to `shed_table`
  (with time and speed); in *non-peak* the PeriodicThread waits instead. Shed rows are replayed in non-peak mode only when no
  online/periodic transaction is waiting. Online transactions are never shed.
* **Idempotency key:** `fact_sales.source_key` (`O-<csv line>` / `P-<staging id>`, UNIQUE) makes duplicate facts impossible even
  across restarts; a shopping cart may legitimately contain the same product twice, so (transaction, product) is not unique.
* Age groups use ASCII hyphens (`0-17`). Prices are stored as `DECIMAL(12,4)`; ids use a binary collation because the UCI file may contain both
  `85123A` and `85123a`. Ties for "longest description" take the first one in file order.
* "Loaded year" = 2011; December 2011 is partial (the UCI file ends on 9 December 2011).
