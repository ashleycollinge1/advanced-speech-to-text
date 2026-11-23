import uuid

from conftest import Api
from fastapi.testclient import TestClient

from app import media
from app.db import session_scope
from app.main import app
from app.models import STATUS_DONE, Recording, Segment


def _make_user(api, username="alice", role="user"):
    r = api.post("/api/users", json={"username": username, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def _activate(_client, username, temp, new="a-much-longer-password"):
    # A separate client (cookie jar) per simulated user.
    u = Api(TestClient(app))
    assert u.login(username, temp).status_code == 200
    # Forced password change gate.
    assert u.get("/api/recordings").json()["detail"] == "password_change_required"
    r = u.post("/api/auth/password", json={"current_password": temp, "new_password": new})
    assert r.status_code == 200, r.text
    return u


def _upload(a, name="talk.mp3", body=b"ID3fakeaudio"):
    r = a.post(f"/api/recordings?filename={name}", content=body, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 201, r.text
    return r.json()["recording"]


def _finish(rec_id, segments):
    with session_scope() as db:
        rec = db.get(Recording, uuid.UUID(rec_id))
        rec.status = STATUS_DONE
        rec.has_playback = True
        for i, (start, end, words) in enumerate(segments):
            db.add(
                Segment(
                    recording_id=rec.id, idx=i, start=start, end=end, text=" ".join(w[2] for w in words), words=words
                )
            )


# --------------------------------------------------------------------------- auth


def test_admin_bootstrap_and_me(api):
    me = api.get("/api/auth/me").json()
    assert me["user"]["username"] == "admin" and me["user"]["role"] == "admin"
    assert me["csrf_token"] == api.csrf


def test_bad_login_and_rate_limit(client):
    a = Api(client)
    for _ in range(5):
        assert a.login("admin", "nope").status_code == 401
    r = a.login("admin", "correct-horse-battery")
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_unknown_user_is_401(client):
    assert Api(client).login("ghost", "whatever").status_code == 401


def test_csrf_required_on_mutations(api, client):
    r = client.post("/api/users", json={"username": "bob"})
    assert r.status_code == 403
    r = client.post("/api/users", json={"username": "bob"}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403


def test_logout_invalidates_session(api):
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/auth/me").status_code == 401


def test_cookie_flags(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


# --------------------------------------------------------------------------- users


def test_user_lifecycle(api, client):
    created = _make_user(api)
    uid = created["user"]["id"]
    assert created["user"]["must_change_password"] is True
    alice = _activate(client, "alice", created["temporary_password"])
    assert alice.get("/api/recordings").status_code == 200
    # Non-admins can't manage users.
    assert alice.get("/api/users").status_code == 403
    # Weak passwords are rejected.
    r = alice.post("/api/auth/password", json={"current_password": "a-much-longer-password", "new_password": "short"})
    assert r.status_code == 422
    # Deactivation signs the user out.
    assert api.patch(f"/api/users/{uid}", json={"is_active": False}).status_code == 200
    assert alice.get("/api/auth/me").status_code == 401
    assert api.delete(f"/api/users/{uid}").status_code == 200


def test_duplicate_username_case_insensitive(api):
    _make_user(api, "Carol")
    assert api.post("/api/users", json={"username": "carol"}).status_code == 409


def test_cannot_remove_last_admin(api):
    me = api.get("/api/auth/me").json()["user"]
    assert api.patch(f"/api/users/{me['id']}", json={"role": "user"}).status_code == 409
    assert api.delete(f"/api/users/{me['id']}").status_code == 409


# --------------------------------------------------------------------------- recordings


def test_upload_validation(api):
    r = api.post("/api/recordings?filename=evil.exe", content=b"x")
    assert r.status_code == 415
    r = api.post("/api/recordings?filename=a.wav", content=b"")
    assert r.status_code == 400
    r = api.post("/api/recordings?filename=big.wav", content=b"0" * (1024 * 1024 + 1))
    assert r.status_code == 413


def test_upload_and_isolation(api, client):
    alice = _activate(client, "alice", _make_user(api)["temporary_password"])
    rec = _upload(alice, "Team Meeting.webm")
    assert rec["status"] == "queued" and rec["title"] == "Team Meeting"
    assert media.original_path(uuid.UUID(rec["id"]), "webm").is_file()

    bob = _activate(client, "bob", _make_user(api, "bob")["temporary_password"])
    assert bob.get(f"/api/recordings/{rec['id']}").status_code == 404
    assert bob.get("/api/recordings").json()["recordings"] == []
    # Admin sees everything with scope=all.
    all_recs = api.get("/api/recordings?scope=all").json()["recordings"]
    assert [r["owner"] for r in all_recs] == ["alice"]
    assert api.get("/api/recordings").json()["recordings"] == []

    assert alice.delete(f"/api/recordings/{rec['id']}").status_code == 200
    assert not media.recording_dir(uuid.UUID(rec["id"])).exists()


def test_transcript_edit_search_export_highlights(api):
    rec = _upload(api)
    _finish(
        rec["id"],
        [
            (0.0, 2.0, [[0.0, 0.5, "Budget"], [0.5, 1.0, "planning"], [1.0, 2.0, "today."]]),
            (2.5, 4.0, [[2.5, 3.0, "Quarterly"], [3.0, 4.0, "forecasts."]]),
        ],
    )
    segs = api.get(f"/api/recordings/{rec['id']}/transcript").json()["segments"]
    assert len(segs) == 2 and segs[0]["words"][0] == [0.0, 0.5, "Budget"]

    r = api.put(f"/api/recordings/{rec['id']}/segments/{segs[1]['id']}", json={"text": "Quarterly revenue forecasts."})
    seg = r.json()["segment"]
    assert seg["edited"] and [w[2] for w in seg["words"]] == ["Quarterly", "revenue", "forecasts."]
    assert seg["words"][0][:2] == [2.5, 3.0]

    res = api.get("/api/search?q=revenue").json()
    assert res["results"][0]["recording_id"] == rec["id"]
    assert "\x02revenue\x03" in res["results"][0]["hits"][0]["snippet"]
    assert api.get("/api/search?q=nonexistentword").json()["results"] == []

    srt = api.get(f"/api/recordings/{rec['id']}/export?format=srt")
    assert srt.status_code == 200 and "00:00:02,500 --> 00:00:04,000" in srt.text
    assert "attachment" in srt.headers["content-disposition"]

    h = api.post(
        f"/api/recordings/{rec['id']}/highlights", json={"start": 0.5, "end": 2.0, "text": "planning today."}
    ).json()["highlight"]
    assert (
        api.patch(f"/api/highlights/{h['id']}", json={"note": "action item"}).json()["highlight"]["note"]
        == "action item"
    )
    hs = api.get(f"/api/recordings/{rec['id']}/highlights").json()["highlights"]
    assert len(hs) == 1
    bad = api.post(f"/api/recordings/{rec['id']}/highlights", json={"start": 2, "end": 1, "text": "x"})
    assert bad.status_code == 422
    assert api.delete(f"/api/highlights/{h['id']}").status_code == 200


def test_search_isolated_between_users(api, client):
    alice = _activate(client, "alice", _make_user(api)["temporary_password"])
    rec = _upload(api)
    _finish(rec["id"], [(0.0, 1.0, [[0.0, 1.0, "confidential"]])])
    assert alice.get("/api/search?q=confidential").json()["results"] == []
    assert api.get("/api/search?q=confidential").json()["results"]


def test_audio_range_requests(api):
    rec = _upload(api)
    rid = uuid.UUID(rec["id"])
    media.playback_path(rid).write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    url = f"/api/recordings/{rec['id']}/audio"
    full = api.get(url)
    assert full.status_code == 200 and len(full.content) == 1024 and full.headers["accept-ranges"] == "bytes"
    part = api.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/1024"
    tail = api.get(url, headers={"Range": "bytes=-4"})
    assert tail.content == bytes([252, 253, 254, 255])
    assert api.get(url, headers={"Range": "bytes=5000-"}).status_code == 416


def test_retry_only_failed(api):
    rec = _upload(api)
    assert api.post(f"/api/recordings/{rec['id']}/retry").status_code == 409


def test_security_headers_and_spa(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/nope").status_code == 404
    assert client.get("/healthz").json()["ok"] is True
