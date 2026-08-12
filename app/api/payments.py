import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from starlette.concurrency import run_in_threadpool
from datetime import datetime, timezone

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
                        'recurring': {'interval': 'month'},  # Assuming standard monthly billing
                        'product_data': {
                            'name': plan.name,
                            'description': plan.description or "Gym Subscription",
                        },
                    },
                    'quantity': 1,
                }
            ],
            mode='subscription',
            # Dynamically construct redirect URLs based on the environment
            success_url=f"{settings.FRONTEND_URL}/dashboard?payment=success",
            cancel_url=f"{settings.FRONTEND_URL}/subscriptions?payment=cancelled",

            # CRITICAL: For subscription mode, top-level `metadata` only lands on the
            # Checkout Session itself. `subscription_data.metadata` is what propagates
            # onto the actual Stripe Subscription object AND every future invoice it
            # generates, which is what our recurring `invoice.payment_succeeded`
            # webhook needs to identify WHO paid for WHAT on renewal.
            subscription_data={
                "metadata": {
                    "user_id": user_id,
                    "plan_id": plan.id
                }
            }
        )

        # Return the secure Stripe URL to the frontend
        return {"checkout_url": checkout_session.url}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/customer-portal")
async def create_customer_portal_session(
        db: AsyncSession = Depends(get_db),
        user_id: int = Depends(get_current_user_id)
):
    """
    Creates a Stripe Billing Portal session for the caller's active subscription.

    The portal is Stripe's own hosted page: cancelling, swapping the card on file
    and downloading invoices all happen there, so we never handle card data.
    Returns the one-time URL the frontend redirects to.
    """
    # 1. Find the caller's active subscription. Same ordering as
    #    GET /subscriptions/my-subscription so both endpoints agree on which row
    #    is "the" active one if a user somehow ends up holding two.
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(UserSubscription)
        .where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
        .order_by(UserSubscription.end_date.desc())
    )
    active_sub = result.scalars().first()

    if not active_sub:
        raise HTTPException(
            status_code=400,
            detail="You don't have an active subscription to manage."
        )

    # 2. Legacy rows (and anything the desk activated by hand) have no Stripe
    #    subscription behind them, so there is no portal to send them to.
    if not active_sub.stripe_subscription_id:
        raise HTTPException(
            status_code=400,
            detail="This subscription wasn't created through Stripe, so it can't be managed online."
        )

    try:
        # 3. We don't store stripe_customer_id, so read it off the subscription.
        #    Both Stripe calls go through run_in_threadpool: the SDK is synchronous
        #    `requests` under the hood, and calling it bare inside an async route
        #    blocks the entire event loop for two network round trips.
        stripe_sub = await run_in_threadpool(
            stripe.Subscription.retrieve, active_sub.stripe_subscription_id
        )
        customer_id = stripe_sub.customer

        # 4. Mint the session. return_url is where Stripe's "Back to..." link goes.
        portal_session = await run_in_threadpool(
            lambda: stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=f"{settings.FRONTEND_URL}/subscriptions"
            )
        )
    except stripe.StripeError:
        # Stripe being unreachable is an upstream failure, not a bug on our side -
        # a 502 says that honestly and keeps it out of our 5xx error budget.
        raise HTTPException(
            status_code=502,
            detail="Could not reach Stripe right now. Please try again in a moment."
        )

    return {"url": portal_session.url}


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

    # 3. Handle recurring subscription events
    if event['type'] == 'invoice.payment_succeeded':
        invoice = event['data']['object']

        # Extract the subscription's Stripe ID. Newer Stripe API versions nest this
        # under `parent.subscription_details.subscription` instead of the legacy
        # top-level `subscription` field, so we fall back between the two.
        stripe_subscription_id = getattr(invoice, "subscription", None)
        if not stripe_subscription_id:
            parent = getattr(invoice, "parent", None)
            nested_details = getattr(parent, "subscription_details", None) if parent else None
            stripe_subscription_id = getattr(nested_details, "subscription", None) if nested_details else None

        # The first (and only, for our single-item checkout) invoice line item.
        # Used both as a metadata fallback below and as the source of truth for
        # the billing period end.
        lines = getattr(invoice, "lines", None)
        line_items = getattr(lines, "data", []) if lines else []
        first_line = line_items[0] if line_items else None

        # Extract the metadata we stamped via `subscription_data.metadata` at checkout
        # time. It's mirrored onto `invoice.subscription_details.metadata` for every
        # invoice (including renewals); fall back to the first line item's metadata
        # if that field is unavailable on this API version.
        subscription_details = getattr(invoice, "subscription_details", None)
        metadata = getattr(subscription_details, "metadata", None) if subscription_details else None

        if not metadata and first_line:
            metadata = first_line.get("metadata")

        # IDEMPOTENCY: Stripe can (and does) redeliver the same webhook event on
        # network retries. Incrementing end_date by a duration would double-grant
        # days on a duplicate delivery, so instead we pull the ABSOLUTE period-end
        # timestamp Stripe already computed for this invoice and set end_date to
        # it directly. Replaying the same event N times converges to the same
        # end_date instead of compounding it.
        period = getattr(first_line, "period", None) if first_line else None
        stripe_end_timestamp = getattr(period, "end", None) if period else None

        if (
            metadata and stripe_subscription_id and stripe_end_timestamp
            and "user_id" in metadata and "plan_id" in metadata
        ):
            user_id = int(metadata["user_id"])
            plan_id = int(metadata["plan_id"])
            end_date = datetime.fromtimestamp(stripe_end_timestamp, timezone.utc)

            # Confirm the plan still exists
            result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
            plan = result.scalars().first()

            if plan:
                existing_result = await db.execute(
                    select(UserSubscription).where(
                        UserSubscription.stripe_subscription_id == stripe_subscription_id
                    )
                )
                existing_sub = existing_result.scalars().first()

                if existing_sub:
                    # RENEWAL (or a duplicate redelivery of the same invoice event):
                    # overwrite end_date with Stripe's own period end rather than
                    # incrementing it, so replays are idempotent.
                    existing_sub.end_date = end_date
                    existing_sub.is_active = 1
                    db.add(existing_sub)
                else:
                    # FIRST PAYMENT: brand-new Stripe subscription, create our row.
                    new_sub = UserSubscription(
                        user_id=user_id,
                        plan_id=plan.id,
                        start_date=datetime.now(timezone.utc),
                        end_date=end_date,
                        is_active=1,
                        stripe_subscription_id=stripe_subscription_id
                    )
                    db.add(new_sub)

                await db.commit()

    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        stripe_subscription_id = subscription.id

        result = await db.execute(
            select(UserSubscription).where(
                UserSubscription.stripe_subscription_id == stripe_subscription_id
            )
        )
        user_sub = result.scalars().first()

        if user_sub:
            user_sub.is_active = 0
            db.add(user_sub)
            await db.commit()

    # Always return a 200 OK so Stripe knows we received the message
    return {"status": "success"}