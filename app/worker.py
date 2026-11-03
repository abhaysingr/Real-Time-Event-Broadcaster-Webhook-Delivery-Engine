
import json
import logging
from app.webhook_security import validate_webhook_url
from typing import Any

import httpx
from arq.connections import RedisSettings
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.delivery_store import record_attempt, update_delivery
from app.webhook_signing import generate_signature

logger = logging.getLogger(__name__)


def is_retryable_error(error: BaseException) -> bool:
    if isinstance(
        error,
        (httpx.TimeoutException, httpx.NetworkError),
    ):
        return True

    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status in (408, 425, 429) or status >= 500

    return False


async def startup(ctx: dict[str, Any]) -> None:
    ctx["http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.webhook_timeout_seconds),
        limits=httpx.Limits(
            max_connections=settings.webhook_max_concurrency,
            max_keepalive_connections=settings.webhook_max_concurrency,
        ),
        follow_redirects=False,
    )
    logger.info("Webhook worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
    client = ctx.get("http")
    if client is not None:
        await client.aclose()

    logger.info("Webhook worker stopped")


async def deliver_webhook(
    ctx: dict[str, Any],
    delivery_id: str,
    webhook_url: str,
    event: dict[str, Any],
    secret: str | None = None,
) -> dict[str, Any]:
    client: httpx.AsyncClient = ctx["http"]
    secret = secret or settings.webhook_secret

    payload = json.dumps(
        event,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    event_id = str(event["event_id"])

    headers = {
        "Content-Type": "application/json",
        "X-Event-ID": event_id,
        "Idempotency-Key": event_id,
    }

    if secret:
        signature = generate_signature(payload, secret)
        headers["X-Webhook-Signature"] = f"sha256={signature}"

    attempts = 0
    last_status: int | None = None

    await update_delivery(delivery_id, status="processing")

    try:
        # Revalidate queued jobs before any outbound HTTP request.
        validate_webhook_url(
            webhook_url,
            allow_local=settings.allow_local_webhooks,
        )
        
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.webhook_max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception(is_retryable_error),
            reraise=True,
        ):
            attempts += 1
            response = None

            with attempt:
                response = await client.post(
                    webhook_url,
                    content=payload,
                    headers=headers,
                )
                last_status = response.status_code
                response.raise_for_status()

            outcome = attempt.retry_state.outcome

            if outcome is not None and outcome.failed:
                error = outcome.exception()
                status_code = (
                    error.response.status_code
                    if isinstance(error, httpx.HTTPStatusError)
                    else None
                )

                await record_attempt(
                    delivery_id=delivery_id,
                    attempt_number=attempts,
                    http_status=status_code,
                    error=type(error).__name__,
                )
            else:
                last_status = response.status_code
                await record_attempt(
                    delivery_id=delivery_id,
                    attempt_number=attempts,
                    http_status=last_status,
                    error=None,
                )

        await update_delivery(
            delivery_id,
            status="delivered",
            attempts=attempts,
            http_status=last_status,
            error=None,
        )

        return {
            "delivery_id": delivery_id,
            "event_id": event_id,
            "status": "delivered",
            "attempts": attempts,
            "http_status": last_status,
        }

    except Exception as error:
        status_code = (
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else None
        )

        await update_delivery(
            delivery_id,
            status="failed",
            attempts=attempts,
            http_status=status_code,
            error=type(error).__name__,
        )

        logger.warning(
            "Webhook delivery failed: delivery_id=%s event_id=%s "
            "attempts=%s error_type=%s",
            delivery_id,
            event_id,
            attempts,
            type(error).__name__,
        )

        return {
            "delivery_id": delivery_id,
            "event_id": event_id,
            "status": "failed",
            "attempts": attempts,
            "http_status": status_code,
            "error_type": type(error).__name__,
        }


class WorkerSettings:
    functions = [deliver_webhook]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.webhook_max_concurrency
    job_timeout = 120
    on_startup = startup
    on_shutdown = shutdown
