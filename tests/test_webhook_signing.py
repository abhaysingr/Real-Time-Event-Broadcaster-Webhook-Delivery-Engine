from app.webhook_signing import (
    generate_signature,
    verify_signature,
)


def test_valid_signature_is_accepted():
    payload = b'{"event":"order.created","order_id":101}'
    secret = "test-secret"

    signature = generate_signature(payload, secret)

    assert verify_signature(payload, secret, signature) is True


def test_modified_payload_is_rejected():
    original_payload = b'{"event":"order.created","order_id":101}'
    modified_payload = b'{"event":"order.created","order_id":999}'
    secret = "test-secret"

    signature = generate_signature(original_payload, secret)

    assert verify_signature(modified_payload, secret, signature) is False


def test_wrong_secret_is_rejected():
    payload = b'{"event":"order.created","order_id":101}'

    signature = generate_signature(payload, "correct-secret")

    assert verify_signature(payload, "wrong-secret", signature) is False


def test_invalid_signature_is_rejected():
    payload = b'{"event":"order.created","order_id":101}'

    assert verify_signature(payload, "test-secret", "invalid-signature") is False