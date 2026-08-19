import pytest

from app.core.websockets import ConnectionManager
from app.main import app

WS_URL = "/api/access/ws"


class FakeSocket:
    """
    The manager only stores and compares sockets, so identity is all a stand-in
    needs. Using real sockets here would drag in an event loop for no benefit.
    """

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"<FakeSocket {self.name}>"


# ==========================================
# 1. THE RECONNECT RACE
# ==========================================
def test_disconnect_keeps_a_socket_that_already_replaced_the_old_one():
    """
    A stale handler must not evict the connection that replaced it.

    The registry is keyed by user_id, so one member has at most one socket in it.
    When a phone hands off from mobile data to WiFi, the new socket registers
    while the old one has not noticed the drop yet - TCP can take a while to give
    up. The old handler then unwinds and runs its cleanup. Deleting the entry
    blindly at that point removes the LIVE socket, and the member silently stops
    receiving turnstile events with no error anywhere to explain it.

    This was survivable while the frontend never reconnected on its own. With
    useGymWebSocket it happens on every network change, so the removal has to be
    identity-checked.
    """
    manager = ConnectionManager()
    stale, live = FakeSocket("stale"), FakeSocket("live")

    manager.connect(stale, user_id=1)
    manager.connect(live, user_id=1)  # the reconnect replaces the entry

    manager.disconnect(1, stale)  # the stale handler finally unwinds

    assert manager.active_connections.get(1) is live


def test_disconnect_removes_the_socket_it_was_given():
    """
    The ordinary case still has to clean up, or the registry leaks dead sockets
    and send_personal_message keeps writing to a closed connection.
    """
    manager = ConnectionManager()
    socket = FakeSocket("only")

    manager.connect(socket, user_id=7)
    manager.disconnect(7, socket)

    assert 7 not in manager.active_connections


def test_disconnect_without_a_socket_stays_unconditional():
    """
    Called with no socket the guard does not apply. Pinned so that the fallback
    used by any future caller cannot quietly become a no-op.
    """
    manager = ConnectionManager()
    manager.connect(FakeSocket("whoever"), user_id=3)

    manager.disconnect(3)

    assert 3 not in manager.active_connections


# ==========================================
# 2. AUTH REFUSAL REACHES THE BROWSER
# ==========================================
#
# These drive the ASGI app by hand instead of using TestClient, and that is the
# whole point of them.
#
# TestClient never performs a real HTTP upgrade: it reads the raw ASGI messages,
# so it reports close code 1008 whether or not the socket was ever accepted. A
# test written against it passes with the bug fully reintroduced - verified, not
# assumed. The difference only exists on the wire, where closing an unaccepted
# socket makes Starlette answer the upgrade with a plain HTTP 403 instead. The
# WebSocket spec forbids browsers from exposing that status to JavaScript, so the
# client sees close code 1006 ("abnormal closure"), which is exactly what a
# dropped network looks like - and the frontend would retry an expired session
# forever instead of giving up.
#
# What actually has to hold is the ORDER of the messages the app emits: accept
# first, close second. That is what these assert.


async def collect_handshake_messages(cookie: str | None = None) -> list[dict]:
    """
    Runs the websocket route directly against the ASGI interface and returns
    every message the app sent, in order.
    """
    headers = [(b"host", b"testserver")]
    if cookie is not None:
        headers.append((b"cookie", f"access_token={cookie}".encode()))

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "path": WS_URL,
        "raw_path": WS_URL.encode(),
        "query_string": b"",
        "headers": headers,
        "subprotocols": [],
        "state": {},
    }

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_unauthenticated_socket_is_accepted_before_it_is_closed():
    """
    No cookie: the app must accept the handshake, then close with 1008.

    If `websocket.accept` is missing from the front of this list, the browser
    never learns why it was turned away.
    """
    sent = await collect_handshake_messages()

    assert [message["type"] for message in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == 1008


@pytest.mark.asyncio
async def test_socket_with_an_invalid_token_is_accepted_before_it_is_closed():
    """
    Same contract for a cookie that is present but not valid - the case a member
    actually hits when their session expires while the dashboard is open.
    """
    sent = await collect_handshake_messages(cookie="not-a-real-jwt")

    assert [message["type"] for message in sent] == ["websocket.accept", "websocket.close"]
    assert sent[-1]["code"] == 1008
