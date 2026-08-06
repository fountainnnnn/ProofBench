import pytest

from engine import integration_agent
from server.test_backend_hardening import client, headers  # noqa: F401


@pytest.fixture(autouse=True)
def clear_agent_provider_environment(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
        "SCRAPEDO_API_TOKEN",
        "OXYLABS_USERNAME", "OXYLABS_PASSWORD",
        "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE",
        "BRIGHTDATA_UNLOCKER_ZONE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_status_and_messages_fail_closed_without_both_prerequisites(client):
    status = client.get(
        "/api/settings/integration-agent",
        headers=headers("token-a"),
    )
    assert status.status_code == 200
    assert status.json()["ready"] is False
    assert set(status.json()["missing"]) == {"default_llm", "web_scraper"}

    blocked = client.post(
        "/api/settings/integration-agent/messages",
        headers=headers("token-a"),
        json={"message": "Integrate Vendor Cloud"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "integration_agent_unavailable"


def test_ready_agent_uses_the_tenant_provider_snapshot(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "deployment-llm-secret")
    monkeypatch.setenv("SCRAPEDO_API_TOKEN", "deployment-scraper-secret")
    captured = {}

    def fake_respond(message, env, history):
        captured.update({"message": message, "env": env, "history": history})
        return {
            "message": "A bounded connector proposal is ready.",
            "sources": [{"title": "Docs", "url": "https://vendor.test/docs"}],
            "implementation": {
                "status": "proposal",
                "summary": "Proposal generated.",
            },
        }

    monkeypatch.setattr(integration_agent, "respond", fake_respond)

    status = client.get(
        "/api/settings/integration-agent",
        headers=headers("token-a"),
    ).json()
    assert status["ready"] is True
    assert status["llm"]["provider"] == "openrouter"
    assert status["scraper"]["provider"] == "scrapedo"

    response = client.post(
        "/api/settings/integration-agent/messages",
        headers=headers("token-a"),
        json={
            "message": "What about its batch endpoint?",
            "history": [
                {"role": "user", "content": "Integrate Vendor Cloud"},
                {"role": "assistant", "content": "I found its API documentation."},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["implementation"]["status"] == "proposal"
    assert captured["message"] == "What about its batch endpoint?"
    assert captured["history"][0]["content"] == "Integrate Vendor Cloud"
    assert captured["env"]["OPENROUTER_API_KEY"] == "deployment-llm-secret"
    assert "deployment-llm-secret" not in response.text
    assert "deployment-scraper-secret" not in response.text


def test_integration_agent_requires_authentication(client):
    assert client.get("/api/settings/integration-agent").status_code == 401
    assert client.post(
        "/api/settings/integration-agent/messages",
        json={"message": "Integrate Vendor Cloud"},
    ).status_code == 401
