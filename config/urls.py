"""
URL configuration for printy_API project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    path("api/auth/", include("accounts.urls")),
    path("api/setup/", include("setup.urls")),
    path("api/", include("feedback.urls")),
    path("accounts/", include("allauth.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
