import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.api.dependencies import get_current_user_id
from app.models.subscription import SubscriptionPlan, UserSubscription

# NOVO: Uvozimo settings
from app.core.config import settings

# Load Stripe keys directly from validated Pydantic settings
stripe.api_key = settings.STRIPE_API_KEY
STRIPE_WEBHOOK_SECRET = settings.STRIPE_WEBHOOK_SECRET

router = APIRouter()

@router.post("/checkout-session")
async def create_checkout_session(
        plan_id: int,
        db: AsyncSession = Depends(get_db),
        user_id: int = Depends(get_current_user_id)
):
    """
    Creates a Stripe Checkout Session for a specific subscription plan.
    Returns the URL where the user can securely enter their credit card.
    """
    # 1. Fetch the requested plan from the database
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    plan = result.scalars().first()

    if not plan or not plan.is_active:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

    # 2. Prevent creating a checkout session if the user already has an active subscription
    now = datetime.now(timezone.utc)
    from sqlalchemy import and_
    active_sub_check = await db.execute(
        select(UserSubscription).where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
    )
    if active_sub_check.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="You already have an active subscription."
        )

    # 3. Create the Stripe Checkout Session
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price_data': {
                        'currency': 'rsd',  # Using Serbian Dinar (or 'usd', 'eur')
                        'unit_amount': int(plan.price * 100),  # Stripe requires the price in cents (para)
                        'product_data': {
                            'name': plan.name,
                            'description': plan.description or "Gym Subscription",
                        },
                    },
                    'quantity': 1,
                }
            ],
            mode='payment',
            # URLs where Stripe will redirect the user after payment
            success_url="http://localhost:5173/member/dashboard?payment=success",
            cancel_url="http://localhost:5173/member/dashboard?payment=cancelled",

            # CRITICAL: We attach user_id and plan_id to metadata.
            # Stripe will send this back to our webhook so we know WHO paid for WHAT.
            metadata={
                "user_id": user_id,
                "plan_id": plan.id
            }
        )

        # Return the secure Stripe URL to the frontend
        return {"checkout_url": checkout_session.url}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook")
async def stripe_webhook(
        request: Request,
        stripe_signature: str = Header(None, alias="Stripe-Signature"),
        db: AsyncSession = Depends(get_db)
):
    """
    Webhook endpoint. Stripe sends a POST request here when a payment is successful.
    We verify the signature to ensure hackers aren't sending fake payment confirmations.
    """
    # 1. Read the raw body of the request (Required by Stripe for signature verification)
    payload = await request.body()

    try:
        # 2. Verify that the event actually came from Stripe
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")

    # 3. Handle the successful payment event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']

        # Extract the metadata we attached during checkout creation
        user_id = int(session.metadata.user_id)
        plan_id = int(session.metadata.plan_id)

        # Fetch the plan to calculate the expiration date
        result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
        plan = result.scalars().first()

        if plan:
            # Grant the subscription to the user in our database
            start_date = datetime.now(timezone.utc)
            end_date = start_date + timedelta(days=plan.duration_days)

            new_sub = UserSubscription(
                user_id=user_id,
                plan_id=plan.id,
                start_date=start_date,
                end_date=end_date,
                is_active=1
            )
            db.add(new_sub)
            await db.commit()

    # Always return a 200 OK so Stripe knows we received the message
    return {"status": "success"}