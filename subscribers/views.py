"""
Read-only access API.

The FastAPI product calls this to decide whether a given login email is allowed
to use the product right now. It is intentionally tiny and unauthenticated
(it only reveals account status, no sensitive data) so the product can check it
cheaply on login and on protected requests.

    GET /api/access/<email>/

Response (200):
    {
      "email": "karim@sunrise.edu",
      "known": true,
      "allowed": true,
      "status": "active",
      "reason": "Account active and billing current.",
      "plan": "Standard",
      "period_end": "2026-06-23",
      "days_until_renewal": 30
    }

For an unknown email, "known" is false and "allowed" is true by default, so the
product can choose to let through users that have no subscriber record yet.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import AccountStatus, Subscriber


@require_GET
def access_check(request, email):
    email = (email or "").strip().lower()
    sub = (
        Subscriber.objects.filter(product_user_email__iexact=email).first()
        or Subscriber.objects.filter(email__iexact=email).first()
    )

    if sub is None:
        return JsonResponse({
            "email": email,
            "known": False,
            "allowed": True,  # no record => product decides (fail-open by default)
            "status": None,
            "reason": "No subscriber record for this email.",
            "plan": None,
            "period_end": None,
            "days_until_renewal": None,
        })

    allowed = sub.has_product_access
    if allowed:
        reason = "Account active and billing current."
    elif sub.status != AccountStatus.ACTIVE:
        reason = f"Account status is {sub.get_status_display()}."
        if sub.hold_reason:
            reason += f" ({sub.hold_reason})"
    else:
        reason = "Billing period is not current (expired or no subscription)."

    current = sub.current_subscription
    return JsonResponse({
        "email": email,
        "known": True,
        "allowed": allowed,
        "status": sub.status,
        "reason": reason,
        "plan": sub.plan.name if sub.plan else None,
        "period_end": current.current_period_end.isoformat() if current else None,
        "days_until_renewal": sub.days_until_renewal,
    })


import json as _json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@csrf_exempt
@require_POST
def upsert_subscriber(request):
    """
    Create (or update) a subscriber when a user registers in the product.

    Called by the FastAPI product's register route. New subscribers are created
    ON HOLD with no billing period, so the owner must set a plan and activate
    them before they can use the product (when the gate is enabled).

    POST JSON body:
        {"email": "...", "full_name": "...", "product_user_id": 12}

    Idempotent: if a subscriber with this email already exists, it is left as-is
    (only the product_user_id is filled in if it was missing).
    """
    try:
        body = _json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)

    email = (body.get("email") or "").strip().lower()
    if not email:
        return JsonResponse({"ok": False, "error": "email required"}, status=400)

    full_name = (body.get("full_name") or "").strip() or email.split("@")[0]
    product_user_id = body.get("product_user_id")

    sub = (
        Subscriber.objects.filter(product_user_email__iexact=email).first()
        or Subscriber.objects.filter(email__iexact=email).first()
    )
    if sub:
        created = False
        if product_user_id and not sub.product_user_id:
            sub.product_user_id = product_user_id
            sub.save(update_fields=["product_user_id", "updated_at"])
    else:
        sub = Subscriber.objects.create(
            full_name=full_name,
            email=email,
            product_user_email=email,
            product_user_id=product_user_id,
            status=AccountStatus.ON_HOLD,
            hold_reason="Auto-created on product registration; pending plan/activation.",
        )
        created = True

    return JsonResponse({
        "ok": True,
        "created": created,
        "subscriber_id": sub.id,
        "status": sub.status,
    })
