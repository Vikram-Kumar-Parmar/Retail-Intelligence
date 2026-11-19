# Building and Analysing a Near-Real-Time and Periodic Data Warehouse for a Retail Chain

**Course:** DS3003 & DS3004 - Data Warehousing & Business Intelligence, Fall 2026, FAST-NUCES
**Student:** Vikram Kumar, 23K-2062
**Dataset:** Chen, D., Sain, S. L., and Guo, K. *Online Retail.* UCI Machine Learning Repository,
https://archive.ics.uci.edu/dataset/352/online+retail - downloaded on **[DATE - fill in]**.

> **How to finish this report.** Every number marked `[RUN: ...]` must be copied from `run_statistics.md`, which
> `collect_report_stats.py` writes from the real final run. Sections 11 and 13 are yours to write. Nothing in this
> file was produced from the synthetic test data.

## 1. Project overview
A retail chain sells on its website (online) and in four physical stores. Online sales arrive as a stream and must reach the
data warehouse (DW) in near real time. Store sales are extracted from the operational sources once per (simulated) hour.
Both forms come from the single UCI transactional file. The system cleans the file, prepares deterministic Master Data,
enriches every transaction with MESHJOIN, consolidates it and loads a MySQL star schema that answers 20 OLAP questions.

## 2. Problem statement
1. Clean the raw file in code. 2. Generate Master Data without random numbers. 3. Split the data into an online stream
(`online_sales_stream.csv`, read by a thread at a controllable speed) and a periodic staging table (48 groups, one per hour).
4. Join the stream with the Master Data without per-tuple random reads and without waiting for an hour to close (MESHJOIN).
5. Give online data priority and shed periodic data to disk when MESHJOIN cannot cope. 6. Load the star schema and analyse it.

## 3. System architecture
```
UCI Online Retail.xlsx -> cleaning -> MD5 classification -> online_sales_stream.csv --OnlineThread--+
                                                         \-> periodic_sales_data (48 groups)       |
                                                              --PeriodicThread (1 group / hour)----+--> Stream buffer (1,000)
                                                                                                        |   online first, then periodic
 shed_table (disk) <== periodic only, peak mode =========================== shed outbox <==============+
        | replay in non-peak                                                                            v
        +----------------------------------------------> MeshJoinThread: chunk w=500 -> hash table H + queue
                                                          customer_master_data / product_master_data pages of 500 (disk)
                                                                                                        |
                              consolidation: store (Store_list), SALE = quantity x price -> live_sales_cache (bonus, Live only)
                                                                                      \-> fact_sales  <-/  (copied in slow periods)
```
Modules: `config`, `database`, `data_cleaning`, `master_data`, `meshjoin`, `scheduler`, `threads`, `dw_loader`, `main`.

## 4. Data cleaning
Rules (in code): keep `quantity > 0`, `unit_price > 0`, customer id present; drop cancellations (`transaction_id` starts with `C`).

| Measure | Value |
|---|---|
| Transactions before the cleaning tests | [RUN: records_before_cleaning] |
| Transactions after the cleaning tests | [RUN: records_after_cleaning] |
| Removed | [RUN: records_removed_total] |

## 5. sales_mode rule and stores
`int(MD5(transaction_id)[:8], 16) % 10`: 0-6 online, 7-9 physical store. It is computed once per invoice, so all lines of a
cart share the mode. Online carts use store 1; physical carts use `store_id = 2 + (int(MD5(transaction_id)[:8],16) % 4)`
(stores: 1 Online/Web, 2 London Bridge, 3 Manchester Arndale, 4 Birmingham Bullring, 5 Edinburgh Princes).
Result: online [RUN: online_transactions] ([RUN: online_share_pct] %), periodic [RUN: periodic_transactions].

## 6. Master Data
`stable(text, n) = int(MD5(text in UTF-8)[:8], 16) % n`; no random numbers. Customer: gender, age group, occupation, city category
(`customer_id + country` of the first cleaned transaction), years in city, marital status. Product: longest non-empty description
(else `UNKNOWN`), category, supplier. Customer Master Data: [RUN: customer tuples] tuples = **[RUN: customer pages] partitions** of 500.
Product Master Data: [RUN: product tuples] tuples = **[RUN: product pages] partitions**. The last page of each table is partial.
Periodic groups: 48; smallest **[RUN: periodic_group_min]**, largest **[RUN: periodic_group_max]** transactions.

## 7. Star schema
```
 dim_customer --\                        /-- dim_product -- (supplier_id)
 dim_supplier ---+---- fact_sales -------+
 dim_store ------/   (transaction_id, quantity, unit_price, revenue, sales_mode, processing_mode, source_key)
 dim_time -------/
```
Fact grain: one transaction line (one product, one cart, one customer, one store, one supplier, one time). Measures: `quantity`,
`revenue = quantity x unit_price`. Dimensions use surrogate keys and keep the original ids. `dim_time` stores date, hour, day of month,
month, quarter, year, day of week, weekday/weekend, season (Spring Mar-May, Summer Jun-Aug, Fall Sep-Nov, Winter Dec-Feb) and half-year.
Missing dimension rows are inserted before the fact rows, in the same database transaction. `processing_mode` (Live / Hourly /
Shed-Replay) records how a transaction reached the DW. The view `STORE_QUARTERLY_SALES` (Q20) sits on top of the star.

## 8. MESHJOIN
The stream S is joined with the Master Data R, which stays on disk and is scanned cyclically in 500-tuple pages. Each iteration:
(1) read the next customer page and the next product page into the disk buffers; (2) move up to w = 500 transactions from the Stream
buffer into the hash table H and append one chunk node to the queue; (3) probe every Master Data tuple in H and enrich the matching
transactions; (4) remove the oldest chunk only when it has met every customer page and every product page.
H is two multi-map indexes (customer id, product id) over the same transaction objects (no copies). Because the two tables have
different page counts, each chunk carries its own counters; it stays `max(customer pages, product pages)` iterations
([RUN: max pages]). A transaction is enriched exactly once (the attribute is empty until its page is probed). The implementation was tested
against a plain join on 2,600 transactions arriving in irregular chunk sizes (identical results; every chunk stayed the exact number of iterations).

### Three shortcomings of MESHJOIN
1. **Latency is tied to the size of R, not to the arrival rate.** Every transaction waits one full cycle through the Master Data (here
   max(pages) iterations) even if the stream is idle, so a larger R means a larger delay.
2. **Wasted disk I/O at low rates.** All pages of R are read every cycle although a chunk may hold only a few transactions; at 10
   tx/s the join re-reads whole partitions to enrich a handful of tuples.
3. **Memory and tuning rigidity / no skew handling.** The queue and H must hold a full cycle of chunks, the partition size, chunk size
   and buffer limits must be tuned together, and every R tuple is probed whether or not any pending transaction can match it
   (hot and cold customers are treated alike).

## 9. Threads, priority and load shedding
* **OnlineThread** reads the CSV at the selected speed (peak 80, non-peak 10 tx/s, both configurable) and blocks when the buffer is full.
* **PeriodicThread** wakes once per simulated hour (60 s), extracts the next group (sorted by event time, flagged `released`, never twice) and offers it to the buffer.
* **MeshJoinThread** runs MESHJOIN, writes the Shed_table, replays shed rows and loads the DW.
* Priority: online first, periodic second, shed rows last (chunks are built in that order). In peak mode a full buffer sheds periodic
  transactions (a new online transaction displaces the newest buffered periodic one); in non-peak mode the producer waits (back-pressure).
  Shed rows keep their flag, shed time and speed and are processed in non-peak mode when nothing else is waiting. Online transactions are never shed.

| Measure | Value |
|---|---|
| Shed to disk in peak hours | [RUN: shed peak] |
| Shed to disk in non-peak hours | [RUN: shed non-peak] (shedding is not allowed in non-peak) |
| Replayed (processed) in non-peak hours | [RUN: replayed] |
| Still on the shed disk at the end | [RUN: still on disk] |

Settings used for the final run: peak [RUN] tx/s, non-peak [RUN] tx/s, [RUN] s per simulated hour.

## 10. Bonus - Live sales cache (implemented)
Completed online results are written to `live_sales_cache` (fully enriched rows). When the speed is non-peak and nothing is waiting,
up to 1,000 cached rows are copied into `fact_sales` and exactly those rows are deleted - all in one database transaction, so a failure
leaves the cache untouched. Periodic results never use the cache. Rows left in the cache at the end: [RUN: cache rows left].

## 11. Verification (tests on synthetic data, and on the real load)
Tests are in `Mesh-Join/tests`. On a **synthetic** test file (clearly labelled, never reported as results): the MD5 rules, determinism of the
Master Data, cleaning, MESHJOIN against a reference join, a full concurrent load (fact rows = cleaned rows, no duplicates,
revenue sums equal, shed rows replayed, mode commands, quit + resume) and the key queries against pandas all passed.
Run `verify_load.py` and `verify_queries.py` on the real file and write the outcome here: **[RUN: result of verify_load.py / verify_queries.py]**.

## 12. OLAP analysis - representative findings
Write 5-8 sentences from the **real** query output (Q1 top products, Q8 city categories, Q9 category growth, Q12 store growth,
Q16 top pairs for bundling, Q17 ROLLUP totals). **[FILL FROM YOUR QUERY RESULTS]**

**Q19 - revenue spikes.** Spike rows: [RUN]; products: [RUN]; busiest spike days: [RUN]. Comment on what you see (e.g. whether spikes cluster
before Christmas, around bulk wholesale orders, or on single large carts) **[FILL FROM YOUR RESULTS]**.

## 13. Technical challenges and what I learned
**[WRITE THIS YOURSELF - your own experience.]** Points you can honestly discuss if they match what happened: coordinating three threads with a
bounded buffer, making each chunk wait for two Master Data cycles of different length, keeping facts duplicate-free across shedding and resume,
and the difference between a stream-relation join and a per-tuple lookup.

## 14. Interpretations of the specification
`processing_mode` values, the view vs. materialized view (view plus stored copy `mv_store_quarterly_sales`), store enrichment outside MESHJOIN,
the loaded year (2011), the `source_key` idempotency column, ASCII hyphens in age groups - see the ReadMe, section 11.
