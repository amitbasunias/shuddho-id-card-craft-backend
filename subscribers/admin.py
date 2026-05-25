"""
Admin panel for the ID Craft SaaS layer.

Everything the owner needs to run the business by hand:
    * Create subscriber accounts manually.
    * Assign a plan and open / roll a billing period.
    * See at a glance who is paid-up, due soon, expired, on hold or suspended.
    * Record payments against invoices.
    * Hold, suspend, reactivate or cancel accounts with one click (bulk actions).
"""

from django import forms
from django.contrib import admin, messages
from django.db.models import Sum
from django.utils.html import format_html

from .models_product import usage_for_email, upsert_product_user, ProductUser
from .models import (
    AccountStatus,
    Invoice,
    InvoiceStatus,
    Payment,
    Subscriber,
    Subscription,
    SubscriptionPlan,
)

# Branding for the admin site.
admin.site.site_header = "ID Craft SaaS - Subscriber & Billing Admin"
admin.site.site_title = "ID Craft SaaS"
admin.site.index_title = "Manage subscribers, plans, billing and payments"


def _badge(text, color):
    return format_html(
        '<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        'font-size:11px;font-weight:600;color:#fff;background:{};">{}</span>',
        color, text,
    )


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "currency", "billing_cycle",
                    "max_projects", "max_students", "is_active", "subscriber_count")
    list_filter = ("billing_cycle", "is_active", "currency")
    search_fields = ("name", "description")
    list_editable = ("is_active",)

    @admin.display(description="Subscribers")
    def subscriber_count(self, obj):
        return obj.subscribers.count()


# ---------------------------------------------------------------------------
# Inlines shown on the Subscriber page
# ---------------------------------------------------------------------------

class SubscriptionInline(admin.TabularInline):
    model = Subscription
    extra = 0
    fields = ("plan", "current_period_start", "current_period_end",
              "auto_renew", "cancelled_at")
    show_change_link = True


class InvoiceInline(admin.TabularInline):
    model = Invoice
    extra = 0
    fields = ("number", "issue_date", "due_date", "amount", "currency", "status")
    readonly_fields = ("number",)
    show_change_link = True


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("paid_on", "amount", "currency", "method", "invoice", "note")
    show_change_link = True


# ---------------------------------------------------------------------------
# Subscribers - the main screen
# ---------------------------------------------------------------------------


class SubscriberAdminForm(forms.ModelForm):
    """Adds a write-only field to set the subscriber's PRODUCT login password."""

    product_login_password = forms.CharField(
        label="Product login password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Set or reset the password this subscriber uses to log into "
                  "the product. Leave blank to keep the existing password. "
                  "Saving with a password here creates the product login if it "
                  "doesn't exist yet.",
    )

    class Meta:
        model = Subscriber
        fields = "__all__"


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    form = SubscriberAdminForm
    list_display = (
        "full_name", "organization", "email", "plan",
        "account_badge", "billing_badge", "renewal_info", "access_badge",
    )
    list_filter = ("status", "plan", "plan__billing_cycle")
    search_fields = ("full_name", "organization", "email", "phone",
                     "product_user_email")
    autocomplete_fields = ("plan",)
    inlines = (SubscriptionInline, InvoiceInline, PaymentInline)
    readonly_fields = ("created_at", "updated_at", "access_summary", "product_usage", "product_login_status")

    fieldsets = (
        ("Who", {
            "fields": ("full_name", "organization", "email", "phone", "notes"),
        }),
        ("Product account link", {
            "fields": ("product_user_email", "product_user_id",
                       "product_login_status", "product_login_password"),
            "description": "How this subscriber maps to their login on the "
                           "FastAPI product. Leave product_user_email blank to "
                           "reuse the email above. Set a password below to create "
                           "or reset their product login.",
        }),
        ("Plan & status", {
            "fields": ("plan", "status", "hold_reason", "access_summary", "product_usage"),
        }),
        ("Storage", {
            "fields": ("storage_quota_mb", "storage_used_bytes"),
            "description": "Set quota_mb=0 to disable the cap for this subscriber.",
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    actions = (
        "action_activate",
        "action_hold",
        "action_suspend",
        "action_cancel",
        "action_renew_one_cycle",
        "action_purge_old_media",
        "action_purge_all_media",
    )

    @admin.display(description="Account")
    def account_badge(self, obj):
        colors = {
            AccountStatus.ACTIVE: "#16a34a",
            AccountStatus.ON_HOLD: "#d97706",
            AccountStatus.SUSPENDED: "#dc2626",
            AccountStatus.CANCELLED: "#6b7280",
        }
        return _badge(obj.get_status_display(), colors.get(obj.status, "#6b7280"))

    @admin.display(description="Billing")
    def billing_badge(self, obj):
        sub = obj.current_subscription
        if not sub:
            return _badge("No subscription", "#6b7280")
        if not sub.is_period_current:
            return _badge("Expired", "#dc2626")
        if sub.is_due_soon:
            return _badge("Due soon", "#d97706")
        return _badge("Current", "#16a34a")

    @admin.display(description="Renews / ended")
    def renewal_info(self, obj):
        sub = obj.current_subscription
        if not sub:
            return "-"
        days = obj.days_until_renewal
        if days is None:
            return str(sub.current_period_end)
        if days < 0:
            return format_html('<span style="color:#dc2626;">{} ({} days ago)</span>',
                               sub.current_period_end, abs(days))
        return format_html("{} (in {} days)", sub.current_period_end, days)

    @admin.display(description="Product access")
    def access_badge(self, obj):
        if obj.has_product_access:
            return _badge("Allowed", "#16a34a")
        return _badge("Blocked", "#dc2626")

    @admin.display(description="Access decision")
    def access_summary(self, obj):
        if not obj.pk:
            return "Save the subscriber first."
        if obj.has_product_access:
            return format_html(
                '<b style="color:#16a34a;">Allowed.</b> Account is ACTIVE and the '
                'billing period is current.'
            )
        reasons = []
        if obj.status != AccountStatus.ACTIVE:
            reasons.append(f"account status is {obj.get_status_display()}")
        if not obj.is_billing_current:
            reasons.append("billing period is not current (expired or no subscription)")
        return format_html(
            '<b style="color:#dc2626;">Blocked.</b> Because: {}.',
            "; ".join(reasons) or "unknown",
        )

    @admin.display(description="Product usage (live)")
    def product_usage(self, obj):
        if not obj.pk:
            return "Save the subscriber first."
        email = obj.product_user_email or obj.email
        u = usage_for_email(email)
        if u["projects"] is None:
            return format_html('<i>Product tables not available.</i>')
        if u["user_id"] is None:
            return format_html('No product account found for <code>{}</code> yet.', email)
        return format_html(
            'Logged in as <code>{}</code> &middot; <b>{}</b> project(s), '
            '<b>{}</b> student card(s).',
            email, u["projects"], u["students"],
        )

    @admin.display(description="Product login")
    def product_login_status(self, obj):
        if not obj.pk:
            return "Save first; you can set a password on creation too."
        email = obj.product_user_email or obj.email
        try:
            exists = ProductUser.objects.filter(email__iexact=email).exists()
        except Exception:
            return format_html('<i>Product tables not available.</i>')
        if exists:
            return format_html('<b style="color:#16a34a;">Login exists</b> for <code>{}</code>.', email)
        return format_html('<b style="color:#d97706;">No product login yet</b> for <code>{}</code>. '
                           'Set a password to create one.', email)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # If a product login password was provided, create/update the product
        # user in the shared DB and link the subscriber to it.
        pwd = form.cleaned_data.get("product_login_password")
        login_email = obj.product_user_email or obj.email
        if pwd:
            user = upsert_product_user(login_email, password=pwd)
            if user is None:
                self.message_user(
                    request,
                    "Could not write the product login (product tables not "
                    "available in this database).",
                    messages.WARNING,
                )
            else:
                if obj.product_user_id != user.id:
                    obj.product_user_id = user.id
                    obj.save(update_fields=["product_user_id"])
                self.message_user(
                    request,
                    f"Product login set for {login_email}.",
                    messages.SUCCESS,
                )

    @admin.action(description="Activate selected accounts")
    def action_activate(self, request, queryset):
        n = queryset.update(status=AccountStatus.ACTIVE, hold_reason="")
        self.message_user(request, f"{n} account(s) activated.", messages.SUCCESS)

    @admin.action(description="Put selected accounts ON HOLD")
    def action_hold(self, request, queryset):
        n = queryset.update(status=AccountStatus.ON_HOLD)
        self.message_user(request, f"{n} account(s) put on hold.", messages.WARNING)

    @admin.action(description="Suspend selected accounts (cut off access)")
    def action_suspend(self, request, queryset):
        n = queryset.update(status=AccountStatus.SUSPENDED)
        self.message_user(request, f"{n} account(s) suspended.", messages.WARNING)

    @admin.action(description="Cancel selected accounts")
    def action_cancel(self, request, queryset):
        n = queryset.update(status=AccountStatus.CANCELLED)
        self.message_user(request, f"{n} account(s) cancelled.", messages.WARNING)

    @admin.action(description="Renew billing by one plan cycle")
    def action_renew_one_cycle(self, request, queryset):
        renewed, skipped = 0, 0
        for subscriber in queryset:
            sub = subscriber.current_subscription
            if not sub:
                skipped += 1
                continue
            sub.roll_period_forward(periods=1)
            sub.save()
            renewed += 1
        msg = f"{renewed} subscription(s) rolled forward one cycle."
        if skipped:
            msg += f" {skipped} skipped (no subscription)."
        self.message_user(request, msg, messages.SUCCESS)

    @admin.action(description="Purge stale media (>90 days old)")
    def action_purge_old_media(self, request, queryset):
        """Triggers the same cleanup the daily cron runs. Whole-system, not
        per-subscriber — the queryset is ignored on purpose. We expose it
        here because the Subscriber list is where admins live."""
        from business.management.commands._cleanup import purge_old_media
        deleted = purge_old_media(older_than_days=90)
        self.message_user(
            request, f"Removed {deleted} file(s) older than 90 days.",
            messages.SUCCESS,
        )

    @admin.action(description="⚠ Purge ALL uploaded + generated media now")
    def action_purge_all_media(self, request, queryset):
        from business.management.commands._cleanup import purge_old_media
        deleted = purge_old_media(older_than_days=0)
        self.message_user(
            request, f"Removed {deleted} file(s) — uploads and outputs cleared.",
            messages.WARNING,
        )


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("subscriber", "plan", "current_period_start",
                    "current_period_end", "period_badge", "auto_renew")
    list_filter = ("plan", "auto_renew", "plan__billing_cycle")
    search_fields = ("subscriber__full_name", "subscriber__email",
                     "subscriber__organization")
    autocomplete_fields = ("subscriber", "plan")
    date_hierarchy = "current_period_end"
    actions = ("action_roll_forward",)

    @admin.display(description="Period")
    def period_badge(self, obj):
        if not obj.is_period_current:
            return _badge("Expired", "#dc2626")
        if obj.is_due_soon:
            return _badge("Due soon", "#d97706")
        return _badge("Current", "#16a34a")

    @admin.action(description="Roll period forward one cycle")
    def action_roll_forward(self, request, queryset):
        count = queryset.count()
        for sub in queryset:
            sub.roll_period_forward(periods=1)
            sub.save()
        self.message_user(request, f"{count} period(s) rolled forward.",
                          messages.SUCCESS)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "subscriber", "issue_date", "due_date",
                    "amount", "currency", "status_badge", "balance_col")
    list_filter = ("status", "currency", "issue_date")
    search_fields = ("number", "subscriber__full_name", "subscriber__email")
    autocomplete_fields = ("subscriber", "subscription")
    date_hierarchy = "issue_date"
    readonly_fields = ("number", "created_at", "updated_at")
    inlines = (PaymentInline,)
    actions = ("action_mark_paid", "action_recalculate", "action_void")

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            InvoiceStatus.PAID: "#16a34a",
            InvoiceStatus.PARTIAL: "#d97706",
            InvoiceStatus.UNPAID: "#dc2626",
            InvoiceStatus.VOID: "#6b7280",
        }
        if obj.is_overdue and obj.status != InvoiceStatus.PAID:
            return _badge("Overdue", "#dc2626")
        return _badge(obj.get_status_display(), colors.get(obj.status, "#6b7280"))

    @admin.display(description="Balance")
    def balance_col(self, obj):
        bal = obj.balance_due
        color = "#16a34a" if bal <= 0 else "#dc2626"
        return format_html('<span style="color:{};">{} {}</span>',
                           color, bal, obj.currency)

    @admin.action(description="Mark selected invoices PAID (records a payment)")
    def action_mark_paid(self, request, queryset):
        created = 0
        for inv in queryset:
            if inv.status == InvoiceStatus.VOID:
                continue
            balance = inv.balance_due
            if balance > 0:
                Payment.objects.create(
                    subscriber=inv.subscriber,
                    invoice=inv,
                    amount=balance,
                    currency=inv.currency,
                    note="Marked paid from admin",
                )
                created += 1
            inv.recalculate_status()
        self.message_user(request, f"Recorded {created} payment(s); invoices updated.",
                          messages.SUCCESS)

    @admin.action(description="Recalculate paid/unpaid status")
    def action_recalculate(self, request, queryset):
        for inv in queryset:
            inv.recalculate_status()
        self.message_user(request, "Invoice statuses recalculated.", messages.SUCCESS)

    @admin.action(description="Void selected invoices")
    def action_void(self, request, queryset):
        n = queryset.update(status=InvoiceStatus.VOID)
        self.message_user(request, f"{n} invoice(s) voided.", messages.WARNING)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("paid_on", "subscriber", "amount", "currency",
                    "method", "invoice", "gateway_reference")
    list_filter = ("method", "currency", "paid_on")
    search_fields = ("subscriber__full_name", "subscriber__email",
                     "gateway_reference", "note")
    autocomplete_fields = ("subscriber", "invoice")
    date_hierarchy = "paid_on"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            qs = response.context_data["cl"].queryset
            total = qs.aggregate(s=Sum("amount"))["s"] or 0
            response.context_data["title"] = (
                f"{response.context_data['title']} - total shown: {total}"
            )
        except (AttributeError, KeyError):
            pass
        return response
