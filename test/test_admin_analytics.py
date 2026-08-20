import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin
from app.api.helpers import to_gym_time


# Same override trick test_hr.py uses: force the admin dependency to pass without
# having to mint a real JWT.
async def override_get_current_admin():
    return 1


async def _register(ac: AsyncClient, first_name: str = "Test", last_name: str = "User") -> tuple[int, str]:
    """Creates a user and hands back their id and email."""
    email = f"analytics_{uuid.uuid4().hex[:8]}@gym.com"
    res = await ac.post("/api/users/", json={
        "email": email,
        "password": "strongpassword123",
        "first_name": first_name,
        "last_name": last_name,
    })
    assert res.status_code == 200, f"Could not register {email}"
    return res.json()["id"], email


@pytest.mark.asyncio
async def test_manual_override_audit_returns_rows(seed_entry_logs):
    """
    REGRESSION TEST: /audit/manual-overrides must survive actually having a row.

    AdminEntryLogResponse declared model_config at module level instead of inside
    the class, so it never got from_attributes and could not read an ORM object.
    The endpoint looked healthy only because an empty list validates nothing - the
    first real override turned it into a 500. This test seeds one and asserts 200.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        member_id, member_email = await _register(ac, "Audit", "Member")
        worker_id, _ = await _register(ac, "Desk", "Worker")

        # A manual override is a log that carries a worker_id
        await seed_entry_logs([
            {"user_id": member_id, "worker_id": worker_id, "access_granted": True, "action_type": "ENTRY"},
        ])

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            res = await ac.get("/api/admin/audit/manual-overrides")
            assert res.status_code == 200, f"Audit log failed to serialize: {res.text[:200]}"

            body = res.json()
            assert set(body) == {"total", "items"}, "Audit log must use the paging envelope"

            rows = body["items"]
            mine = [r for r in rows if r["user"] and r["user"]["email"] == member_email]
            assert len(mine) == 1, "The seeded override is missing from the audit log"

            # The nested objects are the part that actually exercises from_attributes
            row = mine[0]
            assert row["user"]["email"] == member_email
            assert row["worker"] is not None, "The override lost the worker who authorised it"
            assert row["access_granted"] is True
            assert row["action_type"] == "ENTRY"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_user_dossier_returns_history(seed_entry_logs):
    """
    The member dossier has the same serialization problem as the audit log, plus
    one of its own: the response carries `user`, but the query only eager-loaded
    location and worker. None of those relationships is lazy="selectin", so a
    missing eager load means MissingGreenlet rather than a clean error.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        member_id, member_email = await _register(ac, "Dossier", "Subject")

        await seed_entry_logs([
            {"user_id": member_id, "access_granted": True, "action_type": "ENTRY"},
            {"user_id": member_id, "access_granted": False, "action_type": "ENTRY",
             "reason": "Subscription expired"},
        ])

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            res = await ac.get(f"/api/admin/users/{member_id}/logs")
            assert res.status_code == 200, f"Dossier failed to serialize: {res.text[:200]}"

            body = res.json()
            assert set(body) == {"total", "items"}, "Dossier must use the paging envelope"
            assert body["total"] == 2

            logs = body["items"]
            assert len(logs) == 2

            # Newest first, so the denied attempt leads
            assert logs[0]["access_granted"] is False
            assert logs[0]["reason"] == "Subscription expired"
            assert logs[0]["user"]["email"] == member_email

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_peak_hours_buckets_only_granted_entries(seed_entry_logs):
    """
    Peak hours must return all 24 buckets and count only granted ENTRY scans.
    A denied scan never opened the door, and an EXIT would count one visit twice.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        member_id, _ = await _register(ac, "Peak", "Hours")

        # Pin everything to one hour yesterday so the assertions are exact. Using
        # "yesterday" keeps the rows inside the 7-day window without risking a
        # timestamp in the future if the test runs near midnight.
        base = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            before = await ac.get("/api/admin/analytics/peak-hours")
            assert before.status_code == 200
            baseline = {row["hour"]: row["count"] for row in before.json()}

            await seed_entry_logs([
                # Two that must count
                {"user_id": member_id, "access_granted": True, "action_type": "ENTRY", "timestamp": base},
                {"user_id": member_id, "access_granted": True, "action_type": "ENTRY",
                 "timestamp": base + timedelta(minutes=20)},
                # Denied: never came through the door
                {"user_id": member_id, "access_granted": False, "action_type": "ENTRY",
                 "timestamp": base + timedelta(minutes=30)},
                # An EXIT is the same visit leaving, not a new arrival
                {"user_id": member_id, "access_granted": True, "action_type": "EXIT",
                 "timestamp": base + timedelta(minutes=40)},
            ])

            res = await ac.get("/api/admin/analytics/peak-hours")
            assert res.status_code == 200

            data = res.json()
            assert len(data) == 24, "Every hour of the day should get a bucket, even at zero"
            assert [row["hour"] for row in data] == [f"{h:02d}:00" for h in range(24)]

            after = {row["hour"]: row["count"] for row in data}
            # Bucketed on the gym's clock, so the expected slot is the LOCAL hour
            # of the seeded moment, not its UTC hour
            bucket = f"{to_gym_time(base).hour:02d}:00"

            assert after[bucket] - baseline[bucket] == 2, \
                "Only the two granted ENTRY scans should have been counted"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_finances_shape():
    """
    The finance card must always answer with all three numbers. mrr is a float
    even with no subscriptions at all, where SUM() comes back NULL rather than 0.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            res = await ac.get("/api/admin/analytics/finances")
            assert res.status_code == 200

            data = res.json()
            assert set(data) == {"active_subscriptions", "total_users", "mrr"}
            assert isinstance(data["mrr"], float), "A NULL SUM must be coalesced, not passed through"
            assert data["mrr"] >= 0
            assert data["active_subscriptions"] >= 0
            # Registration happens in other tests, so the gym is definitely not empty
            assert data["total_users"] > 0

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_admin_search_finds_deactivated_and_staff(set_user_roles):
    """
    The admin search is deliberately NOT the worker search: it must return
    deactivated accounts and staff, because those are exactly the ones an audit
    is about. This is the inverse of test_worker.py's members-only assertion.
    """
    transport = ASGITransport(app=app)

    # A distinctive surname so the search cannot collide with other tests' users
    surname = f"Auditcase{uuid.uuid4().hex[:6]}"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        banned_id, banned_email = await _register(ac, "Banned", surname)
        trainer_id, trainer_email = await _register(ac, "Coach", surname)

        await set_user_roles(trainer_id, ["trainer"])

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            res = await ac.get(f"/api/admin/users/search?query={surname}")
            assert res.status_code == 200

            rows = {row["email"]: row for row in res.json()}

            # A trainer holds no member role at all - the worker search would skip them
            assert trainer_email in rows, "Staff must be searchable for auditing"
            assert rows[trainer_email]["roles"] == ["trainer"]

            assert banned_email in rows
            assert rows[banned_email]["full_name"] == f"Banned {surname}"
            assert rows[banned_email]["is_active"] is True

            # Every row carries the badge fields the dropdown renders
            for row in rows.values():
                assert "is_active" in row and isinstance(row["roles"], list)

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_dossier_paging_is_stable_across_pages(seed_entry_logs):
    """
    REGRESSION TEST: paging must not repeat or drop rows.

    Every seeded log shares one timestamp on purpose. Ordering by timestamp alone
    leaves the database free to return tied rows in any order it likes, so page 2
    can hand back a row page 1 already showed while another is never seen at all.
    The id tiebreaker is what makes the order total.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        member_id, _ = await _register(ac, "Paging", "Subject")

        # One timestamp, six rows - the worst case for an unstable sort
        tied = datetime.now(timezone.utc) - timedelta(hours=3)
        await seed_entry_logs([
            {"user_id": member_id, "access_granted": True, "action_type": "ENTRY", "timestamp": tied}
            for _ in range(6)
        ])

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            seen: list[int] = []
            for page in range(3):
                res = await ac.get(f"/api/admin/users/{member_id}/logs?skip={page * 2}&limit=2")
                assert res.status_code == 200

                body = res.json()
                assert body["total"] == 6, "total must count the whole history, not the page"
                assert len(body["items"]) == 2

                seen.extend(row["id"] for row in body["items"])

            assert len(seen) == 6
            assert len(set(seen)) == 6, f"A row appeared on more than one page: {seen}"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_peak_hours_buckets_in_gym_local_time(seed_entry_logs):
    """
    REGRESSION TEST: hours are bucketed on the gym's clock, not on UTC.

    Timestamps are stored in UTC, so reading .hour straight off the column
    reported an 18:00 Belgrade rush hour as 16:00 - and pushed anything just after
    local midnight into the previous evening.

    The expected bucket is computed with to_gym_time rather than hardcoded,
    because Belgrade is UTC+1 in winter and UTC+2 in summer; a literal would make
    this test fail twice a year.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        member_id, _ = await _register(ac, "Timezone", "Case")

        # 23:30 UTC yesterday, which is well into the NEXT day in Belgrade
        utc_moment = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=23, minute=30, second=0, microsecond=0
        )
        local_hour = to_gym_time(utc_moment).hour

        # If this ever stops being true the test has lost its point
        assert local_hour != utc_moment.hour, "Fixture must straddle a UTC/local hour boundary"

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            before = {r["hour"]: r["count"] for r in (await ac.get("/api/admin/analytics/peak-hours")).json()}

            await seed_entry_logs([
                {"user_id": member_id, "access_granted": True, "action_type": "ENTRY",
                 "timestamp": utc_moment},
            ])

            after = {r["hour"]: r["count"] for r in (await ac.get("/api/admin/analytics/peak-hours")).json()}

            local_bucket = f"{local_hour:02d}:00"
            utc_bucket = f"{utc_moment.hour:02d}:00"

            assert after[local_bucket] - before[local_bucket] == 1, \
                f"Entry should be counted in the local bucket {local_bucket}"
            assert after[utc_bucket] - before[utc_bucket] == 0, \
                f"Entry must NOT be counted in the raw UTC bucket {utc_bucket}"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_weekly_groups_by_local_day(seed_entry_logs):
    """
    The same boundary problem one level up: a scan at 01:00 local on Sunday is
    stored as Saturday 23:00 UTC, and used to be charted against Saturday.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        member_id, _ = await _register(ac, "Weekly", "Case")

        utc_moment = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=23, minute=30, second=0, microsecond=0
        )
        local_day = to_gym_time(utc_moment).strftime("%a")
        utc_day = utc_moment.strftime("%a")

        assert local_day != utc_day, "Fixture must straddle a UTC/local day boundary"

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            before = {r["day"]: r["entries"] for r in (await ac.get("/api/admin/analytics/weekly")).json()}

            await seed_entry_logs([
                {"user_id": member_id, "access_granted": True, "action_type": "ENTRY",
                 "timestamp": utc_moment},
            ])

            after = {r["day"]: r["entries"] for r in (await ac.get("/api/admin/analytics/weekly")).json()}

            assert after[local_day] - before[local_day] == 1, \
                f"Entry should be counted on the local day {local_day}"
            assert after[utc_day] - before[utc_day] == 0, \
                f"Entry must NOT be counted on the raw UTC day {utc_day}"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_admin_search_escapes_wildcards():
    """
    A lone '%' must not match every user in the database. build_like_pattern
    escapes it; this is the shared helper worker.py relies on too.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            res = await ac.get("/api/admin/users/search?query=%25%25")  # "%%" url-encoded
            assert res.status_code == 200
            assert res.json() == [], "Wildcards leaked into the LIKE pattern"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)
