from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
CREATE TABLE IF NOT EXISTS lots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
  expiry TEXT, status TEXT, data_quality TEXT, parent_id INT
);
CREATE TABLE IF NOT EXISTS consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

# Columns added after the first release; applied lazily to existing databases.
MIGRATIONS = [
    ("lots", "parent_id", "ALTER TABLE lots ADD COLUMN parent_id INT"),
]


def migrate(c):
    for table, column, ddl in MIGRATIONS:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            c.execute(ddl)


def init_db():
    c = connect()
    c.executescript(SCHEMA)
    migrate(c)
    if c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
        c.executemany("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", [
            ("牛奶", "upper", "盒"), ("鸡蛋", "mid", "个"), ("冻饺", "lower", "袋"),
        ])
        c.executemany(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,parent_id) VALUES (?,?,?,?,?,?,?)",
            [
                (1, 2, 2, "2026-10-01", "on_shelf", "clean", None),
                (1, 1, 1, "2026-09-28", "on_shelf", "clean", None),
                (2, 12, 12, "2026-11-01", "on_shelf", "clean", None),
                (3, 1, 1, "2025-01-01", "on_shelf", "dirty", None),
                (2, -3, -3, "2026-12-01", "on_shelf", "dirty", None),
            ],
        )
        c.execute("INSERT INTO settings(key,value) VALUES ('warn_days','3')")
        c.commit()
    c.close()
