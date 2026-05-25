"""DRF serializers for the business app."""
from __future__ import annotations

from rest_framework import serializers

from subscribers.models import SubscriptionPlan

from .models import (
    ManualPayment,
    Notification,
    NotificationLevel,
    PackageRequest,
    PaymentChannel,
    SupportTicket,
    TicketMessage,
    TicketPriority,
)


# ---- Package requests + manual payments -----------------------------------

class PlanLiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ("id", "name", "price", "currency", "billing_cycle",
                  "max_projects", "max_students")


class ManualPaymentSerializer(serializers.ModelSerializer):
    channel_display = serializers.CharField(source="get_channel_display", read_only=True)

    class Meta:
        model = ManualPayment
        fields = ("id", "channel", "channel_display", "sender_msisdn",
                  "transaction_id", "amount", "currency",
                  "submitted_at", "confirmed", "confirmed_at")
        read_only_fields = ("id", "channel_display", "submitted_at",
                            "confirmed", "confirmed_at")


class PackageRequestSerializer(serializers.ModelSerializer):
    plan_detail = PlanLiteSerializer(source="plan", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payments = ManualPaymentSerializer(many=True, read_only=True)

    class Meta:
        model = PackageRequest
        fields = ("id", "plan", "plan_detail", "amount", "currency",
                  "status", "status_display", "note", "admin_note",
                  "created_at", "updated_at", "approved_at", "payments")
        read_only_fields = ("id", "plan_detail", "status", "status_display",
                            "admin_note", "created_at", "updated_at",
                            "approved_at", "payments", "currency")
        extra_kwargs = {
            "amount": {"required": False},
        }


class PackageRequestCreateSerializer(serializers.Serializer):
    plan = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True)
    )
    note = serializers.CharField(required=False, allow_blank=True)


class ManualPaymentCreateSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=PaymentChannel.choices)
    transaction_id = serializers.CharField(max_length=64)
    sender_msisdn = serializers.CharField(max_length=32, required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)


# ---- Notifications --------------------------------------------------------

class NotificationSerializer(serializers.ModelSerializer):
    level_display = serializers.CharField(source="get_level_display", read_only=True)
    is_read = serializers.SerializerMethodField()
    is_broadcast = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ("id", "title", "body", "level", "level_display",
                  "link_url", "created_at", "is_broadcast", "is_read")

    def get_is_read(self, obj):
        return bool(getattr(obj, "_is_read", False))


# ---- Support tickets ------------------------------------------------------

class TicketMessageSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = TicketMessage
        fields = ("id", "from_admin", "author_name", "body", "created_at")
        read_only_fields = fields

    def get_author_name(self, obj):
        if not obj.author:
            return "Support" if obj.from_admin else "User"
        return (obj.author.get_full_name() or obj.author.email or "User").strip()


class SupportTicketSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    messages = TicketMessageSerializer(many=True, read_only=True)
    last_message_at = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ("id", "subject", "status", "status_display",
                  "priority", "priority_display", "created_at", "updated_at",
                  "closed_at", "last_message_at", "messages")
        read_only_fields = ("id", "status", "status_display", "priority_display",
                            "created_at", "updated_at", "closed_at",
                            "last_message_at", "messages")

    def get_last_message_at(self, obj):
        m = obj.last_message
        return m.created_at if m else None


class SupportTicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=200)
    body = serializers.CharField()
    priority = serializers.ChoiceField(choices=TicketPriority.choices, required=False)


class TicketReplySerializer(serializers.Serializer):
    body = serializers.CharField()
