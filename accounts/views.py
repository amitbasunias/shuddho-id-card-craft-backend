"""
Auth + account endpoints (DRF), replacing the old FastAPI JWT auth.

New signups create a Django auth User AND a linked Subscriber in PENDING/on-hold
state, so they can log in and see their dashboard but have no product access
until an admin activates a subscription. Login returns JWT access+refresh plus
the user profile and current subscription summary.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from subscribers.models import AccountStatus, Subscriber

from .serializers import (
    ChangePasswordSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)

User = get_user_model()


def _tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _subscription_summary(user):
    """Return a small dict describing the user's subscriber/billing state."""
    sub = Subscriber.objects.filter(email__iexact=user.email).first()
    if not sub:
        sub = Subscriber.objects.filter(product_user_email__iexact=user.email).first()
    if not sub:
        return {
            "status": "pending",
            "has_access": False,
            "plan": None,
            "period_end": None,
            "days_until_renewal": None,
            "reason": "No subscriber record yet.",
        }
    cur = sub.current_subscription
    return {
        "status": sub.status,
        "has_access": sub.has_product_access,
        "plan": sub.plan.name if sub.plan else None,
        "period_end": cur.current_period_end.isoformat() if cur else None,
        "days_until_renewal": sub.days_until_renewal,
        "reason": sub.hold_reason or "",
        # Storage quota lets the dashboard render a usage bar + warn before
        # uploads start failing with 413.
        "storage": {
            "used_mb": sub.storage_used_mb,
            "quota_mb": sub.storage_quota_mb,
            "is_over": sub.is_over_quota,
        },
    }


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        # Create a linked subscriber, PENDING/on-hold until an admin activates.
        sub, _ = Subscriber.objects.get_or_create(
            email=user.email,
            defaults={
                "full_name": (user.get_full_name() or user.email),
                "product_user_email": user.email,
                "product_user_id": user.id,
                "status": AccountStatus.ON_HOLD,
                "hold_reason": "New signup; pending plan selection / activation.",
            },
        )
        # Welcome notification.
        try:
            from business.models import Notification
            Notification.objects.create(
                subscriber=sub,
                title="Welcome to ID Craft 👋",
                body=("Thanks for signing up. Pick a package on the Billing page "
                      "to unlock project creation and card generation."),
                level="info",
            )
        except Exception:
            pass
        tokens = _tokens_for(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "subscription": _subscription_summary(user),
                **tokens,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        user = User.objects.filter(email__iexact=email).first()
        if user is None or not user.check_password(password):
            return Response({"detail": "Invalid credentials"},
                            status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            return Response({"detail": "Account disabled"},
                            status=status.HTTP_403_FORBIDDEN)
        tokens = _tokens_for(user)
        return Response({
            "user": UserSerializer(user).data,
            "subscription": _subscription_summary(user),
            **tokens,
        })


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "user": UserSerializer(request.user).data,
            "subscription": _subscription_summary(request.user),
        })


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Blacklist the provided refresh token if given.
        token = request.data.get("refresh")
        if token:
            try:
                RefreshToken(token).blacklist()
            except Exception:
                pass
        return Response({"detail": "Logged out"})


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(ser.validated_data["old_password"]):
            return Response({"detail": "Old password is incorrect"},
                            status=status.HTTP_400_BAD_REQUEST)
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password changed"})


class PasswordResetRequestView(APIView):
    """Emails a reset link. Email backend is console until SMTP is configured."""
    permission_classes = [AllowAny]

    def post(self, request):
        ser = PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        # Always return 200 (don't reveal whether an email exists).
        if user:
            from django.conf import settings
            from django.core.mail import send_mail
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_path = f"/reset-password?uid={uid}&token={token}"
            try:
                send_mail(
                    subject="Reset your ID Craft password",
                    message=f"Use this link to reset your password: {reset_path}",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=True,
                )
            except Exception:
                pass
        return Response({"detail": "If that email exists, a reset link has been sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = PasswordResetConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            uid = force_str(urlsafe_base64_decode(ser.validated_data["uid"]))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            return Response({"detail": "Invalid reset link"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not default_token_generator.check_token(user, ser.validated_data["token"]):
            return Response({"detail": "Invalid or expired token"},
                            status=status.HTTP_400_BAD_REQUEST)
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password has been reset"})
