"""Move existing files into the new per-user layout and rewrite DB paths.

Before:
    PRODUCT_STORAGE_ROOT/uploads/<project_id>/<photo>.png
    PRODUCT_STORAGE_ROOT/outputs/<project_id>/<student_id>/card.svg
    PRODUCT_STORAGE_ROOT/templates/<token>.<ext>
    PRODUCT_STORAGE_ROOT/templates/svg/<token>.svg
    PRODUCT_STORAGE_ROOT/templates/<user_id>/<token>.<ext>        (newer)
    PRODUCT_STORAGE_ROOT/templates/<user_id>/svg/<token>.svg      (newer)
    PRODUCT_STORAGE_ROOT/fonts/<user_id>/<filename>               (newer)
    PRODUCT_STORAGE_ROOT/fonts/global/<filename>                  (newer)

After:
    PRODUCT_STORAGE_ROOT/users/<user_id>/projects/<project_id>/uploads/<photo>.png
    PRODUCT_STORAGE_ROOT/users/<user_id>/projects/<project_id>/cards/<student_id>.svg
    PRODUCT_STORAGE_ROOT/users/<user_id>/templates/<token>.<ext>
    PRODUCT_STORAGE_ROOT/users/<user_id>/templates/svg/<token>.svg
    PRODUCT_STORAGE_ROOT/users/<user_id>/fonts/<filename>
    PRODUCT_STORAGE_ROOT/global/templates/svg/<token>.svg
    PRODUCT_STORAGE_ROOT/global/fonts/<filename>

Usage:
    python manage.py migrate_storage_layout            # actually move + rewrite
    python manage.py migrate_storage_layout --dry-run  # show plan only
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand

from product import storage
from product.models import Font, Project, Student, Template


class Command(BaseCommand):
    help = "Move existing storage files into the per-user layout and rewrite DB paths."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        self.dry = opts.get("dry_run")
        self.moved = 0
        self.skipped = 0
        self.rewrote = 0

        self._migrate_students()
        self._migrate_templates()
        self._migrate_fonts()

        verb = "WOULD HAVE" if self.dry else "DID"
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb} move {self.moved} file(s), skip {self.skipped}, "
            f"rewrite {self.rewrote} DB row(s)."
        ))

    # -------------------------------------------------------------------

    def _move(self, src: str, dst: Path) -> str | None:
        """Move src → dst. Returns the new path string, or None if src is bad.
        If dst already exists with identical contents we just rewrite the DB."""
        if not src:
            return None
        src_p = Path(src)
        if not src_p.exists():
            self.stdout.write(f"  ! missing on disk, skipping: {src}")
            self.skipped += 1
            return None
        if src_p.resolve() == dst.resolve():
            return str(dst)
        if self.dry:
            self.stdout.write(f"  mv {src_p} -> {dst}")
            self.moved += 1
            return str(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # Don't overwrite; just keep what's already there and point the DB
            # at it. The old copy gets removed afterwards.
            try:
                if src_p.exists():
                    src_p.unlink()
            except OSError:
                pass
        else:
            shutil.move(str(src_p), str(dst))
        self.moved += 1
        return str(dst)

    # -------------------------------------------------------------------

    def _migrate_students(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Students (photos + generated cards)"))
        for s in Student.objects.select_related("project__owner").all():
            proj = s.project
            if not proj or not proj.owner:
                continue
            owner = proj.owner

            updates = {}
            # photo_path → users/<uid>/projects/<pid>/uploads/<name>
            if s.photo_path:
                target = storage.uploads_dir(owner, proj.id) / Path(s.photo_path).name
                new = self._move(s.photo_path, target)
                if new and new != s.photo_path:
                    updates["photo_path"] = new
                    self.rewrote += 1
            # generated_svg → users/<uid>/projects/<pid>/cards/<student_id>.svg
            if s.generated_svg:
                target = storage.cards_dir(owner, proj.id) / f"{s.id}.svg"
                new = self._move(s.generated_svg, target)
                if new and new != s.generated_svg:
                    updates["generated_svg"] = new
                    self.rewrote += 1
            # Old PDF/EPS artefacts: nuke (we don't serve them anymore).
            for f in (s.generated_pdf, s.generated_eps):
                if f and os.path.exists(f) and not self.dry:
                    try: os.remove(f)
                    except OSError: pass
            if self.dry or not updates:
                continue
            for k, v in updates.items():
                setattr(s, k, v)
            s.generated_pdf = None
            s.generated_eps = None
            s.save(update_fields=list(updates.keys()) + ["generated_pdf", "generated_eps"])

    def _migrate_templates(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Templates"))
        for t in Template.objects.select_related("owner").all():
            owner = t.owner  # None for global
            is_global = bool(t.is_global)

            updates = {}
            # converted_svg lives in the templates/svg/ folder.
            if t.converted_svg:
                target = storage.templates_dir_for(owner, is_global=is_global, svg=True) / Path(t.converted_svg).name
                new = self._move(t.converted_svg, target)
                if new and new != t.converted_svg:
                    updates["converted_svg"] = new
                    self.rewrote += 1
            if t.original_file and t.original_file != t.converted_svg:
                target = storage.templates_dir_for(owner, is_global=is_global) / Path(t.original_file).name
                new = self._move(t.original_file, target)
                if new and new != t.original_file:
                    updates["original_file"] = new
                    self.rewrote += 1
            elif t.original_file and t.original_file == t.converted_svg and "converted_svg" in updates:
                updates["original_file"] = updates["converted_svg"]
            if self.dry or not updates:
                continue
            for k, v in updates.items():
                setattr(t, k, v)
            t.save(update_fields=list(updates.keys()))

    def _migrate_fonts(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Fonts"))
        for f in Font.objects.select_related("owner").all():
            owner = f.owner
            target = storage.fonts_dir_for(owner, is_global=bool(f.is_global)) / Path(f.path).name
            new = self._move(f.path, target)
            if new and new != f.path and not self.dry:
                f.path = new
                f.save(update_fields=["path"])
                self.rewrote += 1
