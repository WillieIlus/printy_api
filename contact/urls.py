from django.urls import path

from .views import ContactSubmissionView

app_name = "contact"

urlpatterns = [
    path("submit/", ContactSubmissionView.as_view(), name="submit"),
]