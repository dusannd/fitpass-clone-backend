"""
Create (or promote) an administrator.

WHY THIS EXISTS
A freshly migrated database has no users at all, and every admin screen is
behind `RequireRole allowed={["admin"]}`. Registering through the UI only ever
produces a member, and there is no endpoint that hands out the admin role -
deliberately, since that would be a privilege-escalation hole. So without this
script a brand new deployment is a locked building with the key inside.

HOW TO RUN IT (from the backend repo, with the stack up)

    docker compose --env-file .env.prod -f docker-compose.prod.yml \
        run --rm backend python scripts/create_admin.py

It prompts for the details. To script it instead, pass them as arguments:

    ... python scripts/create_admin.py admin@gym.rs 'the-password' Ana Anic

It is IDEMPOTENT: run it against an existing address and it just grants that
account the admin role, leaving the password alone. That is the normal way to
promote yourself after registering through the UI.
"""

import asyncio
import getpass
import sys
from pathlib import Path

# The container runs this as `python scripts/create_admin.py`, which puts
# scripts/ on sys.path rather than the project root, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash

# Every model module has to be imported before the first query, even the ones
# this script never touches. User.subscriptions names its target as the STRING
# "UserSubscription", and SQLAlchemy can only resolve that once the class has
# actually been defined somewhere - otherwise mapper configuration dies with
# "failed to locate a name". alembic/env.py imports the same list for the same
# reason.
from app.models import access, coaching, subscription, user, workout  # noqa: F401
from app.models.user import Role, User

ADMIN_ROLE = "admin"
MIN_PASSWORD_LENGTH = 8


def _ask(prompt: str, argv_value: str | None, required: bool = False) -> str:
    """
    Take the value from the command line if it is there, otherwise prompt.

    There is often nothing to prompt: `docker compose run -T`, a cron job and a
    CI step all hand this script a closed stdin, and input() there raises
    EOFError rather than returning empty. So when nobody is listening, an
    optional field just stays blank instead of crashing the run.
    """
    if argv_value:
        return argv_value
    if not sys.stdin.isatty():
        if required:
            print(f"error: no terminal to ask for '{prompt.strip()}' - pass it as an argument.", file=sys.stderr)
            raise SystemExit(1)
        return ""
    return input(prompt).strip()


async def create_admin(email: str, password: str, first_name: str, last_name: str) -> None:
    async with AsyncSessionLocal() as db:
        # --- 1. The role row itself may not exist yet ---------------------------
        # Roles are seeded by whatever registers the first member, so on a
        # database nobody has touched there is no `admin` row to attach to.
        result = await db.execute(select(Role).where(Role.name == ADMIN_ROLE))
        admin_role = result.scalars().first()
        if admin_role is None:
            admin_role = Role(name=ADMIN_ROLE, description="Full administrative access")
            db.add(admin_role)
            await db.flush()
            print(f"  created the '{ADMIN_ROLE}' role")

        # --- 2. Promote an existing account, or build a new one -----------------
        # `roles` is lazy="selectin" and therefore safe to read, but this query
        # loads it explicitly so the intent survives a change to the model.
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(User.email == email)
        )
        user = result.scalars().first()

        if user is None:
            user = User(
                email=email,
                password_hash=get_password_hash(password),
                first_name=first_name or None,
                last_name=last_name or None,
                is_active=True,
                # Skip the email round trip: this account has to be able to log
                # in before outgoing mail is necessarily working.
                is_verified=True,
            )
            user.roles.append(admin_role)
            db.add(user)
            action = "created"
        else:
            if any(r.name == ADMIN_ROLE for r in user.roles):
                print(f"'{email}' is already an admin - nothing to do.")
                return
            user.roles.append(admin_role)
            action = "promoted"

        await db.commit()

    print(f"{action}: {email} is now an admin.")


def main() -> int:
    args = sys.argv[1:]

    email = _ask("Email: ", args[0] if len(args) > 0 else None, required=True)

    # The same validator the API uses (schemas/user.py types these as EmailStr).
    # Checking it here rather than settling for an "@" is the difference between
    # a clear error now and an admin account that exists but can never log in -
    # reserved domains like example.local sail past a naive check and are then
    # rejected at /login, which reads as a broken password.
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if len(args) > 1:
        password = args[1]
    elif not sys.stdin.isatty():
        # No terminal: this can still be a promotion of an existing account,
        # which needs no password at all.
        password = ""
    else:
        # getpass keeps the password out of the terminal and out of shell
        # history; the confirmation catches a typo you would otherwise only
        # discover at the login screen.
        password = getpass.getpass("Password (leave blank if the account exists): ")
        if password and password != getpass.getpass("Repeat password: "):
            print("The passwords do not match.", file=sys.stderr)
            return 1

    if password and len(password) < MIN_PASSWORD_LENGTH:
        print(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1

    first_name = _ask("First name (optional): ", args[2] if len(args) > 2 else None)
    last_name = _ask("Last name (optional): ", args[3] if len(args) > 3 else None)

    asyncio.run(create_admin(email, password, first_name, last_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
