import logging

from django.conf import settings
from django.core.mail import send_mail
from django.dispatch import receiver

from allauth.account.signals import user_signed_up

logger = logging.getLogger("accounts.signals")


@receiver(user_signed_up)
def notify_admin_on_signup(sender, request, user, **kwargs):
    admin_email = getattr(settings, "ADMIN_NOTIFY_EMAIL", None)
    if not admin_email:
        return

    role = getattr(user, "role", None) or "—"
    subject = f"[Printy] New signup: {user.email}"
    message = (
        f"A new user just signed up on Printy.\n\n"
        f"Email:  {user.email}\n"
        f"Role:   {role}\n"
        f"Name:   {user.get_full_name() or '—'}\n"
        f"ID:     {user.pk}\n\n"
        f"View in admin: {request.build_absolute_uri('/admin/accounts/user/')}"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[admin_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send signup notification for %s", user.email)
