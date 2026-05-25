"""
SaaS business-flow models:

* PackageRequest + ManualPayment - signup -> request package -> submit bKash /
  Nagad txn id -> admin confirms -> subscriber gets activated.
* Notification - admin pushes messages to one subscriber or to everyone; client
  dashboard lists + marks read.
* SupportTicket + TicketMessage - customer support page; client opens a ticket,
  admin replies through Django admin, both sides see the thread.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from subscribers.models import Subscriber, SubscriptionPlan


# ---------------------------------------------------------------------------
# Package requests + manual mobile-banking payments
# ---------------------------------------------------------------------------

class PackageRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending (awaiting payment)"
    SUBMITTED = "submitted", "Payment submitted (awaiting confirmation)"
    APPROVED = "approved", "Approved & activated"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled by user"


class PaymentChannel(models.TextChoices):
    BKASH = "bkash", "bKash"
    NAGAD = "nagad", "Nagad"
    ROCKET = "rocket", "Rocket"
    BANK = "bank", "Bank transfer"
    OTHER = "other", "Other"


class PackageRequest(models.Model):
    """A subscriber's request to start (or upgrade) a paid plan.

    Flow: the user picks a plan -> we auto-populate the amount from the plan ->
    they send money via bKash/Nagad and submit the txn id (creates a
    ManualPayment row + flips status to SUBMITTED) -> admin reviews -> approves
    (activates the subscriber + opens a Subscription period) or rejects.
    """

    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="package_requests",
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="package_requests",
    )

    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Amount the user is asked to pay (copied from plan at request time).",
    )
    currency = models.CharField(max_length=8, default="BDT")

    status = models.CharField(
        max_length=20, choices=PackageRequestStatus.choices,
        default=PackageRequestStatus.PENDING,
    )
    note = models.TextField(blank=True, help_text="Optional message from the user.")
    admin_note = models.TextField(blank=True, help_text="Internal note from admin.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="approved_package_requests",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.subscriber} -> {self.plan.name} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.amount and self.plan_id:
            self.amount = self.plan.price
        if not self.currency and self.plan_id:
            self.currency = self.plan.currency
        super().save(*args, **kwargs)


class ManualPayment(models.Model):
    """A bKash / Nagad / etc. payment the user reports against a PackageRequest.

    The user sends money to the configured merchant number, then submits the
    transaction id here. Admin confirms it matches their gateway statement, then
    approves the parent PackageRequest.
    """

    request = models.ForeignKey(
        PackageRequest, on_delete=models.CASCADE, related_name="payments",
    )
    channel = models.CharField(max_length=20, choices=PaymentChannel.choices)
    sender_msisdn = models.CharField(
        max_length=32, blank=True,
        help_text="The mobile number the user sent money from.",
    )
    transaction_id = models.CharField(
        max_length=64,
        help_text="TrxID printed in the bKash/Nagad confirmation SMS.",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="BDT")

    submitted_at = models.DateTimeField(auto_now_add=True)
    confirmed = models.BooleanField(
        default=False,
        help_text="Set true once admin verifies this txn id against the gateway.",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "transaction_id"],
                name="unique_channel_txn",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} {self.transaction_id} ({self.amount} {self.currency})"


# ---------------------------------------------------------------------------
# In-app notifications
# ---------------------------------------------------------------------------

class NotificationLevel(models.TextChoices):
    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    URGENT = "urgent", "Urgent"


class Notification(models.Model):
    """A message shown inside the client dashboard.

    `subscriber` null = broadcast to every subscriber. A NotificationRead row is
    created the first time a user opens it, so the unread badge is per-user.
    """

    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="notifications",
        null=True, blank=True,
        help_text="Leave blank to broadcast to all subscribers.",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    level = models.CharField(
        max_length=20, choices=NotificationLevel.choices,
        default=NotificationLevel.INFO,
    )
    link_url = models.CharField(
        max_length=500, blank=True,
        help_text="Optional URL the dashboard can render as a CTA.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="sent_notifications",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        target = self.subscriber.email if self.subscriber else "ALL"
        return f"[{self.get_level_display()}] {self.title} -> {target}"

    @property
    def is_broadcast(self) -> bool:
        return self.subscriber_id is None


class NotificationRead(models.Model):
    """Marks that a specific subscriber has read a (possibly broadcast) notification."""

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="reads",
    )
    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="notification_reads",
    )
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "subscriber"],
                name="unique_notification_read",
            ),
        ]


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    ANSWERED = "answered", "Answered (awaiting user)"
    PENDING_ADMIN = "pending_admin", "Pending admin reply"
    CLOSED = "closed", "Closed"


class TicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class SupportTicket(models.Model):
    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.CASCADE, related_name="support_tickets",
    )
    subject = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN,
    )
    priority = models.CharField(
        max_length=20, choices=TicketPriority.choices, default=TicketPriority.NORMAL,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"#{self.pk} {self.subject} [{self.get_status_display()}]"

    @property
    def last_message(self):
        return self.messages.order_by("-created_at").first()


class TicketMessage(models.Model):
    """One post in a ticket thread (either from the customer or from admin)."""

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="ticket_messages",
    )
    from_admin = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        who = "admin" if self.from_admin else "user"
        return f"#{self.ticket_id} {who} @ {self.created_at:%Y-%m-%d %H:%M}"
