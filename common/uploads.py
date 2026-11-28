"""Validated file uploads (audit #9).

Every user upload that Printy stores goes through :func:`validate_upload`. It
checks three things, in order:

1. the extension, with Django's ``FileExtensionValidator`` against a per-kind
   allowlist;
2. the size, against a per-kind cap (an empty file is rejected too);
3. the leading bytes ("magic bytes"), so a file renamed to an allowed
   extension (an HTML page or an SVG saved as ``artwork.png``) is refused.

The allowlists keep exactly the print types the product accepted before this
change (artwork: PDF, JPG/JPEG, PNG, AI, EPS; avatars: JPG/JPEG, PNG, WebP,
GIF). SVG is never accepted: it is XML that a browser executes as a document,
so it is a stored-XSS vector when served inline.

Rejections raise :class:`UploadRejected`, a ``ValueError`` subclass, because the
existing artwork call sites already turn ``ValueError`` into a 400.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

MB = 1024 * 1024

# Extension -> canonical content type. The client-supplied Content-Type is
# never trusted; a validated file is described by its checked extension.
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "ai": "application/postscript",
    "eps": "application/postscript",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}

# Raster types a browser can render without executing anything. Only these may
# ever be served inline, and only after validation.
INLINE_SAFE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "gif"})

_PDF = (b"%PDF-",)
_POSTSCRIPT = (b"%!PS",)
_DOS_EPS = (b"\xc5\xd0\xd3\xc6",)  # binary EPS with a TIFF/WMF preview header
_JPEG = (b"\xff\xd8\xff",)
_PNG = (b"\x89PNG\r\n\x1a\n",)
_GIF = (b"GIF87a", b"GIF89a")

# Extension -> accepted leading byte sequences. Illustrator files are either
# PDF-compatible (modern .ai) or PostScript (legacy .ai).
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": _PDF,
    "ai": _PDF + _POSTSCRIPT,
    "eps": _POSTSCRIPT + _DOS_EPS,
    "jpg": _JPEG,
    "jpeg": _JPEG,
    "png": _PNG,
    "gif": _GIF,
}


def _is_webp(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"


@dataclass(frozen=True)
class UploadPolicy:
    label: str
    extensions: tuple[str, ...]
    max_bytes: int
    type_hint: str

    @property
    def max_mb(self) -> int:
        return self.max_bytes // MB


ARTWORK = UploadPolicy(
    label="Artwork",
    extensions=("pdf", "jpg", "jpeg", "png", "ai", "eps"),
    max_bytes=50 * MB,
    type_hint="JPG, PNG, PDF, AI, or EPS",
)

# Proofs are print previews of the same artwork, so they share its types and
# cap. Before audit #9 proof uploads were not validated at all.
PROOF = UploadPolicy(
    label="Proof",
    extensions=ARTWORK.extensions,
    max_bytes=ARTWORK.max_bytes,
    type_hint=ARTWORK.type_hint,
)

AVATAR = UploadPolicy(
    label="Avatar",
    extensions=("jpg", "jpeg", "png", "webp", "gif"),
    max_bytes=5 * MB,
    type_hint="JPG, PNG, WebP, or GIF",
)


class UploadRejected(ValueError):
    """The upload failed validation. ``str(exc)`` is safe to show the user."""


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    extension: str  # lower case, no dot
    size: int
    content_type: str

    @property
    def inline_safe(self) -> bool:
        return self.extension in INLINE_SAFE_EXTENSIONS


def read_head(file, length: int = 16) -> bytes:
    """Return the first ``length`` bytes and leave the file at offset 0."""
    try:
        file.seek(0)
    except (AttributeError, OSError, ValueError):
        pass
    head = file.read(length) or b""
    try:
        file.seek(0)
    except (AttributeError, OSError, ValueError):
        pass
    if isinstance(head, str):  # a text-mode handle never holds a valid upload
        head = head.encode("utf-8", "replace")
    return bytes(head)


def content_matches_extension(head: bytes, extension: str) -> bool:
    extension = extension.lower().lstrip(".")
    if extension == "webp":
        return _is_webp(head)
    signatures = SIGNATURES.get(extension)
    if not signatures:
        return False
    return any(head.startswith(signature) for signature in signatures)


def validate_upload(file, policy: UploadPolicy, *, original_filename: str = "") -> ValidatedUpload:
    """Validate ``file`` against ``policy`` or raise :class:`UploadRejected`."""
    if file is None:
        raise UploadRejected(f"Choose a file to upload. {policy.label} files must be {policy.type_hint}.")

    filename = os.path.basename(original_filename or getattr(file, "name", "") or "")
    try:
        FileExtensionValidator(allowed_extensions=list(policy.extensions))(_NamedFile(filename))
    except ValidationError:
        raise UploadRejected(
            f"Unsupported {policy.label.lower()} file type. Upload {policy.type_hint}."
        ) from None
    extension = os.path.splitext(filename)[1].lower().lstrip(".")

    size = int(getattr(file, "size", 0) or 0)
    if size <= 0:
        raise UploadRejected(f"{policy.label} file is empty. Choose another file.")
    if size > policy.max_bytes:
        raise UploadRejected(f"{policy.label} files must be {policy.max_mb}MB or smaller.")

    if not content_matches_extension(read_head(file), extension):
        raise UploadRejected(
            f"This file's contents do not match its .{extension} extension. "
            f"Upload a real {policy.type_hint} file."
        )

    return ValidatedUpload(
        filename=filename,
        extension=extension,
        size=size,
        content_type=CONTENT_TYPES.get(extension, "application/octet-stream"),
    )


class _NamedFile:
    """Minimal object for ``FileExtensionValidator``, which reads ``.name``."""

    def __init__(self, name: str):
        self.name = name


__all__ = [
    "ARTWORK",
    "AVATAR",
    "CONTENT_TYPES",
    "INLINE_SAFE_EXTENSIONS",
    "PROOF",
    "UploadPolicy",
    "UploadRejected",
    "ValidatedUpload",
    "content_matches_extension",
    "read_head",
    "validate_upload",
]
