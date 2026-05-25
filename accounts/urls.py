from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("auth/register", views.RegisterView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/refresh", TokenRefreshView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/me", views.MeView.as_view()),
    path("auth/change-password", views.ChangePasswordView.as_view()),
    path("auth/password-reset", views.PasswordResetRequestView.as_view()),
    path("auth/password-reset/confirm", views.PasswordResetConfirmView.as_view()),
]
