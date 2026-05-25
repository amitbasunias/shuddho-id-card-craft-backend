"""
Data models for the ID Craft SaaS layer.

This Django service is the subscription / billing brain that sits conceptually
above the FastAPI product. It answers:

    - Who is a paying subscriber?
    - Is their account active, on hold, or suspended?
    - Is their current billing period still valid, or is payment overdue?
    - What have they paid, and when?

It keeps its own database (db.sqlite3) and does NOT read or write the product's
student_id.db. The link to a product account is a soft reference
(product_user_email / product_user_id) so the two systems stay decoupled.

Billing is manual for this first version: the owner creates accounts, opens
billing periods, and records payments by hand through the Django admin. Models
are shaped so an online gateway can be added later (see Payment.gateway_reference).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class AccountStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On hold"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class BillingCycle(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"
    LIFETIME = "lifetime", "Lifetime"


class InvoiceStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"
    VOID = "void", "Void"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK = "bank", "Bank transfer"
    BKASH = "bkash", "bKash"
    NAGAD = "nagad", "Nagad"
    ROCKET = "rocket", "Rocket"
    CARD = "card", "Card"
    OTHER = "other", "Other"


_CYCLE_DAYS = {
    BillingCycle.MONTHLY: 30,
    BillingCycle.QUARTERLY: 91,
    BillingCycle.YEARLY: 365,
    BillingCycle.LIFETIME: 365 * 100,  # effectively forever
}


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

class SubscriptionPlan(models.Model):
    """A named pricing tier the owner can assign to subscribers."""

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Recurring price per billing cycle.",
    )
    currency = models.CharField(max_length=8, default="BDT")
    billing_cycle = models.CharField(
        max_length=20, choices=BillingCycle.choices, default=BillingCycle.MONTHLY,
    )

    max_projects = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional cap on projects. Blank = unlimited.",
    )
    max_students = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional cap on students/cards. Blank = unlimited.",
    )

    is_active = models.BooleanField(
        default=True, help_text="Inactive plans can't be assigned to new accounts.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.price} {self.currency}/{self.get_billing_cycle_display().lower()})"

    @property
    def cycle_days(self) -> int:
        return _CYCLE_DAYS.get(self.billing_cycle, 30)


# ---------------------------------------------------------------------------
# Subscribers
# ---------------------------------------------------------------------------

class Subscriber(models.Model):
    """A customer of the ID Craft product, created by hand by the owner."""

    full_name = models.CharField(max_length=200)
    organization = models.CharField(
        max_length=200, blank=True,
        help_text="School / institution / company name (optional).",
    )
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True, help_text="Internal notes (not shown to the subscriber).")

    product_user_email = models.EmailField(
        blank=True,
        help_text="Email this subscriber uses to log into the product. "
                  "Defaults to the email above if left blank.",
    )
    product_user_id = models.IntegerField(
        null=True, blank=True,
        help_text="Optional: the user id from the product DB, if known.",
    )

    status = models.CharField(
        max_length=20, choices=AccountStatus.choices, default=AccountStatus.ACTIVE,
    )
    hold_reason = models.CharField(
        max_length=255, blank=True,
        help_text="Why the account is on hold / suspended (shown only to you).",
    )

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="subscribers",
    )

    # Per-user storage cap (MB). Counts every file under
    # PRODUCT_STORAGE_ROOT/users/<user_id>/ — uploads, generated cards,
    # user-uploaded templates and fonts. 0 = no quota.
    storage_quota_mb = models.PositiveIntegerField(
        default=5000,
        help_text="Hard cap on this subscriber's storage in megabytes. "
                  "0 disables the cap. Admin can raise per-account.",
    )
    storage_used_bytes = models.BigIntegerField(
        default=0,
        help_text="Cached total of the subscriber's on-disk files. "
                  "Recomputed on every upload and by saas_tick.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self) -> str:
        if self.organization:
            return f"{self.full_name} - {self.organization}"
        return self.full_name

    def save(self, *args, **kwargs):
        if not self.product_user_email:
            self.product_user_email = self.email
        super().save(*args, **kwargs)

    @property
    def current_subscription(self):
        return self.subscriptions.order_by("-current_period_end", "-id").first()

    @property
    def is_billing_current(self) -> bool:
        sub = self.current_subscription
        return bool(sub and sub.is_period_current)

    @property
    def has_product_access(self) -> bool:
        """Source of truth the product can consult: ACTIVE + billing current."""
        return self.status == AccountStatus.ACTIVE and self.is_billing_current

    @property
    def days_until_renewal(self):
        sub = self.current_subscription
        if not sub or not sub.current_period_end:
            return None
        return (sub.current_period_end - timezone.localdate()).days

    @property
    def storage_quota_bytes(self) -> int:
        return int(self.storage_quota_mb or 0) * 1024 * 1024

    @property
    def storage_used_mb(self) -> float:
        return round((self.storage_used_bytes or 0) / 1024 / 1024, 2)

    @property
    def is_over_quota(self) -> bool:
        if not self.storage_quota_mb:   # 0 = unlimited
            return False
        return (self.storage_used_bytes or 0) >= self.storage_quota_bytes


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

class Subscription(models.Model):
    """Tracks the current billing period for a subscriber on a given plan."""

    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="subscriptions",
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions",
    )

    start_date = models.DateField(default=timezone.localdate)
    current_period_start = models.DateField(default=timezone.localdate)
    current_period_end = models.DateField(
        blank=True,
        help_text="Paid-through date. Auto-filled from the plan cycle if blank.",
    )

    auto_renew = models.BooleanField(
        default=False,
        help_text="For manual billing leave this off; a hint for future automation.",
    )
    cancelled_at = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-current_period_end"]

    def __str__(self) -> str:
        return f"{self.subscriber} - {self.plan.name} (until {self.current_period_end})"

    def save(self, *args, **kwargs):
        if not self.current_period_end:
            base = self.current_period_start or timezone.localdate()
            self.current_period_end = base + timedelta(days=self.plan.cycle_days)
        super().save(*args, **kwargs)

    @property
    def is_period_current(self) -> bool:
        if self.cancelled_at:
            return False
        return timezone.localdate() <= self.current_period_end

    @property
    def is_due_soon(self) -> bool:
        if not self.is_period_current:
            return False
        days = (self.current_period_end - timezone.localdate()).days
        return 0 <= days <= getattr(settings, "SAAS_DUE_SOON_DAYS", 7)

    def roll_period_forward(self, periods: int = 1) -> None:
        """Advance the period by `periods` cycles from max(today, period_end)."""
        anchor = max(self.current_period_end, timezone.localdate())
        self.current_period_start = anchor
        self.current_period_end = anchor + timedelta(days=self.plan.cycle_days * periods)
        self.cancelled_at = None


# ---------------------------------------------------------------------------
# Invoices + payments
# ---------------------------------------------------------------------------

class Invoice(models.Model):
    """A bill issued to a subscriber for one billing period."""

    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="invoices",
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="invoices",
    )

    number = models.CharField(
        max_length=40, unique=True, blank=True,
        help_text="Auto-generated if left blank.",
    )
    period_start = models.DateField(default=timezone.localdate)
    period_end = models.DateField()
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(blank=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="BDT")
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.UNPAID,
    )
    note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-id"]

    def __str__(self) -> str:
        return f"{self.number or 'INV?'} - {self.subscriber} - {self.amount} {self.currency}"

    def save(self, *args, **kwargs):
        if not self.due_date:
            self.due_date = self.issue_date
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"INV-{self.issue_date:%Y%m}-{self.pk:05d}"
            super().save(update_fields=["number"])

    @property
    def amount_paid(self):
        total = self.payments.aggregate(s=models.Sum("amount"))["s"]
        return total or 0

    @property
    def balance_due(self):
        return self.amount - self.amount_paid

    @property
    def is_overdue(self) -> bool:
        return self.status != InvoiceStatus.PAID and timezone.localdate() > self.due_date

    def recalculate_status(self, commit: bool = True) -> str:
        if self.status == InvoiceStatus.VOID:
            return self.status
        paid = self.amount_paid
        if paid <= 0:
            self.status = InvoiceStatus.UNPAID
        elif paid < self.amount:
            self.status = InvoiceStatus.PARTIAL
        else:
            self.status = InvoiceStatus.PAID
        if commit:
            self.save(update_fields=["status", "updated_at"])
        return self.status


class Payment(models.Model):
    """A single payment received against an invoice (or standalone)."""

    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="payments",
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payments",
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="BDT")
    method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH,
    )
    paid_on = models.DateField(default=timezone.localdate)

    gateway_reference = models.CharField(
        max_length=120, blank=True,
        help_text="Transaction id from a payment gateway, if any.",
    )
    note = models.CharField(max_length=255, blank=True)

    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency} - {self.get_method_display()} - {self.paid_on}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.invoice_id:
            self.invoice.recalculate_status()
