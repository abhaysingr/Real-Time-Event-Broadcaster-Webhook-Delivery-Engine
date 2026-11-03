
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


DB_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "deliveries.db"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()





def get_connection() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return aiosqlite.connect(str(DB_PATH))




async def init_db() -> None:
    async with get_connection() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_http_status INTEGER,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                delivered_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS delivery_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                http_status INTEGER,
                error TEXT,
                attempted_at TEXT NOT NULL,
                FOREIGN KEY (delivery_id)
                    REFERENCES deliveries(delivery_id)
            )
        """)

        await db.commit()


async def create_delivery(
    delivery_id: str,
    event_id: str,
    webhook_url: str,
) -> None:
    now = utc_now()

    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO deliveries (
                delivery_id, event_id, webhook_url, status,
                attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, 'queued', 0, ?, ?)
            """,
            (delivery_id, event_id, webhook_url, now, now),
        )
        await db.commit()


async def update_delivery(
    delivery_id: str,
    status: str,
    attempts: int | None = None,
    http_status: int | None = None,
    error: str | None = None,
) -> None:
    now = utc_now()
    delivered_at = now if status == "delivered" else None

    async with get_connection() as db:
        await db.execute(
            """
            UPDATE deliveries
            SET status = ?,
                attempts = COALESCE(?, attempts),
                last_http_status = ?,
                last_error = ?,
                updated_at = ?,
                delivered_at = COALESCE(?, delivered_at)
            WHERE delivery_id = ?
            """,
            (
                status,
                attempts,
                http_status,
                error,
                now,
                delivered_at,
                delivery_id,
            ),
        )
        await db.commit()


async def record_attempt(
    delivery_id: str,
    attempt_number: int,
    http_status: int | None,
    error: str | None,
) -> None:
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO delivery_attempts (
                delivery_id, attempt_number, http_status,
                error, attempted_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                delivery_id,
                attempt_number,
                http_status,
                error,
                utc_now(),
            ),
        )
        await db.commit()



async def get_delivery(delivery_id: str) -> dict | None:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM deliveries WHERE delivery_id = ?",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None




async def get_attempts(delivery_id: str) -> list[dict]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT attempt_number, http_status, error, attempted_at
            FROM delivery_attempts
            WHERE delivery_id = ?
            ORDER BY attempt_number
            """,
            (delivery_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

