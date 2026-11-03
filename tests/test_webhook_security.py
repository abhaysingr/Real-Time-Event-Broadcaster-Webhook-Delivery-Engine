
import socket

import pytest

import app.webhook_security as security
from app.webhook_security import (
    UnsafeWebhookURLError,
    validate_webhook_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9001/webhook",
        "http://localhost/webhook",
        "http://192.168.1.10/webhook",
        "http://10.0.0.5/webhook",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/webhook",
        "http://user:password@example.com/webhook",
    ],
)
def test_rejects_unsafe_webhook_urls(url):
    with pytest.raises(UnsafeWebhookURLError):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/webhook",
        "http://example.com:8080/webhook",
    ],
)
def test_accepts_public_urls_with_public_dns(url, monkeypatch):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "",
             ("93.184.216.34", 443))
        ],
    )

    assert validate_webhook_url(url) == url


def test_allows_loopback_in_local_development():
    assert (
        validate_webhook_url(
            "http://127.0.0.1:9001/webhook",
            allow_local=True,
        )
        == "http://127.0.0.1:9001/webhook"
    )


def test_rejects_hostname_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "",
             ("10.0.0.5", 443))
        ],
    )

    with pytest.raises(
        UnsafeWebhookURLError,
        match="resolves to a private",
    ):
        validate_webhook_url("https://example.com/webhook")


def test_rejects_hostname_with_mixed_public_and_private_ips(monkeypatch):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "",
             ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "",
             ("192.168.1.10", 443)),
        ],
    )

    with pytest.raises(UnsafeWebhookURLError):
        validate_webhook_url("https://example.com/webhook")


def test_rejects_hostname_that_cannot_resolve(monkeypatch):
    def fail_dns(*args, **kwargs):
        raise socket.gaierror("Name resolution failed")

    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        fail_dns,
    )

    with pytest.raises(
        UnsafeWebhookURLError,
        match="could not be resolved",
    ):
        validate_webhook_url("https://example.com/webhook")
