"""Central storage-path helpers.

Single source of truth for where every file lives on disk. Layout:

    PRODUCT_STORAGE_ROOT/
      users/<user_id>/
        projects/<project_id>/
          uploads/<filename>            # student photo, uploaded via /upload/<token>
          cards/<student_id>.svg        # generated card output
        templates/
          <token>.<ext>                 # original upload (.svg/.pdf/.eps)
          svg/<token>.svg               # converted SVG (always present)
        fonts/<filename>
      global/
        templates/<token>.svg           # admin-published shared designs
        fonts/<filename>                # admin-published shared fonts
      tmp/                              # transient working files

Why per-user/per-project? It makes:
  * "purge this customer's data" → `rm -rf users/<id>/`
  * "purge this project" → `rm -rf users/<id>/projects/<pid>/`
  * "back up one customer" → tar one folder
  * the file browser easy to navigate.

Old layout (uploads/, outputs/, templates/, fonts/ at the root) is still
honoured for files whose absolute paths are already stored in the DB — we
just don't write into it any more. `migrate_storage_layout` moves them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from django.conf import settings

UserId = Union[int, str, "object"]  # accepts a User instance, an id, or a str id


def _uid(u) -> str:
    """Coerce a user / id / str into a string id usable as a folder name."""
    if u is None:
        return "anon"
    if hasattr(u, "pk"):
        return str(u.pk)
    return str(u)


def root() -> Path:
    return Path(settings.PRODUCT_STORAGE_ROOT)


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---- per-user roots -------------------------------------------------------

def user_root(user) -> Path:
    return _ensure(root() / "users" / _uid(user))


def project_root(user, project_id) -> Path:
    return _ensure(user_root(user) / "projects" / str(project_id))


def uploads_dir(user, project_id) -> Path:
    """Where student photos uploaded through the share link live."""
    return _ensure(project_root(user, project_id) / "uploads")


def cards_dir(user, project_id) -> Path:
    """Where generated SVG cards land for this project."""
    return _ensure(project_root(user, project_id) / "cards")


def user_templates_dir(user, *, svg: bool = False) -> Path:
    """Where one user's uploaded templates live. svg=True for the converted dir."""
    p = user_root(user) / "templates"
    if svg:
        p = p / "svg"
    return _ensure(p)


def user_fonts_dir(user) -> Path:
    return _ensure(user_root(user) / "fonts")


# ---- global (admin-published) roots --------------------------------------

def global_root() -> Path:
    return _ensure(root() / "global")


def global_templates_dir(*, svg: bool = False) -> Path:
    p = global_root() / "templates"
    if svg:
        p = p / "svg"
    return _ensure(p)


def global_fonts_dir() -> Path:
    return _ensure(global_root() / "fonts")


# ---- convenience choosers ------------------------------------------------

def templates_dir_for(user, *, is_global: bool = False, svg: bool = False) -> Path:
    return global_templates_dir(svg=svg) if is_global else user_templates_dir(user, svg=svg)


def fonts_dir_for(user, *, is_global: bool = False) -> Path:
    return global_fonts_dir() if is_global else user_fonts_dir(user)


def tmp_dir() -> Path:
    return _ensure(root() / "tmp")


# ---- quota helpers --------------------------------------------------------

def usage_bytes(user) -> int:
    """Sum every file under users/<id>/. Cheap enough for our scale (a single
    user's footprint is a few thousand files at most)."""
    base = user_root(user)
    total = 0
    if not base.exists():
        return 0
    for p in base.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def refresh_usage(user) -> int:
    """Recompute usage and persist it on the Subscriber row. Returns the new
    byte total. Called after every upload + by `saas_tick`."""
    from subscribers.models import Subscriber  # local import to dodge cycles
    bytes_ = usage_bytes(user)
    Subscriber.objects.filter(
        models.Q(email__iexact=getattr(user, "email", "")) |
        models.Q(product_user_email__iexact=getattr(user, "email", ""))
    ).update(storage_used_bytes=bytes_)
    return bytes_


class QuotaExceeded(Exception):
    """Raised by `check_quota` when an upload would push the user past their cap."""
    def __init__(self, used: int, quota: int, incoming: int = 0):
        self.used = used
        self.quota = quota
        self.incoming = incoming
        super().__init__(
            f"Storage quota exceeded: {used + incoming} / {quota} bytes."
        )


def check_quota(user, *, incoming: int = 0) -> None:
    """Raise `QuotaExceeded` if `user`'s current usage + an `incoming` upload
    would land over their cap. Looks up the cap on the linked Subscriber.
    No subscriber row = no quota check (admin / pre-billing accounts)."""
    from subscribers.models import Subscriber
    sub = Subscriber.objects.filter(
        models.Q(email__iexact=getattr(user, "email", "")) |
        models.Q(product_user_email__iexact=getattr(user, "email", ""))
    ).first()
    if not sub or not sub.storage_quota_mb:
        return  # no subscriber row or quota disabled
    if sub.is_over_quota and incoming == 0:
        raise QuotaExceeded(sub.storage_used_bytes, sub.storage_quota_bytes)
    if incoming and (sub.storage_used_bytes + incoming) > sub.storage_quota_bytes:
        raise QuotaExceeded(sub.storage_used_bytes, sub.storage_quota_bytes,
                            incoming=incoming)


# `models` import is here so the helpers above don't pollute the file top.
from django.db import models  # noqa: E402
