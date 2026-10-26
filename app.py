"""Cloud Transaction Engine - simple base.

REST API for accounts and money movement with:
  * atomic transactions (SQLite BEGIN IMMEDIATE)
  * integer money (cents) - no float errors
  * idempotency keys - safe client retries
  * an append-only transaction log
Run:  uvicorn app:app --reload
"""
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB_PATH = os.getenv("CTE_DB", "engine.db")
app = FastAPI(title="Cloud Transaction Engine", version="0.1.0")


# ---------- database ----------
def connect():
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK (type IN ('deposit','withdraw','transfer')),
            from_account TEXT REFERENCES accounts(id),
            to_account TEXT REFERENCES accounts(id),
            amount INTEGER NOT NULL CHECK (amount > 0),
            note TEXT,
            idempotency_key TEXT UNIQUE,
            created_at TEXT NOT NULL
        );
        """)


@contextmanager
def atomic():
    """One all-or-nothing database transaction."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).isoformat()


@app.on_event("startup")
def startup():
    init_db()


# ---------- schemas ----------
class AccountIn(BaseModel):
    owner: str = Field(min_length=1)
    initial_balance: float = Field(default=0, ge=0)


class MoneyIn(BaseModel):
    account_id: str
    amount: float = Field(gt=0, description="Amount in currency units, e.g. 10.50")
    note: Optional[str] = None


class TransferIn(BaseModel):
    from_account: str
    to_account: str
    amount: float = Field(gt=0)
    note: Optional[str] = None


def cents(x: float) -> int:
    return round(x * 100)


def account_out(row):
    return {"id": row["id"], "owner": row["owner"],
            "balance": row["balance"] / 100, "created_at": row["created_at"]}


def txn_out(row):
    d = dict(row)
    d["amount"] = d["amount"] / 100
    d.pop("idempotency_key", None)
    return d


# ---------- helpers ----------
def get_account(conn, acct_id):
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (acct_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Account {acct_id} not found")
    return row


def existing_txn(conn, key):
    if not key:
        return None
    return conn.execute("SELECT * FROM transactions WHERE idempotency_key=?", (key,)).fetchone()


def record(conn, type_, frm, to, amount, note, key):
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
        (tid, type_, frm, to, amount, note, key, now()),
    )
    return conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()


# ---------- endpoints ----------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/accounts", status_code=201)
def create_account(body: AccountIn):
    acct_id = str(uuid.uuid4())
    with atomic() as c:
        c.execute("INSERT INTO accounts VALUES (?,?,?,?)",
                  (acct_id, body.owner, cents(body.initial_balance), now()))
        return account_out(get_account(c, acct_id))


@app.get("/accounts/{acct_id}")
def read_account(acct_id: str):
    with connect() as c:
        return account_out(get_account(c, acct_id))


@app.post("/transactions/deposit", status_code=201)
def deposit(body: MoneyIn, idempotency_key: Optional[str] = Header(None)):
    with atomic() as c:
        if (prev := existing_txn(c, idempotency_key)):
            return txn_out(prev)
        get_account(c, body.account_id)
        c.execute("UPDATE accounts SET balance=balance+? WHERE id=?",
                  (cents(body.amount), body.account_id))
        return txn_out(record(c, "deposit", None, body.account_id,
                              cents(body.amount), body.note, idempotency_key))


@app.post("/transactions/withdraw", status_code=201)
def withdraw(body: MoneyIn, idempotency_key: Optional[str] = Header(None)):
    with atomic() as c:
        if (prev := existing_txn(c, idempotency_key)):
            return txn_out(prev)
        acct = get_account(c, body.account_id)
        amt = cents(body.amount)
        if acct["balance"] < amt:
            raise HTTPException(409, "Insufficient funds")
        c.execute("UPDATE accounts SET balance=balance-? WHERE id=?", (amt, body.account_id))
        return txn_out(record(c, "withdraw", body.account_id, None, amt,
                              body.note, idempotency_key))


@app.post("/transactions/transfer", status_code=201)
def transfer(body: TransferIn, idempotency_key: Optional[str] = Header(None)):
    if body.from_account == body.to_account:
        raise HTTPException(400, "Cannot transfer to the same account")
    with atomic() as c:
        if (prev := existing_txn(c, idempotency_key)):
            return txn_out(prev)
        src = get_account(c, body.from_account)
        get_account(c, body.to_account)
        amt = cents(body.amount)
        if src["balance"] < amt:
            raise HTTPException(409, "Insufficient funds")
        c.execute("UPDATE accounts SET balance=balance-? WHERE id=?", (amt, body.from_account))
        c.execute("UPDATE accounts SET balance=balance+? WHERE id=?", (amt, body.to_account))
        return txn_out(record(c, "transfer", body.from_account, body.to_account,
                              amt, body.note, idempotency_key))


@app.get("/accounts/{acct_id}/transactions")
def account_history(acct_id: str, limit: int = 50):
    with connect() as c:
        get_account(c, acct_id)
        rows = c.execute(
            "SELECT * FROM transactions WHERE from_account=? OR to_account=? "
            "ORDER BY created_at DESC LIMIT ?", (acct_id, acct_id, limit)).fetchall()
        return [txn_out(r) for r in rows]
