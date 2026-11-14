"""ProfileImage node: a photo on someone's profile.

Kept apart from Person so loading every person stays cheap. Slot 0 is the
profile photo; slots 1-3 are the "pictures" on the profile card. The id is
derived from the owner and slot, so a slot is found (or replaced) without
scanning. Images are resized in the browser before upload.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from .base import DomainRuleError, GraphRecord, Visibility

PHOTO_SLOT = 0
PICTURE_SLOTS = (1, 2, 3)
MAX_IMAGE_BYTES = 200_000
CONTENT_TYPES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
}


def image_id(owner_id: str, slot: int) -> str:
    return f"img-{owner_id}-{slot}"


@dataclass(frozen=True, kw_only=True)
class ProfileImage(GraphRecord):
    owner_id: str
    slot: int
    content_type: str
    data_b64: str  # base64 of the image bytes
    visibility: Visibility = Visibility.NETWORK

    def __post_init__(self) -> None:
        if self.slot != PHOTO_SLOT and self.slot not in PICTURE_SLOTS:
            raise DomainRuleError("unknown picture slot")
        if self.content_type not in CONTENT_TYPES:
            raise DomainRuleError("use a JPEG, PNG or WebP image")
        data = self.data
        if len(data) > MAX_IMAGE_BYTES:
            raise DomainRuleError("that image is too large")
        if not data.startswith(CONTENT_TYPES[self.content_type]):
            raise DomainRuleError("that file isn't the image type it claims to be")

    @property
    def data(self) -> bytes:
        try:
            return base64.b64decode(self.data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise DomainRuleError("the image data is damaged") from None
