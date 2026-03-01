"""
Reusable slug generation utilities.

Usage on models:
    class Shop(AutoSlugMixin, models.Model):
        slug_source_field = "name"
        ...

The mixin auto-generates a unique slug from ``slug_source_field`` on every
save when the slug is blank.  If the model already has a slug, it is kept
stable (not overwritten).
"""

from django.db import models
from django.utils.text import slugify


MAX_SLUG_LENGTH = 90  # leave room for "-N" suffix within a 100-char SlugField


def generate_unique_slug(
    model_class: type[models.Model],
    base_value: str,
    slug_field: str = "slug",
    instance_pk=None,
    max_length: int = MAX_SLUG_LENGTH,
    scope: dict | None = None,
) -> str:
    """
    Return a slug derived from *base_value* that is unique within *model_class*.

    Parameters
    ----------
    model_class : Model class to check uniqueness against.
    base_value  : Human-readable string to slugify (e.g. a shop name).
    slug_field  : Name of the slug column (default ``"slug"``).
    instance_pk : PK of the current instance (excluded from uniqueness check
                  so an existing object doesn't collide with itself).
    max_length  : Max characters before appending the numeric suffix.
    scope       : Optional dict of field lookups that scope uniqueness
                  (e.g. ``{"shop_id": 5}`` for per-shop unique slugs).
    """
    candidate = slugify(base_value, allow_unicode=False)[:max_length] or "item"

    qs = model_class.objects.all()
    if scope:
        qs = qs.filter(**scope)
    if instance_pk is not None:
        qs = qs.exclude(pk=instance_pk)

    if not qs.filter(**{slug_field: candidate}).exists():
        return candidate

    for i in range(2, 10_000):
        suffix = f"-{i}"
        trial = f"{candidate[:max_length - len(suffix)]}{suffix}"
        if not qs.filter(**{slug_field: trial}).exists():
            return trial

    raise RuntimeError(f"Could not generate unique slug for {model_class.__name__}")


class AutoSlugMixin(models.Model):
    """
    Abstract mixin that auto-populates ``slug`` from ``slug_source_field``
    on save when slug is blank.

    Subclasses must set ``slug_source_field`` (str) pointing to the
    human-readable source field.  Override ``get_slug_scope()`` to return
    a dict of field lookups when uniqueness is scoped (e.g. per-shop).
    """

    slug_source_field: str = "name"

    class Meta:
        abstract = True

    def get_slug_scope(self) -> dict | None:
        """Override to scope uniqueness, e.g. ``{"shop_id": self.shop_id}``."""
        return None

    def save(self, *args, **kwargs):
        if not self.slug:
            source = getattr(self, self.slug_source_field, "") or ""
            self.slug = generate_unique_slug(
                model_class=type(self),
                base_value=str(source),
                instance_pk=self.pk,
                scope=self.get_slug_scope(),
            )
        super().save(*args, **kwargs)
