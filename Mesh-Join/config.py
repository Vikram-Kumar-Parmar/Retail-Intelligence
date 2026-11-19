"""Project constants (no secrets here) and run-time settings."""
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "Data"
CREATE_DW_SQL = PROJECT_ROOT / "Create-DW" / "create_dw.sql"

# ---- Fixed settings required by the specification (Section 12) -------------
PAGE_SIZE = 500            # Master Data partition (disk buffer) size
CHUNK_W = 500              # stream chunk w taken into H on each iteration
STREAM_BUFFER_LIMIT = 1000
N_GROUPS = 48              # periodic groups
DEFAULT_PEAK_RATE = 80.0   # transactions / second
DEFAULT_NONPEAK_RATE = 10.0
DEFAULT_HOUR_SECONDS = 60.0

# ---- Stores (Section 5.4.2) ------------------------------------------------
STORES = [
    (1, "Online", "Web"),
    (2, "London Bridge", "London"),
    (3, "Manchester Arndale", "Manchester"),
    (4, "Birmingham Bullring", "Birmingham"),
    (5, "Edinburgh Princes", "Edinburgh"),
]
STORE_BY_ID = {sid: (name, city) for sid, name, city in STORES}

# ---- Attribute value lists (Section 5.4.3), index 0 = first value ----------
GENDERS = ["Male", "Female"]
AGE_GROUPS = ["0-17", "18-25", "26-35", "36-45", "46-50", "51-55", "55+"]
OCCUPATIONS = ["Student", "Engineer", "Teacher", "Executive", "Clerk",
               "Healthcare", "Retail", "Homemaker", "Artist", "Other"]
CITY_CATEGORIES = ["A", "B", "C"]
STAY_YEARS = ["0", "1", "2", "3", "4+"]
MARITAL = ["Single", "Married"]
CATEGORIES = ["Home Decor", "Kitchen", "Stationery", "Toys", "Seasonal Gifts",
              "Bags", "Jewelry", "Garden", "Party", "Other"]
SUPPLIERS = [("SUP01", "Northwind Supplies"), ("SUP02", "Atlantic Gifts Ltd"),
             ("SUP03", "Midlands Wholesale"), ("SUP04", "Celtic Craft Co"),
             ("SUP05", "EuroPack Partners")]


@dataclass
class Settings:
    peak_rate: float = DEFAULT_PEAK_RATE
    nonpeak_rate: float = DEFAULT_NONPEAK_RATE
    hour_seconds: float = DEFAULT_HOUR_SECONDS
    start_speed: str = "peak"        # peak | non-peak
    start_mode: str = "both"         # online | periodic | both
    bonus_cache: bool = True         # Section 16: cache Live results first
    exit_when_done: bool = False     # automatic shutdown (used for batch/test runs)
    resume: bool = False             # skip preparation, continue an interrupted run
    online_file: str = str(DATA_DIR / "online_sales_stream.csv")
    raw_file: str = str(DATA_DIR / "Raw" / "Online Retail.xlsx")
    download_date: str = ""
