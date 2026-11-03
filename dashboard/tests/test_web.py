from __future__ import annotations

from fastapi.testclient import TestClient


def test_web_auth(client: TestClient) -> None:
    # 1. /healthz ok unauthenticated
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"

    # 2. 200 for direct local (no forwarding headers, localhost client)
    r = client.get("/")
    assert r.status_code == 200

    # 3. 403 for proxied without login
    r = client.get("/", headers={"X-Forwarded-For": "198.18.0.4"})
    assert r.status_code == 403

    # 4. 403 for wrong login
    r = client.get("/", headers={"Tailscale-User-Login": "attacker@example.com"})
    assert r.status_code == 403

    # 5. 200 for allowed user (user@example.com)
    r = client.get(
        "/",
        headers={
            "Tailscale-User-Login": "user@example.com",
            "Tailscale-User-Name": "Daniel Clark",
            "X-Forwarded-For": "100.64.0.1",
        },
    )
    assert r.status_code == 200
    assert "Daniel Clark" in r.text


def test_web_pages_render_200(client: TestClient) -> None:
    # 1. Today view
    r = client.get("/")
    assert r.status_code == 200
    assert "Chief of Staff" in r.text

    # 2. Bot tab
    r = client.get("/b/radar")
    assert r.status_code == 200
    assert "Radar" in r.text

    # 3. Report view
    r = client.get("/r/1")
    assert r.status_code == 200

    # 4. Item view
    r = client.get("/i/1")
    assert r.status_code == 200

    # 5. History view
    r = client.get("/history")
    assert r.status_code == 200
    assert "All Reports by Date" in r.text

    # 6. Search view
    r = client.get("/search?q=hyades")
    assert r.status_code == 200
    assert "hyades" in r.text.lower()


def test_api_endpoints(client: TestClient) -> None:
    r = client.get("/api/items")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0

    r = client.get("/api/reports")
    assert r.status_code == 200
    reps = r.json()
    assert isinstance(reps, list)
    assert len(reps) > 0

    r = client.get("/api/report/1")
    assert r.status_code == 200
    rep = r.json()
    assert rep["id"] == 1
