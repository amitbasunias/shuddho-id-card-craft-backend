"""Daily housekeeping job.

Run from cron / Task Scheduler / systemd timer once a day:

    python manage.py saas_tick

It:
  1. Sends a "renew soon" notification 7 days before each subscription ends
     (once per subscription period — tracked in PackageRequest.admin_note so we
     don't spam if the cron fires twice).
  2. Sends an "expired" notification on the day a subscription lapses and
     flips the subscriber to ON_HOLD so the product gate engages.
  3. Deletes any media (uploads + generated cards) older than 90 days.

The third step is also exposed in Django admin as a button so a human can
trigger it on demand.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from business.models import Notification
from subscribers.models import AccountStatus, Subscriber, Subscription

from ._cleanup import purge_old_media

log = logging.getLogger(__name__)

WARN_DAYS = 7
RETENTION_DAYS = 90


class Command(BaseCommand):
    help = "Daily SaaS housekeeping: renewal warnings, expiry, media cleanup."

    def add_arguments(self, parser):
        parser.add_argument("--skip-cleanup", action="store_true")

    def handle(self, *args, **opts):
        today = timezone.localdate()
        warnings = expirations = 0

        # 1. Renewal warnings (T-7 days)
        warn_day = today + dt.timedelta(days=WARN_DAYS)
        warn_qs = Subscription.objects.filter(
            cancelled_at__isnull=True,
            current_period_end=warn_day,
        ).select_related("subscriber", "plan")
        for sub in warn_qs:
            already = Notification.objects.filter(
                subscriber=sub.subscriber,
                title__startswith="Subscription renews",
                created_at__date=today,
            ).exists()
            if already:
                continue
            Notification.objects.create(
                subscriber=sub.subscriber,
                title=f"Subscription renews in {WARN_DAYS} days",
                body=(f"Your {sub.plan.name} plan ends on {sub.current_period_end}. "
                      "Open Billing to renew before it expires."),
                level="warning",
            )
            warnings += 1

        # 2. Expirations — flip account to ON_HOLD + notify.
        expired_qs = Subscription.objects.filter(
            cancelled_at__isnull=True,
            current_period_end__lt=today,
        ).select_related("subscriber")
        for sub in expired_qs:
            subscriber = sub.subscriber
            # `current_subscription` orders by current_period_end desc, so if
            # the most recent one is in the past, the account has nothing live.
            cur = subscriber.current_subscription
            if cur and cur.current_period_end >= today:
                continue
            if subscriber.status == AccountStatus.ACTIVE:
                subscriber.status = AccountStatus.ON_HOLD
                subscriber.hold_reason = f"Subscription expired on {sub.current_period_end}."
                subscriber.save(update_fields=["status", "hold_reason", "updated_at"])
                Notification.objects.create(
                    subscriber=subscriber,
                    title="Your subscription has expired",
                    body=("Renew your package to keep creating projects and "
                          "generating cards. Your data is preserved for 90 days."),
                    level="urgent",
                )
                expirations += 1

        # 3. Media cleanup
        deleted = 0
        if not opts.get("skip_cleanup"):
            deleted = purge_old_media(older_than_days=RETENTION_DAYS)

        # 4. Recompute storage usage for every subscriber so the dashboards
        #    aren't stale (cron is the source of truth; live updates happen on
        #    each upload).
        usage_refreshed = 0
        try:
            from product import storage as _storage
            from django.contrib.auth import get_user_model
            U = get_user_model()
            for sub in Subscriber.objects.iterator():
                u = U.objects.filter(email__iexact=sub.product_user_email or sub.email).first()
                if u:
                    _storage.refresh_usage(u)
                    usage_refreshed += 1
        except Exception as e:
            log.exception("usage refresh failed: %s", e)

        self.stdout.write(self.style.SUCCESS(
            f"saas_tick: {warnings} renewal warnings, {expirations} expirations, "
            f"{deleted} stale files deleted, {usage_refreshed} storage rows refreshed."
        ))
