from django.urls import path

from . import views

urlpatterns = [
    # Read-only access check used by the product at login + protected requests.
    path("api/access/<str:email>/", views.access_check, name="access_check"),
    path("api/access/<str:email>", views.access_check),
    # Upsert a subscriber when a user registers in the product.
    path("api/subscribers/upsert/", views.upsert_subscriber, name="upsert_subscriber"),
    path("api/subscribers/upsert", views.upsert_subscriber),
]
