from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, and_
from typing import List
from datetime import datetime, timedelta, timezone


from app.core.database import get_db
from app.api.dependencies import RequireRole
from app.models.coaching import TrainerClientLink, Appointment
from app.models.user import User, Role
from app.models.workout import WorkoutSession, ExerciseLog
from app.schemas.coaching import CoachingRequestUpdate, TrainerClientLinkResponse, AppointmentCreate, AppointmentUpdate, AppointmentResponse
from app.schemas.workout import WorkoutSessionResponse

router = APIRouter()

get_current_member = RequireRole("member")
get_current_trainer = RequireRole("trainer")

# How far ahead a member is allowed to book. Keeps a trainer's calendar plannable
# and stops somebody parking a slot years out.
MAX_BOOKING_HORIZON_DAYS = 60


def as_utc(value: datetime) -> datetime:
    """
    Force a datetime to be timezone-aware (assuming UTC when it is not).

    Needed in two places:
      - Incoming payloads: 'start_time' is a bare datetime, so Pydantic happily
        accepts a naive string like "2026-09-01T10:00:00". Comparing that to an
        aware now() raises TypeError, which would surface as a 500.
      - Rows read back out: the columns are DateTime(timezone=True), which returns
        aware values on Postgres but NAIVE ones on SQLite - and the test suite runs
        on SQLite.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@router.post("/request/{trainer_id}")
async def request_coaching(
        trainer_id: int,
        db: AsyncSession = Depends(get_db),
        client_id: int = Depends(get_current_member)
):
    """
    Member Route: Send a 1-on-1 coaching request to a specific trainer.
    """
    # 1. Verify if the target user is actually a trainer
    stmt = select(User).join(User.roles).where(User.id == trainer_id, Role.name == "trainer")
    result = await db.execute(stmt)
    trainer = result.scalars().first()

    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    # 2. Check if a link already exists (prevent spamming requests)
    existing_link_stmt = select(TrainerClientLink).where(
        TrainerClientLink.client_id == client_id,
        TrainerClientLink.trainer_id == trainer_id
    )
    existing_link_result = await db.execute(existing_link_stmt)
    existing_link = existing_link_result.scalars().first()

    if existing_link:
        raise HTTPException(
            status_code=400,
            detail=f"Request already exists with status: {existing_link.status}"
        )

    # 3. Create a new pending request
    new_request = TrainerClientLink(
        trainer_id=trainer_id,
        client_id=client_id,
        status="PENDING"
    )

    db.add(new_request)
    await db.commit()

    return {"status": "success", "message": "Coaching request sent successfully"}


@router.get("/requests", response_model=List[TrainerClientLinkResponse])
async def get_pending_requests(
        db: AsyncSession = Depends(get_db),
        trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Route: View all pending coaching requests from members.
    """
    stmt = (
        select(TrainerClientLink)
        .options(
            # NEW: chained load so we also get the client's bio/goals in one go
            selectinload(TrainerClientLink.client).selectinload(User.profile),
            selectinload(TrainerClientLink.trainer).selectinload(User.profile)  # <--- FIX: Eager load trainer too
        )
        .where(
            TrainerClientLink.trainer_id == trainer_id,
            TrainerClientLink.status == "PENDING"
        )
    )
    result = await db.execute(stmt)
    return result.scalars().all()

@router.put("/requests/{request_id}")
async def respond_to_request(
        request_id: int,
        payload: CoachingRequestUpdate,
        db: AsyncSession = Depends(get_db),
        trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Route: Accept or Reject a client's coaching request.
    """
    if payload.status not in ["ACCEPTED", "REJECTED"]:
        raise HTTPException(status_code=400, detail="Invalid status. Use 'ACCEPTED' or 'REJECTED'")

    stmt = select(TrainerClientLink).where(
        TrainerClientLink.id == request_id,
        TrainerClientLink.trainer_id == trainer_id
    )
    result = await db.execute(stmt)
    link = result.scalars().first()

    if not link:
        raise HTTPException(status_code=404, detail="Request not found")

    # Update the status
    link.status = payload.status
    db.add(link)
    await db.commit()

    return {"status": "success", "message": f"Request updated to {payload.status}"}


@router.get("/clients", response_model=List[TrainerClientLinkResponse])
async def get_my_clients(
        db: AsyncSession = Depends(get_db),
        trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Route: Get a list of all actively accepted clients.
    """
    stmt = (
        select(TrainerClientLink)
        .options(
            # NEW: chained load so the client cards can show bio/goals
            selectinload(TrainerClientLink.client).selectinload(User.profile),
            selectinload(TrainerClientLink.trainer).selectinload(User.profile)  # <--- FIX: Eager load trainer too
        )
        .where(
            TrainerClientLink.trainer_id == trainer_id,
            TrainerClientLink.status == "ACCEPTED"
        )
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/clients/{client_id}/progress", response_model=List[WorkoutSessionResponse])
async def get_client_progress(
        client_id: int,
        db: AsyncSession = Depends(get_db),
        trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Route: Read one client's workout history so the trainer can see their
    progress without having to ask them for numbers.
    """
    # 1. SECURITY CHECK: a trainer may only look at clients who accepted them.
    # Without this any trainer could read any member's training data by guessing an id.
    stmt_link = select(TrainerClientLink).where(
        TrainerClientLink.trainer_id == trainer_id,
        TrainerClientLink.client_id == client_id,
        TrainerClientLink.status == "ACCEPTED"
    )
    result_link = await db.execute(stmt_link)
    if not result_link.scalars().first():
        raise HTTPException(
            status_code=403,
            detail="You can only view the progress of clients who accepted your coaching."
        )

    # 2. Same query the member runs on their own history, just for someone else's id.
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.exercise_logs)
            .selectinload(ExerciseLog.exercise)
        )
        .where(WorkoutSession.user_id == client_id)
        .order_by(WorkoutSession.date.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ==========================================
# APPOINTMENTS (SCHEDULING)
# ==========================================

@router.post("/appointments", response_model=AppointmentResponse)
async def schedule_appointment(
        payload: AppointmentCreate,
        db: AsyncSession = Depends(get_db),
        client_id: int = Depends(get_current_member)
):
    """
    Member Route: Schedule a 1-on-1 session with a trainer (with strict validation).
    """
    now = datetime.now(timezone.utc)

    # Normalise first, so a client that sends a naive timestamp gets a proper 400
    # from the checks below instead of a 500 out of the comparison itself.
    start_time = as_utc(payload.start_time)
    end_time = as_utc(payload.end_time)

    # 1. Cannot schedule in the past
    if start_time < now:
        raise HTTPException(status_code=400, detail="Cannot schedule an appointment in the past.")

    # 2. Cannot schedule beyond the booking horizon
    if start_time > now + timedelta(days=MAX_BOOKING_HORIZON_DAYS):
        raise HTTPException(
            status_code=400,
            detail=f"Appointments can only be booked up to {MAX_BOOKING_HORIZON_DAYS} days in advance."
        )

    # 3. End time must be strictly after start time
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time.")

    # 4. Session duration limit (Max 3 hours / 180 minutes)
    duration = end_time - start_time
    if duration.total_seconds() > (3 * 3600):
        raise HTTPException(status_code=400, detail="A single session cannot exceed 3 hours.")

    # 5. Security Check: Ensure the client is officially assigned to this trainer
    stmt_link = select(TrainerClientLink).where(
        TrainerClientLink.client_id == client_id,
        TrainerClientLink.trainer_id == payload.trainer_id,
        TrainerClientLink.status == "ACCEPTED"
    )
    result_link = await db.execute(stmt_link)
    link = result_link.scalars().first()

    if not link:
        raise HTTPException(
            status_code=403,
            detail="You can only schedule appointments with trainers who have accepted your request."
        )

    # 6. OVERBOOKING PROTECTION: Prevent overlapping sessions for the same trainer
    # Overlap logic: (NewStart < ExistingEnd) AND (NewEnd > ExistingStart)
    stmt_overlap = select(Appointment).where(
        Appointment.trainer_id == payload.trainer_id,
        Appointment.status == "SCHEDULED",
        and_(
            start_time < Appointment.end_time,
            end_time > Appointment.start_time
        )
    )
    overlap_result = await db.execute(stmt_overlap)
    if overlap_result.scalars().first():
        raise HTTPException(
            status_code=409,  # 409 Conflict
            detail="The trainer already has another session booked at this time."
        )

    # 7. Create the appointment (storing the normalised, timezone-aware values)
    new_appointment = Appointment(
        trainer_id=payload.trainer_id,
        client_id=client_id,
        start_time=start_time,
        end_time=end_time,
        status="SCHEDULED"
    )
    db.add(new_appointment)
    await db.commit()

    # 8. Reload with relationships eager-loaded for the Pydantic response
    stmt_reload = (
        select(Appointment)
        .options(
            selectinload(Appointment.trainer).selectinload(User.profile),
            selectinload(Appointment.client).selectinload(User.profile)
        )
        .where(Appointment.id == new_appointment.id)
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalars().first()


@router.get("/appointments/trainer", response_model=List[AppointmentResponse])
async def get_trainer_appointments(
    db: AsyncSession = Depends(get_db),
    trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Route: View all scheduled and past appointments.
    """
    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.client).selectinload(User.profile),
            selectinload(Appointment.trainer).selectinload(User.profile)
        )
        .where(Appointment.trainer_id == trainer_id)
        .order_by(Appointment.start_time.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/appointments/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment_status(
    appointment_id: int,
    payload: AppointmentUpdate,
    db: AsyncSession = Depends(get_db),
    trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Route: Complete or cancel an appointment and optionally add notes.

    Notes follow partial-update semantics: omit the key to leave existing feedback
    alone, send text to replace it, send an explicit null to clear it.
    """
    if payload.status not in ["COMPLETED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail="Status must be COMPLETED or CANCELLED.")

    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.client).selectinload(User.profile),
            selectinload(Appointment.trainer).selectinload(User.profile)
        )
        .where(
            Appointment.id == appointment_id,
            Appointment.trainer_id == trainer_id
        )
    )
    result = await db.execute(stmt)
    appointment = result.scalars().first()

    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    # A session can only be closed out once it has actually begun. We gate on
    # start_time rather than end_time so a trainer who wraps up early doesn't have
    # to sit and wait for the clock.
    # CANCELLED is deliberately NOT gated - cancelling a future session is the
    # entire point of cancelling.
    if payload.status == "COMPLETED" and datetime.now(timezone.utc) < as_utc(appointment.start_time):
        raise HTTPException(
            status_code=400,
            detail="Cannot complete a session that hasn't happened yet."
        )

    appointment.status = payload.status

    # 'notes' used to be assigned unconditionally, so a plain status change with no
    # notes attached silently erased whatever feedback was already there - and that
    # text is shown to the member as "Trainer's Note". exclude_unset lets us tell
    # "not sent" (leave it) apart from an explicit null (a deliberate erase).
    update_data = payload.model_dump(exclude_unset=True)
    if "notes" in update_data:
        appointment.notes = update_data["notes"]

    db.add(appointment)
    await db.commit()

    return appointment


@router.get("/my-trainers", response_model=List[TrainerClientLinkResponse])
async def get_my_trainers(
        db: AsyncSession = Depends(get_db),
        client_id: int = Depends(get_current_member)
):
    """
    Member Route: View all trainers I have requested or am coached by.
    """
    stmt = (
        select(TrainerClientLink)
        .options(
            # NEW: chained load so we also pull the trainer's profile (bio/goals)
            selectinload(TrainerClientLink.trainer).selectinload(User.profile),
            selectinload(TrainerClientLink.client).selectinload(User.profile) # <-- OVO NAM JE FALILO! Pydantic je pokušavao da učita ovo naknadno i pucao.
        )
        .where(TrainerClientLink.client_id == client_id)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/appointments/client", response_model=List[AppointmentResponse])
async def get_client_appointments(
    db: AsyncSession = Depends(get_db),
    client_id: int = Depends(get_current_member)
):
    """
    Member Route: View all my scheduled and past appointments.
    """
    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.client).selectinload(User.profile),
            selectinload(Appointment.trainer).selectinload(User.profile)
        )
        .where(Appointment.client_id == client_id)
        .order_by(Appointment.start_time.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()