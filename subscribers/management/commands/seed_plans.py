"""Seed the three subscriber-facing plans.

Usage:
    python manage.py seed_plans          # idempotent
    python manage.py seed_plans --reset  # remove non-matching plans first

All plans are unlimited (no project / card caps). Yearly gives two months off
vs paying monthly; lifetime is a one-time purchase that never expires.
"""

from django.core.management.base import BaseCommand

from subscribers.models import BillingCycle, SubscriptionPlan

PLANS = [
    {
        "name": "Monthly",
        "description": "Unlimited everything, billed monthly.",
        "price": 2000,
        "currency": "BDT",
        "billing_cycle": BillingCycle.MONTHLY,
        "max_projects": None,
        "max_students": None,
    },
    {
        "name": "Yearly",
        "description": "Unlimited everything, billed yearly. Two months off vs monthly.",
        "price": 20000,
        "currency": "BDT",
        "billing_cycle": BillingCycle.YEARLY,
        "max_projects": None,
        "max_students": None,
    },
    {
        "name": "Lifetime",
        "description": "One-time payment. Unlimited everything, forever.",
        "price": 50000,
        "currency": "BDT",
        "billing_cycle": BillingCycle.LIFETIME,
        "max_projects": None,
        "max_students": None,
    },
]


class Command(BaseCommand):
    help = "Create or update the Monthly / Yearly / Lifetime plans."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Deactivate any plans whose name isn't in the canonical list.",
        )

    def handle(self, *args, **options):
        created = updated = 0
        names = {p["name"] for p in PLANS}
        for data in PLANS:
            obj, was_created = SubscriptionPlan.objects.update_or_create(
                name=data["name"], defaults={**data, "is_active": True},
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created: {obj}"))
            else:
                updated += 1
                self.stdout.write(f"Updated: {obj}")
        if options.get("reset"):
            n = SubscriptionPlan.objects.exclude(name__in=names).update(is_active=False)
            if n:
                self.stdout.write(self.style.WARNING(f"Deactivated {n} other plan(s)."))
        self.stdout.write(self.style.SUCCESS(
            f"Done. {created} created, {updated} updated."
        ))
