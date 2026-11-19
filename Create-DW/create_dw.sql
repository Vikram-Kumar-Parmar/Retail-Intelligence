-- ---------- 0. Drop any old copy ------------------------------------
SET FOREIGN_KEY_CHECKS = 0;
DROP VIEW  IF EXISTS STORE_QUARTERLY_SALES;
DROP TABLE IF EXISTS mv_store_quarterly_sales;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_time;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_supplier;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS live_sales_cache;
DROP TABLE IF EXISTS shed_table;
DROP TABLE IF EXISTS periodic_sales_data;
DROP TABLE IF EXISTS customer_master_data;
DROP TABLE IF EXISTS product_master_data;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------- 1. Dimension tables -------------------------------------
CREATE TABLE dim_customer (
    customer_key               INT UNSIGNED NOT NULL AUTO_INCREMENT,
    customer_id                VARCHAR(20)  NOT NULL,
    gender                     VARCHAR(10)  NOT NULL,
    age_group                  VARCHAR(10)  NOT NULL,
    occupation                 VARCHAR(20)  NOT NULL,
    city_category              CHAR(1)      NOT NULL,
    stay_in_current_city_years VARCHAR(2)   NOT NULL,
    marital_status             VARCHAR(10)  NOT NULL,
    PRIMARY KEY (customer_key),
    UNIQUE KEY uq_dim_customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_supplier (
    supplier_key  SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    supplier_id   VARCHAR(10)  NOT NULL,
    supplier_name VARCHAR(50)  NOT NULL,
    PRIMARY KEY (supplier_key),
    UNIQUE KEY uq_dim_supplier_id (supplier_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_product (
    product_key      INT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id       VARCHAR(20)  NOT NULL,
    product_name     VARCHAR(255) NOT NULL,
    product_category VARCHAR(20)  NOT NULL,
    supplier_id      VARCHAR(10)  NOT NULL,
    PRIMARY KEY (product_key),
    UNIQUE KEY uq_dim_product_id (product_id),
    KEY ix_dim_product_category (product_category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE dim_store (
    store_key  TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    store_id   TINYINT UNSIGNED NOT NULL,
    store_name VARCHAR(40) NOT NULL,
    city       VARCHAR(30) NOT NULL,
    PRIMARY KEY (store_key),
    UNIQUE KEY uq_dim_store_id (store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- One row per distinct event_time (minute resolution in the source data).
CREATE TABLE dim_time (
    time_key     INT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_time   DATETIME     NOT NULL,
    date_value   DATE         NOT NULL,
    hour_of_day  TINYINT UNSIGNED NOT NULL,
    day_of_month TINYINT UNSIGNED NOT NULL,
    month_num    TINYINT UNSIGNED NOT NULL,
    quarter_num  TINYINT UNSIGNED NOT NULL,
    year_num     SMALLINT UNSIGNED NOT NULL,
    day_of_week  VARCHAR(10)  NOT NULL,          -- Monday .. Sunday
    day_type     VARCHAR(7)   NOT NULL,          -- Weekday / Weekend
    season       VARCHAR(6)   NOT NULL,          -- Spring/Summer/Fall/Winter
    half_year    CHAR(2)      NOT NULL,          -- H1 (Jan-Jun) / H2 (Jul-Dec)
    PRIMARY KEY (time_key),
    UNIQUE KEY uq_dim_time_event (event_time),
    KEY ix_dim_time_date (date_value),
    KEY ix_dim_time_ym (year_num, month_num)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- ---------- 2. Fact table (one row = one transaction line) ----------
CREATE TABLE fact_sales (
    sales_key       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_key      VARCHAR(20)  NOT NULL,   -- O-<csv line> / P-<staging id>: idempotency key
    transaction_id  VARCHAR(20)  NOT NULL,   -- invoice / shopping cart
    customer_key    INT UNSIGNED NOT NULL,
    product_key     INT UNSIGNED NOT NULL,
    supplier_key    SMALLINT UNSIGNED NOT NULL,
    store_key       TINYINT UNSIGNED NOT NULL,
    time_key        INT UNSIGNED NOT NULL,
    quantity        INT          NOT NULL,
    unit_price      DECIMAL(12,4) NOT NULL,
    revenue         DECIMAL(16,4) NOT NULL,  -- SALE = quantity x unit_price
    sales_mode      VARCHAR(8)   NOT NULL,   -- Online / Periodic
    processing_mode VARCHAR(12)  NOT NULL,   -- Live / Hourly / Shed-Replay
    PRIMARY KEY (sales_key),
    UNIQUE KEY uq_fact_source (source_key),
    KEY ix_fact_txn (transaction_id),
    KEY ix_fact_customer (customer_key),
    KEY ix_fact_product (product_key),
    KEY ix_fact_supplier (supplier_key),
    KEY ix_fact_store (store_key),
    KEY ix_fact_time (time_key),
    CONSTRAINT fk_fact_customer FOREIGN KEY (customer_key) REFERENCES dim_customer (customer_key),
    CONSTRAINT fk_fact_product  FOREIGN KEY (product_key)  REFERENCES dim_product  (product_key),
    CONSTRAINT fk_fact_supplier FOREIGN KEY (supplier_key) REFERENCES dim_supplier (supplier_key),
    CONSTRAINT fk_fact_store    FOREIGN KEY (store_key)    REFERENCES dim_store    (store_key),
    CONSTRAINT fk_fact_time     FOREIGN KEY (time_key)     REFERENCES dim_time     (time_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- ---------- 3. Working tables (Master Data, staging, shed, cache) ---
-- Master Data R: stays on disk; MESHJOIN reads one 500-tuple page per iteration.
CREATE TABLE customer_master_data (
    customer_id                VARCHAR(20) NOT NULL,
    gender                     VARCHAR(10) NOT NULL,
    age_group                  VARCHAR(10) NOT NULL,
    occupation                 VARCHAR(20) NOT NULL,
    city_category              CHAR(1)     NOT NULL,
    stay_in_current_city_years VARCHAR(2)  NOT NULL,
    marital_status             VARCHAR(10) NOT NULL,
    page_no                    INT         NOT NULL,   -- 0-based partition number (500 tuples each)
    PRIMARY KEY (customer_id),
    KEY ix_cmd_page (page_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE product_master_data (
    product_id       VARCHAR(20)  NOT NULL,
    product_name     VARCHAR(255) NOT NULL,
    product_category VARCHAR(20)  NOT NULL,
    supplier_id      VARCHAR(10)  NOT NULL,
    supplier_name    VARCHAR(50)  NOT NULL,
    page_no          INT          NOT NULL,
    PRIMARY KEY (product_id),
    KEY ix_pmd_page (page_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Staging table for the physical-store (periodic) transactions.
CREATE TABLE periodic_sales_data (
    staging_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    transaction_id VARCHAR(20)  NOT NULL,
    customer_id    VARCHAR(20)  NOT NULL,
    product_id     VARCHAR(20)  NOT NULL,
    quantity       INT          NOT NULL,
    unit_price     DECIMAL(12,4) NOT NULL,
    event_time     DATETIME     NOT NULL,
    sales_mode     VARCHAR(8)   NOT NULL DEFAULT 'Periodic',
    release_hour   TINYINT UNSIGNED NOT NULL,        -- group 0..47
    released       TINYINT(1)   NOT NULL DEFAULT 0,
    released_time  DATETIME(3)  NULL,
    PRIMARY KEY (staging_id),
    KEY ix_staging_group (release_hour, released)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Shed_table: periodic transactions moved to disk when MESHJOIN cannot cope.
CREATE TABLE shed_table (
    shed_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_key     VARCHAR(20)  NOT NULL,
    transaction_id VARCHAR(20)  NOT NULL,
    customer_id    VARCHAR(20)  NOT NULL,
    product_id     VARCHAR(20)  NOT NULL,
    quantity       INT          NOT NULL,
    unit_price     DECIMAL(12,4) NOT NULL,
    event_time     DATETIME     NOT NULL,
    sales_mode     VARCHAR(8)   NOT NULL,
    flag           VARCHAR(10)  NOT NULL DEFAULT 'Periodic',
    shed_time      DATETIME(3)  NOT NULL,
    shed_speed     VARCHAR(10)  NOT NULL,            -- peak / shutdown
    replayed       TINYINT(1)   NOT NULL DEFAULT 0,  -- 1 once loaded into the DW
    replayed_time  DATETIME(3)  NULL,
    PRIMARY KEY (shed_id),
    UNIQUE KEY uq_shed_source (source_key),
    KEY ix_shed_pending (replayed, shed_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Bonus: fully enriched Live (online) results wait here until a slow period.
CREATE TABLE live_sales_cache (
    cache_seq       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_key      VARCHAR(20)  NOT NULL,
    transaction_id  VARCHAR(20)  NOT NULL,
    customer_id     VARCHAR(20)  NOT NULL,
    gender          VARCHAR(10)  NOT NULL,
    age_group       VARCHAR(10)  NOT NULL,
    occupation      VARCHAR(20)  NOT NULL,
    city_category   CHAR(1)      NOT NULL,
    stay_in_current_city_years VARCHAR(2) NOT NULL,
    marital_status  VARCHAR(10)  NOT NULL,
    product_id      VARCHAR(20)  NOT NULL,
    product_name    VARCHAR(255) NOT NULL,
    product_category VARCHAR(20) NOT NULL,
    supplier_id     VARCHAR(10)  NOT NULL,
    supplier_name   VARCHAR(50)  NOT NULL,
    store_id        TINYINT UNSIGNED NOT NULL,
    event_time      DATETIME     NOT NULL,
    quantity        INT          NOT NULL,
    unit_price      DECIMAL(12,4) NOT NULL,
    revenue         DECIMAL(16,4) NOT NULL,
    sales_mode      VARCHAR(8)   NOT NULL,
    processing_mode VARCHAR(12)  NOT NULL,
    cached_time     DATETIME(3)  NOT NULL,
    PRIMARY KEY (cache_seq),
    UNIQUE KEY uq_cache_source (source_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
