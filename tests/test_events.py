from fastapi.testclient import TestClient

from app.main import app


def test_event_is_broadcast_to_connected_websocket():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            # The endpoint expects incoming text to keep the connection active.
            websocket.send_text("ready")

            response = client.post(
                "/events",
                json={
                    "event_type": "order.created",
                    "data": {"order_id": 101},
                },
            )

            assert response.status_code == 202

            received_event = websocket.receive_json()

            assert received_event["event_type"] == "order.created"
            assert received_event["data"]["order_id"] == 101
            assert "event_id" in received_event
            assert "created_at" in received_event
            