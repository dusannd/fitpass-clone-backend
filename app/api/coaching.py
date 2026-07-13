from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List

from app.core.database import get_db
from app.api.dependencies import RequireRole
from app.models.coaching import TrainerClientLink
from app.models.user import User, Role
from app.schemas.coaching import CoachingRequestUpdate, TrainerClientLinkResponse

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