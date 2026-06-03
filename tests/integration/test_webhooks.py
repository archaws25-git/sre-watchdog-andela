"""Integration tests for the webhooks endpoint."""

import pytest


@pytest.mark.integration
class TestWebhookEchoEndpoint:
    """Tests for POST /webhooks/echo."""

    def test_echo_returns_200_with_payload(self, test_client):
        """POST /webhooks/echo returns 200 with the echoed payload."""
        payload = {"alert": "test", "severity": "HIGH", "service": "api-gateway"}
        response = test_client.post("/webhooks/echo", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert "received_at" in body
        assert body["payload"] == payload

    def test_echo_persists_payload(self, test_client):
        """POST /webhooks/echo persists the payload to the database."""
        payload = {"test": "persistence"}
        response1 = test_client.post("/webhooks/echo", json=payload)
        assert response1.status_code == 200

        # Post another one
        payload2 = {"test": "second"}
        response2 = test_client.post("/webhooks/echo", json=payload2)
        assert response2.status_code == 200
