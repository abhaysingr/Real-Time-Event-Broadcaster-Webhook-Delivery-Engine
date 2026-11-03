
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeWebhookURLError(ValueError):
    """Raised when a webhook URL targets an unsafe destination."""


def _is_public_ip(address: str) -> bool:
    """Return True only for globally routable IP addresses."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False

    return ip.is_global


def validate_webhook_url(
    url: str,
    *,
    allow_local: bool = False,
) -> str:
    """Validate URL structure and resolve hostnames to check their IPs."""

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeWebhookURLError("Malformed webhook URL.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeWebhookURLError(
            "Webhook URLs must use HTTP or HTTPS."
        )

    if not hostname:
        raise UnsafeWebhookURLError(
            "Webhook URL must include a hostname."
        )

    if parsed.username is not None or parsed.password is not None:
        raise UnsafeWebhookURLError(
            "Webhook URLs must not contain embedded credentials."
        )

    if port is not None and not 1 <= port <= 65535:
        raise UnsafeWebhookURLError("Invalid webhook port.")

    hostname = hostname.rstrip(".").lower()

    if hostname == "localhost" or hostname.endswith(".localhost"):
        if allow_local:
            return url
        raise UnsafeWebhookURLError(
            "Localhost webhook destinations are not allowed."
        )

    # Permit literal loopback addresses only in local development.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if literal_ip.is_global:
            return url

        if allow_local and literal_ip.is_loopback:
            return url

        raise UnsafeWebhookURLError(
            "Private, local, and reserved IP addresses are not allowed."
        )

    # Resolve DNS names and reject the hostname if ANY answer is unsafe.
    try:
        results = socket.getaddrinfo(
            hostname,
            port or (443 if parsed.scheme.lower() == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError(
            "Webhook hostname could not be resolved."
        ) from exc

    if not results:
        raise UnsafeWebhookURLError(
            "Webhook hostname returned no IP addresses."
        )

    addresses = {result[4][0] for result in results}

    if not all(_is_public_ip(address) for address in addresses):
        raise UnsafeWebhookURLError(
            "Webhook hostname resolves to a private, local, or reserved IP."
        )

    return url
