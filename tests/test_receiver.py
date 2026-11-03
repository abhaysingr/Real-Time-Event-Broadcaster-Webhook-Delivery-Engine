import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import app.receiver as receiver
from app.webhook_signing import generate_signature


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        receiver.settings,
        "webhook_secret",
        "test-secret",
    )

    asyncio.run(receiver.reset_receiver_db())

    with TestClient(receiver.app) as test_client:
        yield test_client

    asyncio.run(receiver.reset_receiver_db())


def send_event(client, event_id, key=None):
    payload = {
        "event_id": event_id,
        "event_type": "test.event",
        "data": {"message": "hello"},
    }

    body = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    signature = generate_signature(body, "test-secret")

    headers = {
        "X-Webhook-Signature": f"sha256={signature}",
    }

    if key is not None:
        headers["Idempotency-Key"] = key

    return client.post(
        "/webhook",
        content=body,
        headers=headers,
    )


def test_first_event_is_processed(client):
    response = send_event(client, "event-001", "key-001")

    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    assert client.get("/received").json()["count"] == 1


def test_duplicate_event_is_not_processed_twice(client):
    first = send_event(client, "event-001", "key-001")
    second = send_event(client, "event-001", "key-001")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["status"] == "already_processed"
    assert client.get("/received").json()["count"] == 1


def test_different_keys_process_separate_events(client):
    first = send_event(client, "event-001", "key-001")
    second = send_event(client, "event-002", "key-002")

    assert first.status_code == 200
    assert second.status_code == 200
    assert client.get("/received").json()["count"] == 2


def test_missing_idempotency_key_is_rejected(client):
    response = send_event(client, "event-001")

    assert response.status_code == 400
    assert client.get("/received").json()["count"] == 0


def test_failed_attempt_can_be_retried(client):
    configured = client.post("/test/fail-next/1")
    assert configured.status_code == 200

    first = send_event(client, "event-001", "key-001")
    second = send_event(client, "event-001", "key-001")

    assert first.status_code == 503
    assert second.status_code == 200
    assert second.json()["duplicate"] is False
    assert client.get("/received").json()["count"] == 1

    attempts = client.get("/attempts").json()["attempts"]
    assert [item["status"] for item in attempts] == [503, 200]


def test_idempotency_persists_after_receiver_restart(monkeypatch):
    monkeypatch.setattr(
        receiver.settings,
        "webhook_secret",
        "test-secret",
    )

    asyncio.run(receiver.reset_receiver_db())

    try:
        with TestClient(receiver.app) as first_client:
            first = send_event(
                first_client,
                "event-persistent-001",
                "key-persistent-001",
            )

            assert first.status_code == 200
            assert first.json()["duplicate"] is False
            assert first_client.get("/received").json()["count"] == 1

        # Reopen the app without resetting SQLite.
        with TestClient(receiver.app) as restarted_client:
            second = send_event(
                restarted_client,
                "event-persistent-001",
                "key-persistent-001",
            )

            assert second.status_code == 200
            assert second.json()["status"] == "already_processed"
            assert second.json()["duplicate"] is True
            assert restarted_client.get("/received").json()["count"] == 1

    finally:
        asyncio.run(receiver.reset_receiver_db())



from concurrent.futures import ThreadPoolExecutor


def test_concurrent_duplicate_requests_are_processed_once(client):
    event_id = "event-concurrent-001"
    idempotency_key = "key-concurrent-001"

    # Send the same event from 10 concurrent requests.
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(
                send_event,
                client,
                event_id,
                idempotency_key,
            )
            for _ in range(10)
        ]

        responses = [future.result() for future in futures]

    # Every request should receive a successful HTTP response.
    assert all(response.status_code == 200 for response in responses)

    # Exactly one request should process the event for the first time.
    results = [response.json() for response in responses]

    first_time_processed = sum(
        result["duplicate"] is False
        for result in results
    )

    duplicates = sum(
        result["duplicate"] is True
        for result in results
    )

    assert first_time_processed == 1
    assert duplicates == 9

    # The database must contain only one processed event.
    assert client.get("/received").json()["count"] == 1



def test_missing_signature_is_rejected(client):
    body = json.dumps({
        "event_id": "event-no-signature",
        "event_type": "test.event",
        "data": {},
    }).encode("utf-8")

    response = client.post(
        "/webhook",
        content=body,
        headers={"Idempotency-Key": "key-no-signature"},
    )

    assert response.status_code == 401
    assert client.get("/received").json()["count"] == 0


def test_invalid_signature_is_rejected(client):
    payload = {
        "event_id": "event-invalid-signature",
        "event_type": "test.event",
        "data": {},
    }

    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/webhook",
        content=body,
        headers={
            "Idempotency-Key": "key-invalid-signature",
            "X-Webhook-Signature": "sha256=invalid",
        },
    )

    assert response.status_code == 401
    assert client.get("/received").json()["count"] == 0


def test_modified_payload_is_rejected(client):
    original_body = json.dumps(
        {
            "event_id": "event-tampered",
            "event_type": "test.event",
            "data": {"amount": 100},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    signature = generate_signature(
        original_body,
        "test-secret",
    )

    modified_body = original_body.replace(b"100", b"999")

    response = client.post(
        "/webhook",
        content=modified_body,
        headers={
            "Idempotency-Key": "key-tampered",
            "X-Webhook-Signature": f"sha256={signature}",
        },
    )

    assert response.status_code == 401
    assert client.get("/received").json()["count"] == 0
