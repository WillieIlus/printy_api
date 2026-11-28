"""
Custom middleware for Printy API.
"""
from django.conf import settings
from django.utils import translation


def _supported_language(code: str) -> str | None:
    code = str(code or "").strip()
    if not code:
        return None
    supported = {lang for lang, _name in getattr(settings, "LANGUAGES", [])}
    if code in supported:
        return code
    base = code.split("-")[0]
    return base if base in supported else None


def _resolve_user(request):
    """Return the authenticated user for this request, or ``None``.

    Django's ``AuthenticationMiddleware`` only knows session users. Most API
    calls authenticate with a JWT bearer token, which DRF resolves later,
    inside the view, so ``request.user`` is anonymous here. For those requests
    the token is checked with the same SimpleJWT authenticator DRF uses. Any
    failure is ignored: this only picks a language, and the view still runs
    its own authentication and returns the real 401.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication

        result = JWTAuthentication().authenticate(request)
    except Exception:
        return None
    if not result:
        return None
    return result[0]


class UserLanguageMiddleware:
    """
    Activate the user's ``preferred_language`` for authenticated requests.

    Audit #9:

    * It runs after authentication: session users come from
      ``AuthenticationMiddleware`` (which sits above it in ``MIDDLEWARE``) and
      JWT users are resolved from the bearer token, because DRF only
      authenticates them inside the view.
    * ``translation.activate`` stores the language in a thread-local. The old
      code never undid it, so the next request served by the same worker
      thread (or anything else run on it) inherited this user's language. The
      previous language is now restored in ``finally``, also when the view
      raises, and the chosen language is kept on the request
      (``request.LANGUAGE_CODE``) rather than only in the thread-local.

    Requests without a preference fall back to ``LocaleMiddleware``'s
    Accept-Language choice.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = _resolve_user(request)
        lang = _supported_language(getattr(user, "preferred_language", "")) if user is not None else None
        if lang is None:
            return self.get_response(request)

        previous = translation.get_language()
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        try:
            response = self.get_response(request)
            # LocaleMiddleware only setdefault()s this header after we return,
            # so the user's language is what the response reports.
            response.headers.setdefault("Content-Language", lang)
            return response
        finally:
            if previous:
                translation.activate(previous)
            else:
                translation.deactivate()
