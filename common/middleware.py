"""
Custom middleware for Printy API.
"""
from django.utils import translation


class UserLanguageMiddleware:
    """
    Activate user's preferred_language for authenticated requests.
    Runs after AuthenticationMiddleware. Falls back to Accept-Language if no preference.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            lang = getattr(request.user, "preferred_language", "") or "en"
            translation.activate(lang)
        response = self.get_response(request)
        return response
