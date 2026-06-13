from __future__ import annotations

from plnt.compute.backend_picker import choose


def test_force_offline_short_circuits(monkeypatch):
    monkeypatch.delenv("PLNT_CLOUD_URL", raising=False)
    c = choose(force="offline")
    assert c.kind == "offline"
    assert c.api_key == ""


def test_ssd_missing_falls_back_to_cloud(monkeypatch, tmp_path):
    # SSD path doesn't exist
    monkeypatch.setenv("PLNT_REQUIRED_PATH", str(tmp_path / "missing"))
    # Cloud configured + reachable (we point at 1.1.1.1:443 which always responds at TCP)
    monkeypatch.setenv("PLNT_CLOUD_URL", "https://1.1.1.1")
    monkeypatch.setenv("PLNT_CLOUD_API_KEY", "sk-test")
    monkeypatch.setenv("PLNT_CLOUD_SMALL_MODEL", "gpt-4o-mini")
    # Local is "reachable" only if we point at something dead
    monkeypatch.setenv("PLNT_LOCAL_URL", "http://127.0.0.1:1")
    c = choose()
    assert c.kind == "cloud"
    assert c.url == "https://1.1.1.1"
    assert c.api_key == "sk-test"


def test_local_chosen_when_ssd_and_url_alive(monkeypatch, tmp_path):
    monkeypatch.setenv("PLNT_REQUIRED_PATH", str(tmp_path))  # exists
    # Point at an obviously reachable TCP target (CloudFlare 1.1.1.1:443)
    monkeypatch.setenv("PLNT_LOCAL_URL", "https://1.1.1.1")
    c = choose()
    assert c.kind == "local"


def test_event_payload_has_no_api_key():
    from plnt.compute.backend_picker import BackendChoice

    bc = BackendChoice(kind="cloud", url="https://x", model="m", api_key="secret", reason="r")
    evt = bc.to_event()
    assert "api_key" not in evt
    assert evt["model"] == "m"
