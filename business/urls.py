from django.urls import path

from . import views

urlpatterns = [
    path("business/plans", views.PlansView.as_view()),
    path("business/payment-config", views.PaymentConfigView.as_view()),

    # Package requests + manual payments
    path("business/package-requests",
         views.PackageRequestListCreateView.as_view()),
    path("business/package-requests/<int:pk>",
         views.PackageRequestDetailView.as_view()),
    path("business/package-requests/<int:pk>/payment",
         views.PackageRequestPaymentView.as_view()),

    # Notifications
    path("business/notifications",
         views.NotificationListView.as_view()),
    path("business/notifications/<int:pk>/read",
         views.NotificationReadView.as_view()),
    path("business/notifications/read-all",
         views.NotificationReadAllView.as_view()),

    # Support tickets
    path("business/support/tickets",
         views.SupportTicketListCreateView.as_view()),
    path("business/support/tickets/<int:pk>",
         views.SupportTicketDetailView.as_view()),
    path("business/support/tickets/<int:pk>/messages",
         views.SupportTicketReplyView.as_view()),
]
