"""Helpers shared by views and admin actions for the business app."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from subscribers.models import (
    AccountStatus,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    Subscription,
)

from .models import (
    Notification,
    PackageRequest,
    PackageRequestStatus,
    PaymentChannel,
)


_CHANNEL_TO_PAYMENT_METHOD = {
    PaymentChannel.BKASH: PaymentMethod.BKASH,
    PaymentChannel.NAGAD: PaymentMethod.NAGAD,
    PaymentChannel.ROCKET: PaymentMethod.ROCKET,
    PaymentChannel.BANK: PaymentMethod.BANK,
    PaymentChannel.OTHER: PaymentMethod.OTHER,
}


def approve_package_request(request: PackageRequest, *, admin_user=None) -> Subscription:
    """Activate a subscriber based on an approved package request.

    Creates / extends the subscription, opens an invoice marked PAID, records
    the manual payments against it, and pushes a notification to the user.
    """
    now = timezone.now()
    today = timezone.localdate()
    subscriber = request.subscriber
    plan = request.plan

    sub = subscriber.subscriptions.filter(plan=plan).order_by("-current_period_end").first()
    if sub:
        sub.roll_period_forward(periods=1)
        sub.save()
    else:
        sub = Subscription.objects.create(
            subscriber=subscriber, plan=plan,
            start_date=today,
            current_period_start=today,
            current_period_end=today + timedelta(days=plan.cycle_days),
        )

    invoice = Invoice.objects.create(
        subscriber=subscriber,
        subscription=sub,
        period_start=sub.current_period_start,
        period_end=sub.current_period_end,
        amount=request.amount,
        currency=request.currency,
        note=f"Package request #{request.pk}",
    )

    for mp in request.payments.all():
        Payment.objects.create(
            subscriber=subscriber,
            invoice=invoice,
            amount=mp.amount,
            currency=mp.currency,
            method=_CHANNEL_TO_PAYMENT_METHOD.get(mp.channel, PaymentMethod.OTHER),
            gateway_reference=mp.transaction_id,
            note=f"{mp.get_channel_display()} from {mp.sender_msisdn or 'unknown'}",
        )
        if not mp.confirmed:
            mp.confirmed = True
            mp.confirmed_at = now
            mp.save(update_fields=["confirmed", "confirmed_at"])

    invoice.recalculate_status()

    if subscriber.status != AccountStatus.ACTIVE or subscriber.plan_id != plan.id:
        subscriber.status = AccountStatus.ACTIVE
        subscriber.hold_reason = ""
        subscriber.plan = plan
        subscriber.save(update_fields=["status", "hold_reason", "plan", "updated_at"])

    request.status = PackageRequestStatus.APPROVED
    request.approved_at = now
    request.approved_by = admin_user
    request.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])

    Notification.objects.create(
        subscriber=subscriber,
        title=f"Your {plan.name} package is active",
        body=f"Payment confirmed. Your subscription runs until {sub.current_period_end}.",
        level="success",
        created_by=admin_user,
    )

    return sub


def reject_package_request(request: PackageRequest, *, admin_user=None, reason: str = "") -> None:
    request.status = PackageRequestStatus.REJECTED
    if reason:
        request.admin_note = (request.admin_note + "\n" + reason).strip() if request.admin_note else reason
    request.save(update_fields=["status", "admin_note", "updated_at"])
    Notification.objects.create(
        subscriber=request.subscriber,
        title=f"Package request rejected",
        body=reason or "Please contact support for details.",
        level="warning",
        created_by=admin_user,
    )
