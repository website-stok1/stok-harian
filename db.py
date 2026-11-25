from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import os
import sqlite3
from zoneinfo import ZoneInfo


JAKARTA = ZoneInfo("Asia/Jakarta")
DB_PATH = Path(os.environ.get("STOK_DB_PATH", Path(__file__).parent / "data/stok.sqlite3"))
FEE_SCALE = 10_000
FEE_COLUMN = f"INTEGER NOT NULL DEFAULT 0 CHECK (fee_bps BETWEEN 0 AND {FEE_SCALE})"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    contact TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    minimum INTEGER NOT NULL CHECK (minimum >= 0),
    cost INTEGER NOT NULL CHECK (cost >= 0),
    description TEXT NOT NULL DEFAULT '',
    supplier_id INTEGER REFERENCES suppliers(id),
    fee_bps {FEE_COLUMN},
    photo BLOB
);
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    movement_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('in', 'out')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    supplier_id INTEGER REFERENCES suppliers(id),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS movements_by_product ON movements(product_id, movement_date, id);
CREATE INDEX IF NOT EXISTS movements_by_date ON movements(movement_date);
"""

PRODUCTS = """
SELECT p.id, p.sku, p.name, p.category, p.unit, p.minimum, p.cost, p.description,
       p.supplier_id, p.fee_bps, p.photo IS NOT NULL AS has_photo,
       s.name AS supplier_name, COALESCE(m.stock, 0) AS stock
FROM products p
LEFT JOIN suppliers s ON s.id = p.supplier_id
LEFT JOIN (
    SELECT product_id, SUM(CASE kind WHEN 'in' THEN quantity ELSE -quantity END) AS stock
    FROM movements
    GROUP BY product_id
) m ON m.product_id = p.id
ORDER BY p.name COLLATE NOCASE, p.sku
"""

MOVEMENTS = """
SELECT m.*, p.sku, p.name AS product_name, p.unit, p.category, s.name AS supplier_name
FROM movements m
JOIN products p ON p.id = m.product_id
LEFT JOIN suppliers s ON s.id = m.supplier_id
"""


class StockError(ValueError):
    pass


@contextmanager
def connect(*, write=False):
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(SCHEMA)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(products)")}
    if {"fee_bps", "photo"}.issubset(columns):
        return
    with connect(write=True) as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(products)")}
        if "fee_bps" not in columns:
            connection.execute(f"ALTER TABLE products ADD COLUMN fee_bps {FEE_COLUMN}")
        if "photo" not in columns:
            connection.execute("ALTER TABLE products ADD COLUMN photo BLOB")


def today():
    return datetime.now(JAKARTA).date()


def all_products():
    with connect() as connection:
        return [dict(row) for row in connection.execute(PRODUCTS)]


def product_photo(product_id):
    return product_photos([product_id]).get(product_id)


def product_photos(product_ids):
    if not product_ids:
        return {}
    photos = {}
    with connect() as connection:
        for offset in range(0, len(product_ids), 500):
            batch = product_ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            matches = connection.execute(
                f"SELECT id, photo FROM products WHERE photo IS NOT NULL AND id IN ({placeholders})",
                batch,
            )
            photos.update((row["id"], row["photo"]) for row in matches)
    return photos


def all_suppliers():
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT s.*, COUNT(p.id) AS product_count
            FROM suppliers s LEFT JOIN products p ON p.supplier_id = s.id
            GROUP BY s.id
            ORDER BY s.name COLLATE NOCASE
            """
        )
        return [dict(row) for row in rows]


def save_supplier(supplier_id, name, contact, phone, email, address):
    if not name.strip():
        raise StockError("Nama supplier wajib diisi.")
    values = (name.strip(), contact.strip(), phone.strip(), email.strip(), address.strip())
    try:
        with connect(write=True) as connection:
            if supplier_id is None:
                connection.execute(
                    "INSERT INTO suppliers (name, contact, phone, email, address) VALUES (?, ?, ?, ?, ?)",
                    values,
                )
            else:
                connection.execute(
                    "UPDATE suppliers SET name=?, contact=?, phone=?, email=?, address=? WHERE id=?",
                    (*values, supplier_id),
                )
    except sqlite3.IntegrityError as error:
        raise StockError("Nama supplier sudah dipakai.") from error


def save_product(product_id, sku, name, category, unit, supplier_id, minimum, cost, description,
                 *, fee_bps=0, photo=None, remove_photo=False):
    if not all(value.strip() for value in (sku, name, category, unit)):
        raise StockError("SKU, nama barang, kategori, dan satuan wajib diisi.")
    if not isinstance(fee_bps, int) or not 0 <= fee_bps <= FEE_SCALE:
        raise StockError("Fee harus antara 0% dan 100%.")
    if photo is not None and remove_photo:
        raise StockError("Untuk mengganti foto, unggah foto baru tanpa mencentang Hapus foto tersimpan.")
    values = (sku.strip().upper(), name.strip(), category.strip(), unit.strip(),
              supplier_id, minimum, cost, description.strip(), fee_bps)
    try:
        with connect(write=True) as connection:
            if product_id is None:
                connection.execute(
                    """INSERT INTO products
                    (sku, name, category, unit, supplier_id, minimum, cost, description, fee_bps, photo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*values, photo),
                )
            else:
                current = connection.execute("SELECT unit FROM products WHERE id=?", (product_id,)).fetchone()
                if current["unit"] != values[3] and connection.execute(
                    "SELECT 1 FROM movements WHERE product_id=? LIMIT 1", (product_id,)
                ).fetchone():
                    raise StockError("Satuan barang yang memiliki transaksi sudah terkunci.")
                connection.execute(
                    """UPDATE products SET sku=?, name=?, category=?, unit=?, supplier_id=?,
                    minimum=?, cost=?, description=?, fee_bps=?,
                    photo=CASE WHEN ? THEN ? ELSE photo END WHERE id=?""",
                    (*values, remove_photo or photo is not None, photo, product_id),
                )
    except sqlite3.IntegrityError as error:
        if "products.sku" in str(error):
            raise StockError("SKU sudah dipakai oleh barang lain.") from error
        raise StockError("Periksa supplier, stok minimum, dan harga modal.") from error


def transactions(*, start=None, end=None, product_id=None, limit=None):
    clauses, params = [], []
    for value, condition in (
        (start, "m.movement_date >= ?"),
        (end, "m.movement_date <= ?"),
        (product_id, "m.product_id = ?"),
    ):
        if value is not None:
            clauses.append(condition)
            params.append(value)
    query = MOVEMENTS
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY m.movement_date DESC, m.id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with connect() as connection:
        return [dict(row) for row in connection.execute(query, params)]


def record_movement(product_id, kind, quantity, movement_date, supplier_id, note):
    if kind not in ("in", "out") or not isinstance(quantity, int) or quantity < 1:
        raise StockError("Jenis transaksi atau jumlah barang tidak valid.")
    if movement_date > today():
        raise StockError("Tanggal transaksi tidak boleh melewati hari ini.")
    if kind == "out":
        supplier_id = None

    with connect(write=True) as connection:
        connection.execute(
            """INSERT INTO movements
            (product_id, movement_date, kind, quantity, supplier_id, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (product_id, movement_date.isoformat(), kind, quantity, supplier_id,
             note.strip(), datetime.now(JAKARTA).isoformat(timespec="seconds")),
        )
        if kind == "out":
            first_negative = connection.execute(
                """
                SELECT movement_date FROM (
                    SELECT movement_date, id, SUM(CASE kind WHEN 'in' THEN quantity ELSE -quantity END)
                    OVER (ORDER BY movement_date, id ROWS UNBOUNDED PRECEDING) AS balance
                    FROM movements WHERE product_id=?
                )
                WHERE balance < 0 ORDER BY movement_date, id LIMIT 1
                """,
                (product_id,),
            ).fetchone()
            if first_negative:
                date_label = datetime.fromisoformat(first_negative["movement_date"]).strftime("%d/%m/%Y")
                raise StockError(f"Stok tidak cukup pada {date_label}. Periksa jumlah atau tanggal transaksi.")
