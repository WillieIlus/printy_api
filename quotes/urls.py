"""URL configuration for quotes app."""
from django.urls import path

from .views import QuoteRequestViewSet, QuoteItemViewSet

urlpatterns = [
    path(
        'shops/<int:shop_pk>/',
        QuoteRequestViewSet.as_view({
            'get': 'list',
            'post': 'create',
        }),
        name='shop-quote-requests',
    ),
    path(
        'shops/<int:shop_pk>/<int:pk>/',
        QuoteRequestViewSet.as_view({
            'get': 'retrieve',
        }),
        name='shop-quote-request-detail',
    ),
    path(
        'shops/<int:shop_pk>/<int:pk>/submit/',
        QuoteRequestViewSet.as_view({'post': 'submit'}),
        name='quote-request-submit',
    ),
    path(
        'shops/<int:shop_pk>/<int:pk>/price/',
        QuoteRequestViewSet.as_view({'post': 'price'}),
        name='quote-request-price',
    ),
    path(
        'shops/<int:shop_pk>/<int:pk>/lock/',
        QuoteRequestViewSet.as_view({'post': 'lock'}),
        name='quote-request-lock',
    ),
    path(
        'shops/<int:shop_pk>/<int:quote_request_pk>/items/',
        QuoteItemViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='quote-items',
    ),
    path(
        'shops/<int:shop_pk>/<int:quote_request_pk>/items/<int:pk>/',
        QuoteItemViewSet.as_view({
            'get': 'retrieve',
            'put': 'update',
            'patch': 'partial_update',
            'delete': 'destroy',
        }),
        name='quote-item-detail',
    ),
]
