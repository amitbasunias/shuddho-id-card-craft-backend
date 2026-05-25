"""Django admin for the business app.

Admin uses these screens to:
  * Confirm a submitted package payment -> activates the subscriber.
  * Push notifications to one user or broadcast to all.
  * Reply to support tickets.
"""
from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    ManualPayment,
    Notification,
    NotificationRead,
    PackageRequest,
    PackageRequestStatus,
    SupportTicket,
    TicketMessage,
    TicketStatus,
)
from .services import approve_package_request, reject_package_request


# ---------------------------------------------------------------------------
# Package requests
# ---------------------------------------------------------------------------

class ManualPaymentInline(admin.TabularInline):
    model = ManualPayment
    extra = 0
    fields = ("channel", "transaction_id", "sender_msisdn", "amount",
              "currency", "submitted_at", "confirmed", "confirmed_at")
    readonly_fields = ("submitted_at", "confirmed_at")


@admin.register(PackageRequest)
class PackageRequestAdmin(admin.ModelAdmin):
    """Manual approval flow.

    Two-step on purpose so the human admin always confirms the payment landed
    in the bKash/Nagad statement before activating:

        1. Click "Mark payments as paid"  → flips every ManualPayment.confirmed
           on this request, posts a "payment received, activating soon"
           notification, but does NOT yet open a subscription.
        2. Click "Approve & activate"     → creates / extends the subscription,
           records the payments against an invoice, flips subscriber to ACTIVE,
           posts the "your plan is active" notification.

    Both buttons appear on the change page (next to Save) and as bulk actions
    on the changelist. `services.approve_package_request` is idempotent enough
    that step 1 is optional if you trust the txn id — but it's there so the
    admin has a "yes I saw the money" intermediate state.
    """

    # No need to set `change_form_template` — Django admin auto-discovers
    # `admin/<app>/<model>/change_form.html` from any app's templates folder,
    # which is exactly where ours lives.

    list_display = ("id", "subscriber", "plan", "amount", "currency",
                    "status_badge", "payments_badge", "created_at")
    list_filter = ("status", "plan", "currency")
    search_fields = ("subscriber__full_name", "subscriber__email",
                     "payments__transaction_id")
    autocomplete_fields = ("subscriber", "plan", "approved_by")
    readonly_fields = ("created_at", "updated_at", "approved_at",
                       "payments_check")
    fieldsets = (
        ("Request", {"fields": ("subscriber", "plan", "amount", "currency",
                                "status", "note", "admin_note")}),
        ("Payment check", {
            "fields": ("payments_check",),
            "description": (
                "Verify each transaction id against your bKash/Nagad app, "
                "then use the buttons at the bottom of the page."
            ),
        }),
        ("Meta", {"fields": ("approved_by", "approved_at",
                             "created_at", "updated_at"),
                  "classes": ("collapse",)}),
    )
    inlines = (ManualPaymentInline,)
    actions = ("action_mark_paid", "action_approve", "action_reject")

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            PackageRequestStatus.PENDING: "#6b7280",
            PackageRequestStatus.SUBMITTED: "#d97706",
            PackageRequestStatus.APPROVED: "#16a34a",
            PackageRequestStatus.REJECTED: "#dc2626",
            PackageRequestStatus.CANCELLED: "#6b7280",
        }
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            'font-size:11px;font-weight:600;color:#fff;background:{};">{}</span>',
            colors.get(obj.status, "#6b7280"), obj.get_status_display(),
        )

    @admin.display(description="Payments")
    def payments_badge(self, obj):
        n = obj.payments.count()
        if not n:
            return format_html('<span style="color:#dc2626;">none</span>')
        confirmed = obj.payments.filter(confirmed=True).count()
        color = "#16a34a" if confirmed == n else "#d97706"
        return format_html(
            '<span style="color:{};">{}/{} confirmed</span>',
            color, confirmed, n,
        )

    @admin.display(description="Submitted payments")
    def payments_check(self, obj):
        if not obj.pk:
            return "Save the request first."
        rows = []
        for p in obj.payments.all():
            tick = ("✅" if p.confirmed else "⬜")
            rows.append(format_html(
                "<div style='margin:4px 0;'>{} <b>{}</b> &middot; "
                "{} {} &middot; from <code>{}</code> "
                "(submitted {:%Y-%m-%d %H:%M})</div>",
                tick, p.get_channel_display(),
                p.transaction_id, p.amount,
                p.sender_msisdn or "?", p.submitted_at,
            ))
        if not rows:
            return format_html("<i>No payments submitted yet.</i>")
        return format_html("".join(rows))

    # ----- per-instance: extra submit buttons on the change form ---------

    def _mark_paid_one(self, request, obj) -> int:
        """Flip every payment on this request to confirmed=True and notify."""
        now = timezone.now()
        n = obj.payments.filter(confirmed=False).update(
            confirmed=True, confirmed_at=now,
        )
        if n:
            Notification.objects.create(
                subscriber=obj.subscriber,
                title="We received your payment",
                body=("We've confirmed your payment for the "
                      f"{obj.plan.name} plan. Activation usually happens "
                      "within an hour."),
                level="success",
                created_by=request.user,
            )
        return n

    def response_change(self, request, obj):
        # Hook our two extra submit buttons. We post a status message and
        # then re-render the change form (don't redirect to the changelist).
        if "_mark_paid" in request.POST:
            n = self._mark_paid_one(request, obj)
            self.message_user(
                request,
                f"Marked {n} payment(s) as paid; user notified." if n
                else "All payments on this request were already marked paid.",
                messages.SUCCESS if n else messages.INFO,
            )
            return self.response_post_save_change(request, obj)
        if "_approve_activate" in request.POST:
            if obj.status == PackageRequestStatus.APPROVED:
                self.message_user(request, "Already approved.", messages.INFO)
            else:
                approve_package_request(obj, admin_user=request.user)
                self.message_user(
                    request,
                    f"Approved — {obj.subscriber} activated on the "
                    f"{obj.plan.name} plan. Notification sent.",
                    messages.SUCCESS,
                )
            return self.response_post_save_change(request, obj)
        if "_reject" in request.POST:
            reject_package_request(
                obj, admin_user=request.user,
                reason="Rejected from admin",
            )
            self.message_user(request, "Request rejected; user notified.",
                              messages.WARNING)
            return self.response_post_save_change(request, obj)
        return super().response_change(request, obj)

    # ----- bulk actions ---------------------------------------------------

    @admin.action(description="① Mark payments as paid (verify txn first)")
    def action_mark_paid(self, request, queryset):
        total = 0
        for pr in queryset:
            total += self._mark_paid_one(request, pr)
        self.message_user(
            request, f"Marked {total} payment(s) as paid across {queryset.count()} request(s).",
            messages.SUCCESS,
        )

    @admin.action(description="② Approve & activate (creates subscription)")
    def action_approve(self, request, queryset):
        ok = skipped = 0
        for pr in queryset:
            if pr.status == PackageRequestStatus.APPROVED:
                skipped += 1
                continue
            approve_package_request(pr, admin_user=request.user)
            ok += 1
        self.message_user(
            request,
            f"Approved {ok} request(s); {skipped} already-approved skipped.",
            messages.SUCCESS,
        )

    @admin.action(description="Reject selected requests")
    def action_reject(self, request, queryset):
        ok = 0
        for pr in queryset:
            if pr.status == PackageRequestStatus.APPROVED:
                continue
            reject_package_request(pr, admin_user=request.user,
                                   reason="Rejected from admin")
            ok += 1
        self.message_user(request, f"Rejected {ok} request(s).", messages.WARNING)


@admin.register(ManualPayment)
class ManualPaymentAdmin(admin.ModelAdmin):
    list_display = ("submitted_at", "request", "channel", "transaction_id",
                    "amount", "currency", "confirmed")
    list_filter = ("channel", "confirmed", "currency")
    search_fields = ("transaction_id", "sender_msisdn",
                     "request__subscriber__email", "request__subscriber__full_name")
    autocomplete_fields = ("request",)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "title", "level", "target")
    list_filter = ("level", "created_at")
    search_fields = ("title", "body", "subscriber__email", "subscriber__full_name")
    autocomplete_fields = ("subscriber", "created_by")
    fieldsets = (
        ("Content", {"fields": ("title", "body", "level", "link_url")}),
        ("Audience", {
            "fields": ("subscriber",),
            "description": "Leave subscriber blank to broadcast to every subscriber.",
        }),
        ("Meta", {"fields": ("created_by",), "classes": ("collapse",)}),
    )

    @admin.display(description="Target")
    def target(self, obj):
        return obj.subscriber.email if obj.subscriber_id else "ALL (broadcast)"

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(NotificationRead)
class NotificationReadAdmin(admin.ModelAdmin):
    list_display = ("notification", "subscriber", "read_at")
    search_fields = ("subscriber__email", "notification__title")
    autocomplete_fields = ("notification", "subscriber")


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

class TicketMessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 1
    fields = ("from_admin", "author", "body", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("author",)


class SupportTicketAdminForm(forms.ModelForm):
    reply = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        help_text="Type a reply here to post it as an admin message on save.",
    )

    class Meta:
        model = SupportTicket
        fields = "__all__"


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    form = SupportTicketAdminForm
    list_display = ("id", "subject", "subscriber", "status", "priority",
                    "updated_at", "message_count")
    list_filter = ("status", "priority")
    search_fields = ("subject", "subscriber__email", "subscriber__full_name",
                     "messages__body")
    autocomplete_fields = ("subscriber",)
    inlines = (TicketMessageInline,)
    readonly_fields = ("created_at", "updated_at", "closed_at")
    actions = ("action_close", "action_reopen")

    @admin.display(description="Messages")
    def message_count(self, obj):
        return obj.messages.count()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        reply = (form.cleaned_data.get("reply") or "").strip()
        if reply:
            TicketMessage.objects.create(
                ticket=obj,
                author=request.user,
                from_admin=True,
                body=reply,
            )
            obj.status = TicketStatus.ANSWERED
            obj.save(update_fields=["status", "updated_at"])
            self.message_user(request, "Reply posted to ticket.", messages.SUCCESS)

    @admin.action(description="Close selected tickets")
    def action_close(self, request, queryset):
        from django.utils import timezone
        n = queryset.update(status=TicketStatus.CLOSED, closed_at=timezone.now())
        self.message_user(request, f"{n} ticket(s) closed.", messages.SUCCESS)

    @admin.action(description="Reopen selected tickets")
    def action_reopen(self, request, queryset):
        n = queryset.update(status=TicketStatus.PENDING_ADMIN, closed_at=None)
        self.message_user(request, f"{n} ticket(s) reopened.", messages.SUCCESS)


@admin.register(TicketMessage)
class TicketMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "ticket", "from_admin", "author", "snippet")
    list_filter = ("from_admin", "created_at")
    search_fields = ("body", "ticket__subject", "ticket__subscriber__email")
    autocomplete_fields = ("ticket", "author")

    @admin.display(description="Body")
    def snippet(self, obj):
        return (obj.body or "")[:80]
