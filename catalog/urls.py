from django.urls import path

from .views import CatalogBrowseView

urlpatterns = [
    path("", CatalogBrowseView.as_view(), name="catalog_browse"),
]
