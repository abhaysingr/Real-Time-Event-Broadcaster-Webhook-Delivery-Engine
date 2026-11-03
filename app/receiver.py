import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.webhook_signing import verify_signature


DB_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "receiver.db"
)

failures_remaining = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_receiver_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS received_events (
                idempotency_key TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS receiver_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                http_status INTEGER NOT NULL,
                duplicate INTEGER NOT NULL DEFAULT 0,
                attempted_at TEXT NOT NULL
            )
        """)

        await db.commit()


async def reset_receiver_db() -> None:
    """Clear receiver data for isolated automated tests."""
    global failures_remaining

    await init_receiver_db()

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM received_events")
        await db.execute("DELETE FROM receiver_attempts")
        await db.commit()

    failures_remaining = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_receiver_db()
    yield


app = FastAPI(
    title="Local Webhook Receiver",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "receiver-ready"}


@app.post("/test/fail-next/{count}")
async def fail_next_requests(count: int):
    global failures_remaining

    if count < 0 or count > 20:
        raise HTTPException(
            status_code=400,
            detail="Count must be between 0 and 20",
        )

    failures_remaining = count

    return {
        "message": "Failure simulation configured",
        "failures_remaining": failures_remaining,
    }




@app.post("/webhook")
async def receive_webhook(request: Request):
    global failures_remaining

    # Fail closed if the receiver has no configured secret.
    if not settings.webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Webhook receiver secret is not configured",
        )

    # Verify the exact bytes sent by the worker.
    raw_body = await request.body()
    signature_header = request.headers.get("X-Webhook-Signature", "")

    if not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid webhook signature",
        )

    signature = signature_header.removeprefix("sha256=")

    if not verify_signature(
        raw_body,
        settings.webhook_secret,
        signature,
    ):
        raise HTTPException(
            status_code=401,
            detail="Webhook signature verification failed",
        )

    # Parse JSON only after signature verification.
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON payload",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="JSON payload must be an object",
        )

    event_id = str(payload.get("event_id", "unknown"))
    idempotency_key = request.headers.get("Idempotency-Key")

    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Missing Idempotency-Key header",
        )

    async with aiosqlite.connect(str(DB_PATH)) as db:
        # Simulate a transient failure before processing the event.
        if failures_remaining > 0:
            failures_remaining -= 1

            await db.execute(
                """
                INSERT INTO receiver_attempts (
                    event_id, http_status, duplicate, attempted_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (event_id, 503, 0, utc_now()),
            )
            await db.commit()

            return JSONResponse(
                status_code=503,
                content={"error": "Temporary test failure"},
            )

        # The primary key prevents duplicate processing.
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO received_events (
                idempotency_key, event_id, payload, received_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                idempotency_key,
                event_id,
                json.dumps(payload, sort_keys=True),
                utc_now(),
            ),
        )

        is_duplicate = cursor.rowcount == 0

        await db.execute(
            """
            INSERT INTO receiver_attempts (
                event_id, http_status, duplicate, attempted_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                event_id,
                200,
                int(is_duplicate),
                utc_now(),
            ),
        )
        await db.commit()

    if is_duplicate:
        return {
            "status": "already_processed",
            "event_id": event_id,
            "duplicate": True,
        }

    return {
        "status": "received",
        "event_id": event_id,
        "duplicate": False,
    }




@app.get("/received")
async def get_received_events():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            """
            SELECT idempotency_key, event_id, payload, received_at
            FROM received_events
            ORDER BY received_at
            """
        )
        rows = await cursor.fetchall()

    events = [
        {
            "idempotency_key": row[0],
            "event_id": row[1],
            "payload": json.loads(row[2]),
            "received_at": row[3],
        }
        for row in rows
    ]

    return {"count": len(events), "events": events}


@app.get("/attempts")
async def get_delivery_attempts():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute(
            """
            SELECT event_id, http_status, duplicate, attempted_at
            FROM receiver_attempts
            ORDER BY id
            """
        )
        rows = await cursor.fetchall()

    attempts = [
        {
            "event_id": row[0],
            "status": row[1],
            "duplicate": bool(row[2]),
            "attempted_at": row[3],
        }
        for row in rows
    ]

    return {"count": len(attempts), "attempts": attempts}
