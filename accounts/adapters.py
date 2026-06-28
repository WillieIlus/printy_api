from django.conf import settings
from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """
    Redirect allauth email links to the frontend SPA instead of the Django
    form-based views.  This prevents confirmation/reset URLs from pointing at
    the API host or at localhost in production.
    """

    def get_email_confirmation_url(self, request, emailconfirmation):
        return f"{settings.FRONTEND_URL}/auth/confirm-email?key={emailconfirmation.key}"

    def get_reset_password_url(self, request):
        return f"{settings.FRONTEND_URL}/auth/reset-password"

    def get_reset_password_from_key_url(self, key):
        return f"{settings.FRONTEND_URL}/auth/reset-password?key={key}"
