
from contextlib import asynccontextmanager
from uuid import uuid4

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from app.config import settings
from app.delivery_store import (
    create_delivery,
    get_attempts,
    get_delivery,
    init_db,
    update_delivery,
)
from app.events import EventRequest, build_event
from app.webhook_security import (
    UnsafeWebhookURLError,
    validate_webhook_url,
)
from app.websocket_manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    redis_pool = await create_pool(
        RedisSettings.from_dsn(settings.redis_url)
    )
    app.state.redis_pool = redis_pool

    try:
        yield
    finally:
        await redis_pool.aclose()


app = FastAPI(
    title="Real-Time Event Broadcaster & Webhook Engine",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/events", status_code=202)
async def publish_event(request: EventRequest):
    event = build_event(request)
    delivery_id = None

    if request.webhook_url is not None:
        webhook_url = str(request.webhook_url)

        # Validate the destination before persisting or queueing.
        try:
            validate_webhook_url(
                webhook_url,
                allow_local=settings.allow_local_webhooks,
            )
        except UnsafeWebhookURLError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error

        delivery_id = str(uuid4())

        # Persist the delivery before enqueueing it.
        await create_delivery(
            delivery_id=delivery_id,
            event_id=event["event_id"],
            webhook_url=webhook_url,
        )

        try:
            job = await app.state.redis_pool.enqueue_job(
                "deliver_webhook",
                delivery_id,
                webhook_url,
                event,
                _job_id=delivery_id,
            )

            if job is None:
                await update_delivery(
                    delivery_id,
                    status="enqueue_failed",
                    error="JobNotEnqueued",
                )
                raise HTTPException(
                    status_code=503,
                    detail="Delivery job was not enqueued",
                )

        except HTTPException:
            raise
        except Exception as error:
            await update_delivery(
                delivery_id,
                status="enqueue_failed",
                error=type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Unable to enqueue webhook delivery",
            ) from error

    # Broadcast even when no webhook URL was supplied.
    await manager.broadcast(event)

    return {
        "message": "Event accepted",
        "event": event,
        "delivery_queued": delivery_id is not None,
        "delivery_id": delivery_id,
    }


@app.get("/deliveries/{delivery_id}")
async def delivery_status(delivery_id: str):
    delivery = await get_delivery(delivery_id)

    if delivery is None:
        raise HTTPException(
            status_code=404,
            detail="Delivery not found",
        )

    return {
        "delivery": delivery,
        "attempts": await get_attempts(delivery_id),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)
        raise
