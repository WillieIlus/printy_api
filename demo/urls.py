from django.urls import path

from . import views

urlpatterns = [
    path("demo/rate-card/", views.demo_rate_card),
    path("demo/templates/", views.demo_templates_list),
    path("demo/quote/", views.demo_quote),
]
