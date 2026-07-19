import sqlite3
import os
from crypto_vault import CryptoVault

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxos_prod.db")
vault = CryptoVault()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,       -- Encrypted
            pan TEXT NOT NULL,        -- Encrypted
            email TEXT NOT NULL,      -- Encrypted
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. Clients table (Freelancers have multiple clients)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,       -- Encrypted
            gstin TEXT,               -- Encrypted
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # 3. Financial Years
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS financial_years (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT UNIQUE NOT NULL, -- e.g. "FY 2026-27"
            is_active INTEGER DEFAULT 0
        )
    """)
    
    # 4. Invoices (Immutable with Supersedes pointer and smart contract states)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            client_id INTEGER NOT NULL,
            financial_year_id INTEGER NOT NULL,
            vendor TEXT NOT NULL,               -- Encrypted
            amount REAL NOT NULL,               -- Numeric (unencrypted for fast SQL aggregate stats)
            date TEXT NOT NULL,                 -- Encrypted
            is_anomaly INTEGER DEFAULT 0,
            supersedes_invoice_id INTEGER,      -- Pointer to corrected invoice
            is_active INTEGER DEFAULT 1,        -- 0 if superseded by a newer version
            payment_state TEXT DEFAULT 'ISSUED',-- DRAFT, ISSUED, PAID, OVERDUE, ESCALATED, NOTICE_GENERATED
            payment_date TEXT,                  -- Date payment was received (NULL = unpaid)
            interest_charged REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (client_id) REFERENCES clients (id),
            FOREIGN KEY (financial_year_id) REFERENCES financial_years (id),
            FOREIGN KEY (supersedes_invoice_id) REFERENCES invoices (id)
        )
    """)
    try:
        cursor.execute("ALTER TABLE invoices ADD COLUMN payment_state TEXT DEFAULT 'ISSUED'")
        cursor.execute("ALTER TABLE invoices ADD COLUMN interest_charged REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE invoices ADD COLUMN payment_date TEXT")
    except sqlite3.OperationalError:
        pass
    
    # 5. Approvals Ledger (Tamper-evident hash chain linked per user)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            previous_hash TEXT NOT NULL,
            event_data TEXT NOT NULL,           -- Encrypted
            current_hash TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # 6. Paired Devices (Zero-trust registry)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            device_token TEXT UNIQUE NOT NULL,
            public_key TEXT,
            device_password TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("ALTER TABLE devices ADD COLUMN device_password TEXT")
    except sqlite3.OperationalError:
        pass

    # 7. Merkle Roots Chain Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS merkle_roots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_hash TEXT NOT NULL,
            signature TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 8. AIS Entries Table (Government Mirror Reconciliation)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ais_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            amount REAL NOT NULL,
            tds_deducted REAL DEFAULT 0.0,
            section TEXT DEFAULT 'TDS',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 9. Purchases Table (GST Complete Suite - ITC Tracking)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            vendor_name TEXT NOT NULL,
            vendor_gstin TEXT,
            taxable_amount REAL NOT NULL,
            cgst REAL DEFAULT 0.0,
            sgst REAL DEFAULT 0.0,
            igst REAL DEFAULT 0.0,
            date TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # 10. GSTR-2B Entries (ITC Auto-Matching)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gstr2b_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_gstin TEXT NOT NULL,
            taxable_amount REAL NOT NULL,
            cgst REAL DEFAULT 0.0,
            sgst REAL DEFAULT 0.0,
            igst REAL DEFAULT 0.0,
            invoice_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 11. TDS Records (TDS Complete Suite)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tds_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            section TEXT NOT NULL,
            rate REAL NOT NULL,
            certificate_status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)
    
    # 12. Cross Border Transactions (Transfer Pricing Suite)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cross_border_tx (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            nature_of_tx TEXT NOT NULL,
            amount REAL NOT NULL,
            country_code TEXT NOT NULL,
            arms_length_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
    """)

    conn.commit()
    
    # Seed default user, client, financial year, and genesis block if database is empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        # Seed default user: Devashish Sharma
        enc_name = vault.encrypt("Devashish Sharma")
        enc_pan = vault.encrypt("ABCDE1234F")
        enc_email = vault.encrypt("devashish@edge.taxos")
        cursor.execute("INSERT INTO users (name, pan, email) VALUES (?, ?, ?)", (enc_name, enc_pan, enc_email))
        user_id = cursor.lastrowid
        
        # Seed default client: Acme Global Inc
        enc_client = vault.encrypt("Acme Global Inc")
        enc_gstin = vault.encrypt("99AAABB1234C1Z0")
        cursor.execute("INSERT INTO clients (user_id, name, gstin) VALUES (?, ?, ?)", (user_id, enc_client, enc_gstin))
        client_id = cursor.lastrowid
        
        # Seed financial years
        cursor.execute("INSERT INTO financial_years (label, is_active) VALUES (?, ?)", ("FY 2025-26", 0))
        cursor.execute("INSERT INTO financial_years (label, is_active) VALUES (?, ?)", ("FY 2026-27", 1))
        fy_id = cursor.lastrowid
        
        # Seed genesis block for default user
        cursor.execute("""
            INSERT INTO approvals (user_id, previous_hash, event_data, current_hash)
            VALUES (?, ?, ?, ?)
        """, (user_id, "0" * 64, vault.encrypt("GENESIS"), "GENESIS_HASH_PLACEHOLDER"))
        
    conn.commit()
    conn.close()

# Users helper
def get_primary_user_id() -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 1

def get_active_fy_id() -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM financial_years WHERE is_active = 1 LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 1

# Invoices
def add_invoice(user_id: int, client_id: int, fy_id: int, vendor: str, amount: float, date: str, is_anomaly: bool = False, supersedes_id: int = None) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # If this invoice supersedes a previous one, mark the previous as inactive
    if supersedes_id is not None:
        cursor.execute("UPDATE invoices SET is_active = 0 WHERE id = ?", (supersedes_id,))
        
    enc_vendor = vault.encrypt(vendor)
    enc_date = vault.encrypt(date)
    
    cursor.execute("""
        INSERT INTO invoices (user_id, client_id, financial_year_id, vendor, amount, date, is_anomaly, supersedes_invoice_id, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (user_id, client_id, fy_id, enc_vendor, amount, enc_date, 1 if is_anomaly else 0, supersedes_id))
    
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_invoices(include_inactive: bool = False):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if include_inactive:
        cursor.execute("SELECT id, user_id, client_id, financial_year_id, vendor, amount, date, is_anomaly, supersedes_invoice_id, is_active, payment_state, payment_date, interest_charged, created_at FROM invoices ORDER BY id DESC")
    else:
        cursor.execute("SELECT id, user_id, client_id, financial_year_id, vendor, amount, date, is_anomaly, supersedes_invoice_id, is_active, payment_state, payment_date, interest_charged, created_at FROM invoices WHERE is_active = 1 ORDER BY id DESC")
        
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        d = dict(row)
        # Transparent decryption
        d["vendor"] = vault.decrypt(d["vendor"])
        d["date"] = vault.decrypt(d["date"])
        results.append(d)
    return results

def update_invoice_payment_state(invoice_id: int, state: str, interest: float = 0.0):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE invoices SET payment_state = ?, interest_charged = ? WHERE id = ?", (state, interest, invoice_id))
    conn.commit()
    conn.close()

def mark_invoice_paid(invoice_id: int, payment_date: str):
    """Marks an invoice as PAID with the date payment was received."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE invoices SET payment_state = 'PAID', payment_date = ? WHERE id = ?", (payment_date, invoice_id))
    conn.commit()
    conn.close()

# AIS Entries (Government Mirror)
def add_ais_entry(source_name: str, amount: float, tds_deducted: float = 0.0, section: str = "TDS") -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO ais_entries (source_name, amount, tds_deducted, section) VALUES (?, ?, ?, ?)",
                   (source_name, amount, tds_deducted, section))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_ais_entries() -> list:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, source_name, amount, tds_deducted, section, created_at FROM ais_entries ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def clear_ais_entries():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ais_entries")
    conn.commit()
    conn.close()

# Approvals / Attestation chain
def get_latest_approval(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, previous_hash, event_data, current_hash, timestamp FROM approvals WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["event_data"] = vault.decrypt(d["event_data"])
        return d
    return None

def add_approval(user_id: int, previous_hash: str, event_data: str, current_hash: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    enc_event = vault.encrypt(event_data)
    cursor.execute("""
        INSERT INTO approvals (user_id, previous_hash, event_data, current_hash)
        VALUES (?, ?, ?, ?)
    """, (user_id, previous_hash, enc_event, current_hash))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_approvals(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, previous_hash, event_data, current_hash, timestamp FROM approvals WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        d = dict(row)
        d["event_data"] = vault.decrypt(d["event_data"])
        results.append(d)
    return results

# Zero-trust Paired Devices
def add_device(device_name: str, device_token: str, public_key: str = None, device_password: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO devices (device_name, device_token, public_key, device_password) VALUES (?, ?, ?, ?)", (device_name, device_token, public_key, device_password))
    conn.commit()
    conn.close()

def verify_device_password(device_token: str, password_attempt: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT device_password FROM devices WHERE device_token = ?", (device_token,))
    row = cursor.fetchone()
    conn.close()
    if row:
        if row[0]:
            return row[0] == password_attempt
        return password_attempt.lower() == "taxos"
    return None

def get_device_public_key(device_token: str) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT public_key FROM devices WHERE device_token = ?", (device_token,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# Merkle Roots Helpers
def add_merkle_root(root_hash: str, signature: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO merkle_roots (root_hash, signature) VALUES (?, ?)", (root_hash, signature))
    conn.commit()
    conn.close()

def get_latest_merkle_root() -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, root_hash, signature, timestamp FROM merkle_roots ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def verify_device_token(device_token: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM devices WHERE device_token = ?", (device_token,))
    exists = cursor.fetchone()[0] > 0
    conn.close()
    return exists

def get_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, device_name, created_at FROM devices ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Administration / Reset
def clear_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS users")
    cursor.execute("DROP TABLE IF EXISTS clients")
    cursor.execute("DROP TABLE IF EXISTS financial_years")
    cursor.execute("DROP TABLE IF EXISTS invoices")
    cursor.execute("DROP TABLE IF EXISTS approvals")
    cursor.execute("DROP TABLE IF EXISTS devices")
    cursor.execute("DROP TABLE IF EXISTS merkle_roots")
    cursor.execute("DROP TABLE IF EXISTS ais_entries")
    cursor.execute("DROP TABLE IF EXISTS purchases")
    cursor.execute("DROP TABLE IF EXISTS gstr2b_entries")
    cursor.execute("DROP TABLE IF EXISTS tds_records")
    cursor.execute("DROP TABLE IF EXISTS cross_border_tx")
    conn.commit()
    conn.close()
    init_db()

# Purchases (GST Suite)
def add_purchase(user_id: int, vendor_name: str, vendor_gstin: str, taxable_amount: float, cgst: float, sgst: float, igst: float, date: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO purchases (user_id, vendor_name, vendor_gstin, taxable_amount, cgst, sgst, igst, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, vault.encrypt(vendor_name), vault.encrypt(vendor_gstin) if vendor_gstin else None, taxable_amount, cgst, sgst, igst, vault.encrypt(date)))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_purchases() -> list:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM purchases WHERE is_active = 1 ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        d = dict(row)
        d["vendor_name"] = vault.decrypt(d["vendor_name"])
        if d["vendor_gstin"]:
            d["vendor_gstin"] = vault.decrypt(d["vendor_gstin"])
        d["date"] = vault.decrypt(d["date"])
        results.append(d)
    return results

# GSTR-2B (ITC Matcher)
def add_gstr2b_entry(vendor_gstin: str, taxable_amount: float, cgst: float, sgst: float, igst: float, invoice_date: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO gstr2b_entries (vendor_gstin, taxable_amount, cgst, sgst, igst, invoice_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (vendor_gstin, taxable_amount, cgst, sgst, igst, invoice_date))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_gstr2b_entries() -> list:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM gstr2b_entries ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# TDS Records (TDS Suite)
def add_tds_record(client_id: int, amount: float, section: str, rate: float) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tds_records (client_id, amount, section, rate) VALUES (?, ?, ?, ?)", (client_id, amount, section, rate))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_tds_records() -> list:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tds_records ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_tds_status(record_id: int, status: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tds_records SET certificate_status = ? WHERE id = ?", (status, record_id))
    conn.commit()
    conn.close()

# Cross Border TX (Transfer Pricing Suite)
def add_cross_border_tx(client_id: int, nature_of_tx: str, amount: float, country_code: str, arms_length_price: float = None) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cross_border_tx (client_id, nature_of_tx, amount, country_code, arms_length_price)
        VALUES (?, ?, ?, ?, ?)
    """, (client_id, nature_of_tx, amount, country_code, arms_length_price))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_cross_border_txs() -> list:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cross_border_tx ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
