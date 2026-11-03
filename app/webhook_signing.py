import hashlib
import hmac


def generate_signature(payload: bytes, secret: str) -> str:
    """Generate an HMAC-SHA256 signature for the exact payload bytes."""
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    payload: bytes,
    secret: str,
    signature: str,
) -> bool:
    """Verify a signature without ordinary string comparison."""
    expected_signature = generate_signature(payload, secret)

    return hmac.compare_digest(
        expected_signature,
        signature,
    )