"""Public contact-form submissions.

Visitors leave messages on /contact. Each submission is a plain DB record that
staff review in the admin. There is no per-user recipient to notify — the form
is public and pre-auth — so this deliberately does not ride on the
user-scoped notifications app.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ContactMessage(models.Model):
    class Topic(models.TextChoices):
        QUOTE = "quote", _("A quote or order")
        PARTNER = "partner", _("Joining as a printer")
        MANAGER = "manager", _("Becoming a manager")
        OTHER = "other", _("Something else")

    topic = models.CharField(
        max_length=20,
        choices=Topic.choices,
        default=Topic.QUOTE,
        verbose_name=_("topic"),
    )
    name = models.CharField(max_length=120, verbose_name=_("name"))
    email = models.EmailField(max_length=254, verbose_name=_("email"))
    message = models.TextField(verbose_name=_("message"))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_messages",
        verbose_name=_("user"),
        help_text=_("Account that signed in when submitting. Null for guests."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("contact message")
        verbose_name_plural = _("contact messages")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} · {self.get_topic_display()}"