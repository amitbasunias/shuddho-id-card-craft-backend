"""
URL configuration for the saas_admin project.

- /admin/                  Django admin (manage subscribers, billing, payments).
- /api/access/<email>/     Read-only access check used by the FastAPI product.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('accounts.urls')),
    path('api/', include('product.urls')),
    path('api/', include('business.urls')),
    path('', include('subscribers.urls')),
]
