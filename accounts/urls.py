from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from .views import (
    ClientRegisterView,
    ConfirmEmailView,
    CustomTokenObtainPairView,
    GoogleSocialLoginView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PartnerRegisterView,
    ProductionRegisterView,
    RegisterView,
    ResendEmailConfirmationView,
    UserDetailView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("register/client/", ClientRegisterView.as_view(), name="register_client"),
    path("register/partner/", PartnerRegisterView.as_view(), name="register_partner"),
    path("register/production/", ProductionRegisterView.as_view(), name="register_production"),
    path("login/", CustomTokenObtainPairView.as_view(), name="login"),
    path("token/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("logout/", TokenBlacklistView.as_view(), name="logout"),
    path("me/", UserDetailView.as_view(), name="user_detail"),
    path("confirm-email/", ConfirmEmailView.as_view(), name="confirm_email"),
    path("email/verify/", ConfirmEmailView.as_view(), name="email_verify"),
path("resend-confirmation/", ResendEmailConfirmationView.as_view(), name="resend_confirmation"),
    path("email/resend/", ResendEmailConfirmationView.as_view(), name="email_resend"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="password_reset"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password-change/", PasswordChangeView.as_view(), name="password_change"),
    path("social/google/", GoogleSocialLoginView.as_view(), name="social_google_login"),
]
