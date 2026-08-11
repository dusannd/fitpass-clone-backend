import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.worker import get_current_worker


# --- 1. MOCKING (Dependency Override) ---
# Same trick test_hr.py uses for the admin gate: swap the RequireRole("worker")
# instance for a stub so the tests can hit the desk panel endpoints without
# building a worker account and a JWT first.
async def override_get_current_worker():
    return 1  # Simulate Desk Worker User ID 1


async def register_member(ac: AsyncClient, first_name: str, last_name: str) -> tuple[int, str]:
    """
    Creates a member through the public API and hands back their id and email.

    The @test.com suffix matters: app/api/users.py auto-verifies those while
    settings.TESTING is on, which is what lets a test log the account in.
    """
    email = f"{uuid.uuid4().hex[:8]}@test.com"
    res = await ac.post("/api/users/", json={
        "email": email,
        "password": "strongpassword123",
        "first_name": first_name,
        "last_name": last_name,
    })
    assert res.status_code == 200, f"Registration failed: {res.text}"
    return res.json()["id"], email


# --- 2. SEARCH ---

@pytest.mark.asyncio
async def test_search_matches_first_name_last_name_email_and_full_name():
    """
    The whole point of /search is that the worker never types a numeric ID, so
    every way a member might identify themselves has to find the same row.
    """
    transport = ASGITransport(app=app)
    # A tag nothing else in the database can accidentally contain
    tag = uuid.uuid4().hex[:8]

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            user_id, email = await register_member(ac, f"Ana{tag}", f"Ilic{tag}")

            # a) By first name
            res = await ac.get(f"/api/worker/search?query=Ana{tag}")
            assert res.status_code == 200
            assert [u["user_id"] for u in res.json()] == [user_id]
            assert res.json()[0]["full_name"] == f"Ana{tag} Ilic{tag}"
            assert res.json()[0]["email"] == email

            # b) By last name
            res = await ac.get(f"/api/worker/search?query=Ilic{tag}")
            assert [u["user_id"] for u in res.json()] == [user_id]

            # c) By email fragment
            res = await ac.get(f"/api/worker/search?query={email.split('@')[0]}")
            assert [u["user_id"] for u in res.json()] == [user_id]

            # d) By "first last" typed as one string - the concat condition
            res = await ac.get(f"/api/worker/search?query=Ana{tag} Ilic{tag}")
            assert [u["user_id"] for u in res.json()] == [user_id]

        finally:
            # Drop ONLY our key. clear() would also wipe conftest's get_db
            # override and send the rest of the suite into the real database.
            app.dependency_overrides.pop(get_current_worker, None)


@pytest.mark.asyncio
async def test_search_escapes_wildcards_and_rejects_short_queries():
    """
    '%' is a SQL wildcard. Unescaped, a worker leaning on the key would match
    every user in the gym instead of nobody.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            # Make sure at least one user exists, so "match everything" would show
            await register_member(ac, "Wildcard", "Probe")

            res = await ac.get("/api/worker/search?query=%25%25")  # URL encoded "%%"
            assert res.status_code == 200
            assert res.json() == []

            # A single character is not a search, it is a table scan
            res_short = await ac.get("/api/worker/search?query=a")
            assert res_short.status_code == 422

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


@pytest.mark.asyncio
async def test_search_only_returns_active_members(set_user_roles):
    """
    The box is labelled "Find a member". Without the role join it also hands out
    the names and email addresses of trainers, admins and other workers, and
    offers deactivated accounts for a door override.
    """
    transport = ASGITransport(app=app)
    tag = uuid.uuid4().hex[:8]

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            # Three people who share a surname, so one query reaches all of them
            member_id, _ = await register_member(ac, "Real", f"Shared{tag}")
            trainer_id, _ = await register_member(ac, "Coach", f"Shared{tag}")
            admin_id, _ = await register_member(ac, "Boss", f"Shared{tag}")

            await set_user_roles(trainer_id, ["trainer"])
            await set_user_roles(admin_id, ["admin"])

            res = await ac.get(f"/api/worker/search?query=Shared{tag}")
            assert res.status_code == 200
            assert [u["user_id"] for u in res.json()] == [member_id]

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


@pytest.mark.asyncio
async def test_search_caps_results_at_ten():
    """A dropdown shows ten rows, so the query must never return more."""
    transport = ASGITransport(app=app)
    tag = uuid.uuid4().hex[:8]

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            for i in range(11):
                await register_member(ac, f"Crowd{tag}", f"Member{i}")

            res = await ac.get(f"/api/worker/search?query=Crowd{tag}")
            assert res.status_code == 200
            assert len(res.json()) == 10

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


# --- 3. ACTIVITY LOG ---

@pytest.mark.asyncio
async def test_logs_are_newest_first_and_paginate(seed_entry_logs):
    """
    Newest first, and page 2 must not repeat page 1 - that is the whole contract
    the Prev/Next buttons rely on.
    """
    transport = ASGITransport(app=app)
    now = datetime.now(timezone.utc)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            user_id, _ = await register_member(ac, "Log", "Walker")

            # Oldest first on the way in, so the endpoint has to flip them
            seeded = await seed_entry_logs([
                {"user_id": user_id, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=50)},
                {"user_id": user_id, "action_type": "EXIT", "access_granted": True,
                 "timestamp": now - timedelta(minutes=40)},
                {"user_id": user_id, "action_type": "ENTRY", "access_granted": False,
                 "reason": "No active subscription", "timestamp": now - timedelta(minutes=30)},
                {"user_id": user_id, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=20)},
                {"user_id": user_id, "action_type": "EXIT", "access_granted": True,
                 "timestamp": now - timedelta(minutes=10)},
            ])
            newest_first = list(reversed(seeded))

            # a) Envelope shape and ordering
            res = await ac.get("/api/worker/logs?skip=0&limit=50")
            assert res.status_code == 200
            body = res.json()
            assert body["total"] >= 5

            mine = [item for item in body["items"] if item["id"] in seeded]
            assert [item["id"] for item in mine] == newest_first

            # The denied scan has to carry its reason, that is why a worker looks
            denied = next(item for item in mine if item["access_granted"] is False)
            assert denied["reason"] == "No active subscription"
            assert denied["action_type"] == "ENTRY"
            assert denied["full_name"] == "Log Walker"

            # b) Pagination slices, and total stays the full count
            page_one = await ac.get("/api/worker/logs?skip=0&limit=2")
            page_two = await ac.get("/api/worker/logs?skip=2&limit=2")

            assert len(page_one.json()["items"]) == 2
            assert page_one.json()["total"] == body["total"]

            ids_one = {item["id"] for item in page_one.json()["items"]}
            ids_two = {item["id"] for item in page_two.json()["items"]}
            assert ids_one.isdisjoint(ids_two)

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


# --- 4. LIVE ATTENDANCE ---

@pytest.mark.asyncio
async def test_currently_inside_paginates_and_ignores_users_who_left(seed_entry_logs):
    """
    'Inside' means the LATEST log for that user was a granted ENTRY. Somebody who
    entered and then left must drop off the list, no matter how recent the entry.
    """
    transport = ASGITransport(app=app)
    now = datetime.now(timezone.utc)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            inside_a, _ = await register_member(ac, "Still", "Inside")
            inside_b, _ = await register_member(ac, "Also", "Inside")
            left, _ = await register_member(ac, "Already", "Gone")
            denied, _ = await register_member(ac, "Turned", "Away")

            await seed_entry_logs([
                {"user_id": inside_a, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=45)},
                {"user_id": inside_b, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=35)},
                # Entered, then left again - latest log wins
                {"user_id": left, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=25)},
                {"user_id": left, "action_type": "EXIT", "access_granted": True,
                 "timestamp": now - timedelta(minutes=15)},
                # Never got in at all
                {"user_id": denied, "action_type": "ENTRY", "access_granted": False,
                 "reason": "No active subscription", "timestamp": now - timedelta(minutes=5)},
            ])

            res = await ac.get("/api/worker/currently-inside?skip=0&limit=50")
            assert res.status_code == 200
            body = res.json()

            present = {item["user_id"] for item in body["items"]}
            assert {inside_a, inside_b}.issubset(present)
            assert left not in present
            assert denied not in present

            # The envelope carries the real headcount, not the page length
            first_page = await ac.get("/api/worker/currently-inside?skip=0&limit=1")
            assert len(first_page.json()["items"]) == 1
            assert first_page.json()["total"] == body["total"]

            second_page = await ac.get("/api/worker/currently-inside?skip=1&limit=1")
            assert first_page.json()["items"][0]["user_id"] != second_page.json()["items"][0]["user_id"]

            # Out of range values are rejected rather than silently clamped
            assert (await ac.get("/api/worker/currently-inside?limit=500")).status_code == 422
            assert (await ac.get("/api/worker/currently-inside?skip=-1")).status_code == 422

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


@pytest.mark.asyncio
async def test_denied_scan_does_not_hide_someone_who_is_inside(seed_entry_logs):
    """
    The "ghost in the gym" regression.

    Ranking every log - denied ones included - meant that a member who was inside
    and then had a single scan refused dropped off Live Attendance. Nobody could
    see them, so nobody could force-check them out, and their Redis state stayed
    INSIDE forever. A refused scan opens no door, so it must not change presence.
    This has to agree with resolve_user_status() in app/api/access.py.
    """
    transport = ASGITransport(app=app)
    now = datetime.now(timezone.utc)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            ghost, _ = await register_member(ac, "Ghost", "Inside")
            left, _ = await register_member(ac, "Went", "Home")

            await seed_entry_logs([
                # In the building...
                {"user_id": ghost, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=40)},
                # ...then scans again and is refused, exactly what access.py writes
                {"user_id": ghost, "action_type": "ENTRY", "access_granted": False,
                 "reason": "Anti-Passback Violation: Already inside.",
                 "timestamp": now - timedelta(minutes=10)},

                # Control: a granted EXIT still removes somebody, denials aside
                {"user_id": left, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=35)},
                {"user_id": left, "action_type": "EXIT", "access_granted": True,
                 "timestamp": now - timedelta(minutes=20)},
                {"user_id": left, "action_type": "EXIT", "access_granted": False,
                 "reason": "Anti-Passback Violation: Not checked in.",
                 "timestamp": now - timedelta(minutes=5)},
            ])

            res = await ac.get("/api/worker/currently-inside?skip=0&limit=50")
            assert res.status_code == 200
            items = res.json()["items"]

            present = [item["user_id"] for item in items]
            assert ghost in present, "A denied re-scan hid a member who is still inside"
            assert left not in present

            # One row per user, no matter how many logs they have
            assert present.count(ghost) == 1

            # The card still shows when they actually walked in, not when they
            # were turned away at the door. SQLite hands timestamps back without a
            # tzinfo, so pin it to UTC before comparing - that is what was stored.
            entered_at = next(item["entered_at"] for item in items if item["user_id"] == ghost)
            entered = datetime.fromisoformat(entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)

            assert (now - entered) > timedelta(minutes=30)

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


# --- 5. NULL NAMES ---

@pytest.mark.asyncio
async def test_missing_names_never_render_as_none_none(seed_entry_logs, clear_user_name):
    """
    first_name and last_name are nullable, so f"{first} {last}" prints the literal
    string "None None" onto the desk panel for anyone who has neither.
    """
    transport = ASGITransport(app=app)
    now = datetime.now(timezone.utc)
    tag = uuid.uuid4().hex[:8]

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            # Registered with a name so the email carries the searchable tag,
            # then stripped back to the nameless state older rows can be in
            nameless, email = await register_member(ac, f"Temp{tag}", "Placeholder")
            await clear_user_name(nameless)

            await seed_entry_logs([
                {"user_id": nameless, "action_type": "ENTRY", "access_granted": True,
                 "timestamp": now - timedelta(minutes=15)},
            ])

            # a) Search, found by email since the name is gone
            res_search = await ac.get(f"/api/worker/search?query={email.split('@')[0]}")
            assert res_search.status_code == 200
            assert [u["user_id"] for u in res_search.json()] == [nameless]
            assert res_search.json()[0]["full_name"] == "Unknown user"

            # b) Status check
            res_status = await ac.get(f"/api/worker/user/{nameless}/status")
            assert res_status.json()["full_name"] == "Unknown user"

            # c) Live attendance
            res_inside = await ac.get("/api/worker/currently-inside?skip=0&limit=50")
            mine = [i for i in res_inside.json()["items"] if i["user_id"] == nameless]
            assert [i["full_name"] for i in mine] == ["Unknown user"]

            # d) Activity log
            res_logs = await ac.get("/api/worker/logs?skip=0&limit=50")
            log_rows = [i for i in res_logs.json()["items"] if i["user_id"] == nameless]
            assert log_rows and all(i["full_name"] == "Unknown user" for i in log_rows)

            # The bug this guards against, spelled out
            everything = res_search.text + res_status.text + res_inside.text + res_logs.text
            assert "None None" not in everything

        finally:
            app.dependency_overrides.pop(get_current_worker, None)


# --- 6. ROLE GATE ---

@pytest.mark.asyncio
async def test_worker_endpoints_are_closed_to_everyone_else():
    """
    Without this, any logged in member could read the full turnstile history of
    every person in the gym.
    """
    transport = ASGITransport(app=app)
    endpoints = ["/api/worker/search?query=ana", "/api/worker/logs", "/api/worker/currently-inside"]

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # a) No cookie at all
        for url in endpoints:
            assert (await ac.get(url)).status_code == 401

        # b) A real, logged in member - has a valid token, just not the role
        _, email = await register_member(ac, "Nosy", "Member")
        res_login = await ac.post("/api/users/login", json={
            "email": email, "password": "strongpassword123"
        })
        assert res_login.status_code == 200
        headers = {"Cookie": f"access_token={res_login.cookies['access_token']}"}

        for url in endpoints:
            assert (await ac.get(url, headers=headers)).status_code == 403
