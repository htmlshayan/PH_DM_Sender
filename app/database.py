import sqlite3
import datetime
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = str(DATA_DIR / "stats.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stats
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  sent_date DATE, 
                  count INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY,
                  value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS message_templates
                 (key TEXT PRIMARY KEY,
                  value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS targets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  url TEXT UNIQUE NOT NULL,
                  sent INTEGER DEFAULT 0,
                  sent_at DATETIME)''')
    c.execute('''CREATE TABLE IF NOT EXISTS account_messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  account_id TEXT NOT NULL,
                  sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  target_url TEXT,
                  message_text TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS accounts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  cookies_json TEXT NOT NULL,
                  gologin_profile_id TEXT,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute("PRAGMA table_info(accounts)")
    existing_columns = [row[1] for row in c.fetchall()]
    if "gologin_profile_id" not in existing_columns:
        c.execute("ALTER TABLE accounts ADD COLUMN gologin_profile_id TEXT")
    c.execute("PRAGMA table_info(account_messages)")
    message_columns = [row[1] for row in c.fetchall()]
    if "target_url" not in message_columns:
        c.execute("ALTER TABLE account_messages ADD COLUMN target_url TEXT")
    if "message_text" not in message_columns:
        c.execute("ALTER TABLE account_messages ADD COLUMN message_text TEXT")

    c.execute("SELECT value FROM settings WHERE key = ?", ("SPINTAX_MESSAGE",))
    spintax_row = c.fetchone()
    if spintax_row:
        c.execute(
            "INSERT OR REPLACE INTO message_templates (key, value) VALUES (?, ?)",
            ("SPINTAX_MESSAGE", spintax_row[0]),
        )
        c.execute("DELETE FROM settings WHERE key = ?", ("SPINTAX_MESSAGE",))
    conn.commit()
    conn.close()


def set_setting(key, value):
    if not key:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    if not key:
        return default
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else default


def set_message_template(key, value):
    if not key:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO message_templates (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_message_template(key, default=""):
    if not key:
        return default
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM message_templates WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else default


def get_target_counts():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT COUNT(*)
        FROM targets
        WHERE sent = 0
          AND LOWER(url) NOT IN (
            SELECT LOWER(target_url)
            FROM account_messages
            WHERE target_url IS NOT NULL AND target_url != ''
          )
        """
    )
    pending_row = c.fetchone()
    pending = pending_row[0] if pending_row and pending_row[0] else 0

    c.execute(
        """
        SELECT COUNT(DISTINCT LOWER(target_url))
        FROM account_messages
        WHERE target_url IS NOT NULL AND target_url != ''
        """
    )
    sent_row = c.fetchone()
    sent = sent_row[0] if sent_row and sent_row[0] else 0
    conn.close()
    total = pending + sent
    return total, pending


def get_targets():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM targets WHERE sent = 0 ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_targets_split():
    pending = get_targets()
    sent = get_sent_targets()
    sent_set = {s.lower() for s in sent}
    pending = [url for url in pending if url.lower() not in sent_set]
    return pending, sent


def get_sent_targets():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT target_url, MAX(sent_at) AS last_sent
        FROM account_messages
        WHERE target_url IS NOT NULL AND target_url != ''
        GROUP BY target_url
        ORDER BY last_sent DESC
        """
    )
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_sent_targets_with_accounts():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT am.target_url, a.name
        FROM account_messages am
        JOIN (
            SELECT target_url, MAX(sent_at) AS last_sent
            FROM account_messages
            WHERE target_url IS NOT NULL AND target_url != ''
            GROUP BY target_url
        ) latest ON latest.target_url = am.target_url AND latest.last_sent = am.sent_at
        LEFT JOIN accounts a ON a.gologin_profile_id = am.account_id
        WHERE am.target_url IS NOT NULL AND am.target_url != ''
        ORDER BY latest.last_sent DESC
        """
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"url": row[0], "account_name": row[1] or ""}
        for row in rows
    ]


def save_targets(submitted_urls):
    cleaned = []
    seen = set()
    for url in submitted_urls:
        normalized = (url or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT DISTINCT target_url FROM account_messages WHERE target_url IS NOT NULL AND target_url != ''"
    )
    sent_urls = {((row[0] or "").strip()).lower() for row in c.fetchall()}

    if cleaned:
        placeholders = ",".join(["?"] * len(cleaned))
        c.execute(
            f"DELETE FROM targets WHERE sent = 0 AND url NOT IN ({placeholders})",
            tuple(cleaned),
        )
    else:
        c.execute("DELETE FROM targets WHERE sent = 0")

    if sent_urls:
        placeholders = ",".join(["?"] * len(sent_urls))
        c.execute(
            f"DELETE FROM targets WHERE sent = 0 AND LOWER(url) IN ({placeholders})",
            tuple(sent_urls),
        )

    for url in cleaned:
        if url.lower() in sent_urls:
            continue
        c.execute("INSERT OR IGNORE INTO targets (url, sent) VALUES (?, 0)", (url,))
        c.execute("UPDATE targets SET sent = 0, sent_at = NULL WHERE url = ?", (url,))

    conn.commit()
    conn.close()


def mark_target_sent(url):
    normalized = (url or "").strip()
    if not normalized:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM targets WHERE LOWER(url) = LOWER(?)", (normalized,))
    conn.commit()
    conn.close()

def log_message_sent():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    
    c.execute("SELECT count FROM stats WHERE sent_date = ?", (today,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE stats SET count = count + 1 WHERE sent_date = ?", (today,))
    else:
        c.execute("INSERT INTO stats (sent_date, count) VALUES (?, 1)", (today,))
    
    conn.commit()
    conn.close()


def log_account_message(account_id, target_url=None, message_text=None):
    if not account_id:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO account_messages (account_id, sent_at, target_url, message_text) "
        "VALUES (?, CURRENT_TIMESTAMP, ?, ?)",
        (account_id, target_url, message_text),
    )
    conn.commit()
    conn.close()


def get_account_message_count_last_24h(account_id):
    if not account_id:
        return 0
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM account_messages WHERE account_id = ? AND sent_at >= datetime('now', '-24 hours')",
        (account_id,),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.date.today().isoformat()
    
    c.execute("SELECT count FROM stats WHERE sent_date = ?", (today,))
    today_row = c.fetchone()
    today_count = today_row[0] if today_row else 0
    
    c.execute("SELECT SUM(count) FROM stats")
    total_row = c.fetchone()
    total_count = total_row[0] if total_row and total_row[0] else 0
    
    conn.close()
    return {"today": today_count, "total": total_count}


def get_accounts():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, cookies_json, gologin_profile_id FROM accounts ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return [
        {"id": row[0], "name": row[1], "cookies_json": row[2], "gologin_profile_id": row[3]}
        for row in rows
    ]


def get_account_by_id(account_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT id, name, cookies_json, gologin_profile_id FROM accounts WHERE id = ?",
        (account_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "cookies_json": row[2], "gologin_profile_id": row[3]}


def add_account(name, cookies_json, gologin_profile_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO accounts (name, cookies_json, gologin_profile_id) VALUES (?, ?, ?)",
        (name, cookies_json, gologin_profile_id),
    )
    conn.commit()
    conn.close()


def update_account(account_id, name, cookies_json, gologin_profile_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "UPDATE accounts SET name = ?, cookies_json = ?, gologin_profile_id = ? WHERE id = ?",
        (name, cookies_json, gologin_profile_id, account_id),
    )
    conn.commit()
    conn.close()


def update_account_cookies_by_profile_id(profile_id, cookies_json):
    if not profile_id:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "UPDATE accounts SET cookies_json = ? WHERE gologin_profile_id = ?",
        (cookies_json, profile_id),
    )
    conn.commit()
    conn.close()


def delete_account(account_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

init_db()