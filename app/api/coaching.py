from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List

from app.core.database import get_db
from app.api.dependencies import RequireRole
from app.models.coaching import TrainerClientLink, Appointment
from app.models.user import User, Role
from app.schemas.coaching import CoachingRequestUpdate, TrainerClientLinkResponse, AppointmentCreate, AppointmentUpdate, AppointmentResponse

router = APIRouter()

get_current_member = RequireRole("member")
get_current_trainer = RequireRole("trainer")


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
            selectinload(TrainerClientLink.client),
            selectinload(TrainerClientLink.trainer)  # <--- FIX: Eager load trainer too
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
            selectinload(TrainerClientLink.client),
            selectinload(TrainerClientLink.trainer)  # <--- FIX: Eager load trainer too
        )
        .where(
            TrainerClientLink.trainer_id == trainer_id,
            TrainerClientLink.status == "ACCEPTED"
        )
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
    Member Route: Schedule a 1-on-1 session with a trainer.
    """
    # 1. Ensure the member is actually an accepted client of this trainer
    stmt = select(TrainerClientLink).where(
        TrainerClientLink.client_id == client_id,
        TrainerClientLink.trainer_id == payload.trainer_id,
        TrainerClientLink.status == "ACCEPTED"
    )
    result = await db.execute(stmt)
    link = result.scalars().first()

    if not link:
        raise HTTPException(
            status_code=403,
            detail="You can only schedule appointments with trainers who have accepted your coaching request."
        )

    # 2. Basic time validation (End time must be after start time)
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time.")

    # 3. Create the appointment
    new_appointment = Appointment(
        trainer_id=payload.trainer_id,
        client_id=client_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        status="SCHEDULED"
    )
    db.add(new_appointment)
    await db.commit()

    # 4. Reload to eager-load relationships for the response
    stmt_reload = (
        select(Appointment)
        .options(selectinload(Appointment.trainer), selectinload(Appointment.client))
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
        .options(selectinload(Appointment.client), selectinload(Appointment.trainer))
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
    """
    if payload.status not in ["COMPLETED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail="Status must be COMPLETED or CANCELLED.")

    stmt = (
        select(Appointment)
        .options(selectinload(Appointment.client), selectinload(Appointment.trainer))
        .where(
            Appointment.id == appointment_id,
            Appointment.trainer_id == trainer_id
        )
    )
    result = await db.execute(stmt)
    appointment = result.scalars().first()

    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    appointment.status = payload.status
    appointment.notes = payload.notes

    db.add(appointment)
    await db.commit()

    return appointment