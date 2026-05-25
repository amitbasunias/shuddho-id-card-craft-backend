"""Client-facing DRF endpoints for package requests, notifications, support."""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from subscribers.models import AccountStatus, Subscriber, SubscriptionPlan

from .models import (
    ManualPayment,
    Notification,
    NotificationRead,
    PackageRequest,
    PackageRequestStatus,
    SupportTicket,
    TicketMessage,
    TicketPriority,
    TicketStatus,
)
from .serializers import (
    ManualPaymentCreateSerializer,
    NotificationSerializer,
    PackageRequestCreateSerializer,
    PackageRequestSerializer,
    PlanLiteSerializer,
    SupportTicketCreateSerializer,
    SupportTicketSerializer,
    TicketReplySerializer,
)


class PlansView(APIView):
    """List active subscription plans.

    Public on purpose — the marketing homepage renders pricing from here so
    the page reflects whatever the admin published, without bundling a
    duplicate copy of the prices in JS."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        qs = SubscriptionPlan.objects.filter(is_active=True).order_by("price", "name")
        return Response(PlanLiteSerializer(qs, many=True).data)


class PaymentConfigView(APIView):
    """Merchant numbers + instructions for the manual-payment screen.

    Configured via env so each deployment can publish its own numbers without
    a code change."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "currency": getattr(settings, "SAAS_DEFAULT_CURRENCY", "BDT"),
            "channels": [
                {
                    "channel": "bkash",
                    "label": "bKash",
                    "merchant_number": getattr(settings, "PAY_BKASH_NUMBER", ""),
                    "type": getattr(settings, "PAY_BKASH_TYPE", "Send Money"),
                },
                {
                    "channel": "nagad",
                    "label": "Nagad",
                    "merchant_number": getattr(settings, "PAY_NAGAD_NUMBER", ""),
                    "type": getattr(settings, "PAY_NAGAD_TYPE", "Send Money"),
                },
            ],
            "instructions": getattr(
                settings, "PAY_INSTRUCTIONS",
                "Send the exact amount, then submit the bKash/Nagad transaction id below."
            ),
        })


def _subscriber_for(user) -> Subscriber:
    sub = Subscriber.objects.filter(
        Q(email__iexact=user.email) | Q(product_user_email__iexact=user.email)
    ).first()
    if not sub:
        raise NotFound("No subscriber record linked to this account.")
    return sub


# ---------------------------------------------------------------------------
# Package requests
# ---------------------------------------------------------------------------

class PackageRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = _subscriber_for(request.user)
        qs = sub.package_requests.all().prefetch_related("payments", "plan")
        return Response(PackageRequestSerializer(qs, many=True).data)

    def post(self, request):
        sub = _subscriber_for(request.user)
        ser = PackageRequestCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        plan = ser.validated_data["plan"]
        if sub.package_requests.filter(
            status__in=[PackageRequestStatus.PENDING, PackageRequestStatus.SUBMITTED]
        ).exists():
            raise ValidationError(
                "You already have an open package request. Cancel it before opening a new one."
            )
        pr = PackageRequest.objects.create(
            subscriber=sub,
            plan=plan,
            amount=plan.price,
            currency=plan.currency,
            note=ser.validated_data.get("note", ""),
        )
        Notification.objects.create(
            subscriber=sub,
            title=f"Package request created: {plan.name}",
            body=(f"Send {pr.amount} {pr.currency} via bKash or Nagad, then "
                  "submit the transaction id on the Billing page."),
            level="info",
        )
        return Response(PackageRequestSerializer(pr).data,
                        status=status.HTTP_201_CREATED)


class PackageRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, user, pk) -> PackageRequest:
        sub = _subscriber_for(user)
        try:
            return sub.package_requests.prefetch_related("payments", "plan").get(pk=pk)
        except PackageRequest.DoesNotExist:
            raise NotFound("Package request not found.")

    def get(self, request, pk):
        return Response(PackageRequestSerializer(self._get(request.user, pk)).data)

    def delete(self, request, pk):
        pr = self._get(request.user, pk)
        if pr.status not in (PackageRequestStatus.PENDING, PackageRequestStatus.SUBMITTED):
            raise ValidationError("This request cannot be cancelled anymore.")
        pr.status = PackageRequestStatus.CANCELLED
        pr.save(update_fields=["status", "updated_at"])
        return Response(PackageRequestSerializer(pr).data)


class PackageRequestPaymentView(APIView):
    """User submits their bKash/Nagad txn id against a package request."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        sub = _subscriber_for(request.user)
        try:
            pr = sub.package_requests.get(pk=pk)
        except PackageRequest.DoesNotExist:
            raise NotFound("Package request not found.")
        if pr.status not in (PackageRequestStatus.PENDING, PackageRequestStatus.SUBMITTED):
            raise ValidationError("This request is not accepting payments.")

        ser = ManualPaymentCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        amount = data.get("amount") or pr.amount
        if ManualPayment.objects.filter(
            channel=data["channel"], transaction_id=data["transaction_id"]
        ).exists():
            raise ValidationError("This transaction id has already been submitted.")

        ManualPayment.objects.create(
            request=pr,
            channel=data["channel"],
            transaction_id=data["transaction_id"],
            sender_msisdn=data.get("sender_msisdn", ""),
            amount=amount,
            currency=pr.currency,
        )
        pr.status = PackageRequestStatus.SUBMITTED
        pr.save(update_fields=["status", "updated_at"])
        Notification.objects.create(
            subscriber=sub,
            title="Payment submitted — awaiting confirmation",
            body=(f"Thanks! Your {pr.plan.name} payment is being reviewed by an "
                  "admin. You'll get a notification as soon as it's approved."),
            level="info",
        )
        return Response(PackageRequestSerializer(pr).data,
                        status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = _subscriber_for(request.user)
        qs = Notification.objects.filter(
            Q(subscriber=sub) | Q(subscriber__isnull=True)
        ).order_by("-created_at")[:200]
        read_ids = set(
            NotificationRead.objects.filter(subscriber=sub, notification__in=qs)
            .values_list("notification_id", flat=True)
        )
        items = list(qs)
        for n in items:
            n._is_read = n.id in read_ids
        unread_count = sum(1 for n in items if not n._is_read)
        return Response({
            "unread_count": unread_count,
            "results": NotificationSerializer(items, many=True).data,
        })


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        sub = _subscriber_for(request.user)
        try:
            n = Notification.objects.get(
                Q(pk=pk) & (Q(subscriber=sub) | Q(subscriber__isnull=True))
            )
        except Notification.DoesNotExist:
            raise NotFound("Notification not found.")
        NotificationRead.objects.get_or_create(notification=n, subscriber=sub)
        return Response({"detail": "marked read"})


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        sub = _subscriber_for(request.user)
        qs = Notification.objects.filter(Q(subscriber=sub) | Q(subscriber__isnull=True))
        existing = set(
            NotificationRead.objects.filter(subscriber=sub)
            .values_list("notification_id", flat=True)
        )
        to_create = [
            NotificationRead(notification=n, subscriber=sub)
            for n in qs if n.id not in existing
        ]
        if to_create:
            NotificationRead.objects.bulk_create(to_create, ignore_conflicts=True)
        return Response({"detail": "all marked read", "count": len(to_create)})


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

class SupportTicketListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = _subscriber_for(request.user)
        qs = sub.support_tickets.prefetch_related("messages").all()
        return Response(SupportTicketSerializer(qs, many=True).data)

    def post(self, request):
        sub = _subscriber_for(request.user)
        ser = SupportTicketCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ticket = SupportTicket.objects.create(
            subscriber=sub,
            subject=ser.validated_data["subject"],
            priority=ser.validated_data.get("priority", TicketPriority.NORMAL),
            status=TicketStatus.PENDING_ADMIN,
        )
        TicketMessage.objects.create(
            ticket=ticket,
            author=request.user,
            from_admin=False,
            body=ser.validated_data["body"],
        )
        return Response(SupportTicketSerializer(ticket).data,
                        status=status.HTTP_201_CREATED)


class SupportTicketDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, user, pk) -> SupportTicket:
        sub = _subscriber_for(user)
        try:
            return sub.support_tickets.prefetch_related("messages").get(pk=pk)
        except SupportTicket.DoesNotExist:
            raise NotFound("Ticket not found.")

    def get(self, request, pk):
        return Response(SupportTicketSerializer(self._get(request.user, pk)).data)

    def patch(self, request, pk):
        ticket = self._get(request.user, pk)
        action = (request.data.get("action") or "").lower()
        if action == "close":
            ticket.status = TicketStatus.CLOSED
            ticket.closed_at = timezone.now()
            ticket.save(update_fields=["status", "closed_at", "updated_at"])
            return Response(SupportTicketSerializer(ticket).data)
        if action == "reopen":
            ticket.status = TicketStatus.PENDING_ADMIN
            ticket.closed_at = None
            ticket.save(update_fields=["status", "closed_at", "updated_at"])
            return Response(SupportTicketSerializer(ticket).data)
        raise ValidationError("Unknown action. Use 'close' or 'reopen'.")


class SupportTicketReplyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        sub = _subscriber_for(request.user)
        try:
            ticket = sub.support_tickets.get(pk=pk)
        except SupportTicket.DoesNotExist:
            raise NotFound("Ticket not found.")
        if ticket.status == TicketStatus.CLOSED:
            raise ValidationError("This ticket is closed; reopen it first.")

        ser = TicketReplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        msg = TicketMessage.objects.create(
            ticket=ticket,
            author=request.user,
            from_admin=False,
            body=ser.validated_data["body"],
        )
        ticket.status = TicketStatus.PENDING_ADMIN
        ticket.save(update_fields=["status", "updated_at"])
        return Response(SupportTicketSerializer(ticket).data,
                        status=status.HTTP_201_CREATED)
