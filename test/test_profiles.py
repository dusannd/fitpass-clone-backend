import io
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from PIL import Image

from app.main import app
from app.services.storage import AVATAR_DIR

transport = ASGITransport(app=app)


def unique_email(prefix: str) -> str:
    # @test.com is auto verified when settings.TESTING = True
    return f"{prefix}_{uuid.uuid4().hex[:6]}@test.com"


def make_image_bytes(size=(800, 400), fmt="PNG") -> bytes:
    """Builds a real image in memory, so we don't have to keep files in the repo."""
    buf = io.BytesIO()
    Image.new("RGB", size, (12, 110, 220)).save(buf, format=fmt)
    return buf.getvalue()


async def register_and_login(ac: AsyncClient, email: str, profile=None) -> int:
    """
    Registers a user and logs them in.
    The token arrives as an httpOnly cookie, and AsyncClient remembers it
    for the following calls on its own.
    """
    password = "testpassword123"

    payload = {
        "email": email,
        "password": password,
        "first_name": "Test",
        "last_name": "User",
    }
    if profile is not None:
        payload["profile"] = profile

    res = await ac.post("/api/users/", json=payload)
    assert res.status_code == 200, res.text
    user_id = res.json()["id"]

    res_login = await ac.post("/api/users/login", json={"email": email, "password": password})
    assert res_login.status_code == 200, res_login.text

    return user_id


# ==========================================
# REGISTRATION WITH A PROFILE
# ==========================================

@pytest.mark.asyncio
async def test_register_with_nested_profile():
    """Bio and goals sent at registration must be saved and returned back."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        email = unique_email("withprofile")

        res = await ac.post("/api/users/", json={
            "email": email,
            "password": "testpassword123",
            "first_name": "Gym",
            "last_name": "Bro",
            "profile": {
                "bio": "Complete beginner, training 3x a week.",
                "fitness_goals": "Lose weight, Build muscle",
            },
        })

        assert res.status_code == 200, res.text
        profile = res.json()["profile"]

        assert profile is not None
        assert profile["bio"] == "Complete beginner, training 3x a week."
        assert profile["fitness_goals"] == "Lose weight, Build muscle"
        # We did not send a picture during registration
        assert profile["profile_picture_url"] is None


@pytest.mark.asyncio
async def test_register_without_profile_returns_null():
    """The profile is optional - registration must still go through without it."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/api/users/", json={
            "email": unique_email("noprofile"),
            "password": "testpassword123",
            "first_name": "Plain",
            "last_name": "User",
        })

        assert res.status_code == 200, res.text
        assert res.json()["profile"] is None


# ==========================================
# PROFILE UPDATE
# ==========================================

@pytest.mark.asyncio
async def test_update_profile_creates_row_for_old_account():
    """
    Accounts created before profiles existed have no row in the database.
    The PUT has to create it on the fly instead of blowing up with a 404.
    """
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("legacy"))

        res = await ac.put("/api/users/me/profile", json={
            "bio": "Added this later.",
            "fitness_goals": "Run a 5k",
        })

        assert res.status_code == 200, res.text
        assert res.json()["bio"] == "Added this later."

        # And /me has to see it now as well
        res_me = await ac.get("/api/users/me")
        assert res_me.json()["profile"]["fitness_goals"] == "Run a 5k"


@pytest.mark.asyncio
async def test_update_profile_is_partial():
    """A field that was NOT sent has to stay untouched."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("partial"), profile={
            "bio": "Original bio",
            "fitness_goals": "Original goals",
        })

        # We only send the bio
        res = await ac.put("/api/users/me/profile", json={"bio": "Updated bio"})

        assert res.status_code == 200, res.text
        assert res.json()["bio"] == "Updated bio"
        assert res.json()["fitness_goals"] == "Original goals"  # not touched


@pytest.mark.asyncio
async def test_empty_string_clears_the_field():
    """An empty textarea from the frontend ("") should be saved as NULL, not as an empty string."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("clearme"), profile={"bio": "Something"})

        res = await ac.put("/api/users/me/profile", json={"bio": "   "})

        assert res.status_code == 200, res.text
        assert res.json()["bio"] is None


@pytest.mark.asyncio
async def test_profile_endpoints_require_login():
    """No cookie, no profile."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.put("/api/users/me/profile", json={"bio": "hax"})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_bio_over_max_length_is_rejected():
    """Pydantic has to reject a bio longer than 2000 characters."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("toolong"))

        res = await ac.put("/api/users/me/profile", json={"bio": "x" * 2001})
        assert res.status_code == 422


# ==========================================
# PROFILE PICTURE
# ==========================================

@pytest.mark.asyncio
async def test_avatar_upload_and_delete():
    """
    Uploading a real image has to: store the file on disk and return a URL,
    and the DELETE after it has to clean up both the database and the disk.
    """
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("avatar"))

        res = await ac.post(
            "/api/users/me/avatar",
            files={"file": ("selfie.png", make_image_bytes(), "image/png")},
        )

        assert res.status_code == 200, res.text
        url = res.json()["profile_picture_url"]
        assert url.startswith("/static/avatars/")

        stored = AVATAR_DIR / url.split("/")[-1]
        try:
            # We always store a square JPEG, no matter what came in
            assert stored.exists()
            assert stored.suffix == ".jpg"
            with Image.open(stored) as img:
                assert img.format == "JPEG"
                assert img.size == (512, 512)

            # Deleting
            res_del = await ac.delete("/api/users/me/avatar")
            assert res_del.status_code == 200, res_del.text
            assert res_del.json()["profile_picture_url"] is None
            assert not stored.exists()
        finally:
            stored.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_avatar_upload_replaces_old_file():
    """When you upload a new picture, the old one must not stay lying on the disk."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("replace"))

        first = (await ac.post(
            "/api/users/me/avatar",
            files={"file": ("a.png", make_image_bytes(), "image/png")},
        )).json()["profile_picture_url"]

        second = (await ac.post(
            "/api/users/me/avatar",
            files={"file": ("b.png", make_image_bytes(size=(300, 900)), "image/png")},
        )).json()["profile_picture_url"]

        old_file = AVATAR_DIR / first.split("/")[-1]
        new_file = AVATAR_DIR / second.split("/")[-1]

        try:
            assert first != second
            assert not old_file.exists()
            assert new_file.exists()
        finally:
            old_file.unlink(missing_ok=True)
            new_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_avatar_rejects_non_image():
    """
    A file that only pretends to be an image has to be rejected.
    The filename and the content-type come from the client, so we don't trust them.
    """
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("notimage"))

        res = await ac.post(
            "/api/users/me/avatar",
            files={"file": ("evil.png", b"<svg onload=alert(1)></svg>", "image/png")},
        )

        assert res.status_code == 400
        assert "not a valid image" in res.json()["detail"]


@pytest.mark.asyncio
async def test_avatar_rejects_oversized_file():
    """Anything over 2MB gets a 413, so nobody can fill up our disk."""
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await register_and_login(ac, unique_email("toobig"))

        res = await ac.post(
            "/api/users/me/avatar",
            files={"file": ("huge.png", b"x" * (3 * 1024 * 1024), "image/png")},
        )

        assert res.status_code == 413


@pytest.mark.asyncio
async def test_avatar_upload_requires_login():
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            "/api/users/me/avatar",
            files={"file": ("a.png", make_image_bytes(), "image/png")},
        )
        assert res.status_code == 401
