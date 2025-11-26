import requests

def test_health():
    r = requests.get("http://localhost:8000/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
