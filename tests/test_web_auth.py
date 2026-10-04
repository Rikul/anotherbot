import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.channels.web_auth as web_auth
from app.channels.message_queue import MessageQueue
from app.channels.web_auth import COOKIE_NAME, WebAuthMiddleware, is_loopback_host
from app.channels.web_channel import WebChannel

PASSWORD = "correct horse"
SAME_ORIGIN = {"origin": "http://testserver"}


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch):
    monkeypatch.setattr(web_auth, "_FAILED_LOGIN_DELAY", 0)


def make_channel(host="0.0.0.0", password=PASSWORD):
    ch = WebChannel(mq=MessageQueue(), host=host, port=8765, password=password)
    ch.start()
    return ch


def make_client(password=PASSWORD):
    return TestClient(make_channel(password=password)._asgi_app, follow_redirects=False)


def login(client, password=PASSWORD):
    return client.post("/login", data={"password": password}, headers=SAME_ORIGIN)


# --- startup guard ---

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_host_without_password_is_allowed(host):
    ch = make_channel(host=host, password=None)
    assert ch._asgi_app is ch._fasthtml_app  # no auth layer


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_non_loopback_host_without_password_refuses_to_start(host):
    with pytest.raises(RuntimeError, match="WEB_PASSWORD"):
        make_channel(host=host, password=None)


def test_empty_password_counts_as_unset():
    with pytest.raises(RuntimeError, match="WEB_PASSWORD"):
        make_channel(host="0.0.0.0", password="")


def test_password_wraps_app_in_auth_middleware():
    ch = make_channel()
    assert isinstance(ch._asgi_app, WebAuthMiddleware)


def test_is_loopback_host():
    assert is_loopback_host("127.0.0.5")
    assert not is_loopback_host("example.com")


# --- unauthenticated access ---

def test_index_redirects_to_login():
    resp = make_client().get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/api/conversations", "/api/messages?conv_id=1", "/api/status",
                                  "/static/web_channel.js"])
def test_api_and_static_return_401(path):
    assert make_client().get(path).status_code == 401


def test_upload_requires_login():
    resp = make_client().post("/api/upload", files={"files": ("a.txt", b"x")}, headers=SAME_ORIGIN)
    assert resp.status_code == 401


def test_login_page_is_public():
    resp = make_client().get("/login")
    assert resp.status_code == 200
    assert 'name="password"' in resp.text


def test_websocket_rejected_without_session():
    client = make_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=SAME_ORIGIN):
            pass


# --- login / logout ---

def test_wrong_password_shows_error_and_sets_no_cookie():
    resp = login(make_client(), "nope")
    assert resp.status_code == 401
    assert "Wrong password" in resp.text
    assert COOKIE_NAME not in resp.cookies


def test_correct_password_sets_session_cookie():
    resp = login(make_client())
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers["set-cookie"].lower()
    assert COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie  # plain http


def test_cookie_is_secure_behind_https_proxy():
    client = make_client()
    resp = client.post("/login", data={"password": PASSWORD},
                       headers={**SAME_ORIGIN, "x-forwarded-proto": "https"})
    assert "secure" in resp.headers["set-cookie"].lower()


def test_session_grants_access():
    client = make_client()
    login(client)
    assert client.get("/api/status").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/logout"' in page.text


def test_login_page_redirects_when_already_logged_in():
    client = make_client()
    login(client)
    resp = client.get("/login")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_websocket_accepted_with_session():
    client = make_client()
    login(client)
    with client.websocket_connect("/ws", headers=SAME_ORIGIN) as ws:
        ws.send_text('{"type": "message", "content": "/whoami"}')
        assert "Connection ID" in ws.receive_json()["content"]


def test_logout_clears_session():
    client = make_client()
    login(client)
    resp = client.get("/logout")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert client.get("/api/status").status_code == 401


def test_tampered_cookie_rejected():
    client = make_client()
    client.cookies.set(COOKIE_NAME, "web.forged.signature")
    assert client.get("/api/status").status_code == 401


def test_expired_session_rejected(monkeypatch):
    client = make_client()
    login(client)
    monkeypatch.setattr(web_auth, "SESSION_MAX_AGE", -1)
    assert client.get("/api/status").status_code == 401


def test_changing_password_invalidates_sessions():
    old = make_client()
    login(old)
    new = make_client(password="a different password")
    new.cookies.set(COOKIE_NAME, old.cookies[COOKIE_NAME])
    assert new.get("/api/status").status_code == 401


def test_login_body_too_large_rejected():
    resp = make_client().post("/login", content=b"password=" + b"x" * 10_000,
                              headers={**SAME_ORIGIN, "content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 413


# --- same-origin checks ---

def test_cross_origin_websocket_rejected_even_with_session():
    client = make_client()
    login(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}):
            pass


def test_cross_origin_post_rejected():
    client = make_client()
    login(client)
    resp = client.post("/api/upload", files={"files": ("a.txt", b"x")},
                       headers={"origin": "http://evil.example"})
    assert resp.status_code == 403


def test_cross_origin_login_rejected():
    resp = make_client().post("/login", data={"password": PASSWORD},
                              headers={"origin": "http://evil.example"})
    assert resp.status_code == 403


# --- no password: unchanged behaviour on localhost ---

def test_no_password_on_localhost_serves_without_login():
    ch = make_channel(host="127.0.0.1", password=None)
    client = TestClient(ch._asgi_app, follow_redirects=False)
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/logout"' not in page.text
