"""Shared helpers for media retention.

We sweep TWO things:
  1. Files on disk under PRODUCT_STORAGE_ROOT/uploads and .../outputs older
     than the cutoff (this covers raw photo uploads and generated PDFs/SVGs).
  2. Student rows whose `created_at` is older than the cutoff get their
     generated_* paths and photo_path cleared so the DB doesn't keep dangling
     references — the row itself stays around for history.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from django.conf import settings
from django.utils import timezone


_SWEEP_DIRS = ("uploads", "outputs")


def purge_old_media(older_than_days: int = 90) -> int:
    """Delete every regular file under PRODUCT_STORAGE_ROOT/{uploads,outputs}
    older than `older_than_days` days. Returns the number of files removed."""

    root = Path(getattr(settings, "PRODUCT_STORAGE_ROOT", "."))
    cutoff_ts = time.time() - (older_than_days * 86400)
    removed = 0

    for sub in _SWEEP_DIRS:
        base = root / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            try:
                if not p.is_file():
                    continue
                if p.stat().st_mtime < cutoff_ts:
                    p.unlink()
                    removed += 1
            except OSError:
                continue

    # Drop in-DB references too so the admin UI stops showing 404 thumbnails.
    try:
        from product.models import Student
        old = Student.objects.filter(
            created_at__lt=timezone.now() - timezone.timedelta(days=older_than_days)
        )
        old.update(
            generated_svg=None, generated_pdf=None, generated_eps=None,
            photo_path=None,
        )
    except Exception:
        pass

    return removed
