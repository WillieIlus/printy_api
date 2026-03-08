from django.urls import path

from . import views

urlpatterns = [
    path("demo/templates/", views.demo_templates_list),
    path("demo/quote/", views.demo_quote),
]
