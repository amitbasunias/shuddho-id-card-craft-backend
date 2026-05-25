"""
Card generation orchestration (Django ORM port of generate_card_for_student).

Calls the framework-agnostic engine in generation.py to render each student's
SVG and convert it to PDF/EPS. Output files live under PRODUCT_STORAGE_ROOT.
"""

import os
import traceback
from pathlib import Path

from django.conf import settings

from django.db.models import Q

from . import generation, storage
from .models import Font, Student, StudentStatus


def _storage_root() -> Path:
    root = Path(settings.PRODUCT_STORAGE_ROOT)
    return root


def output_dir_for(project, student_id: int) -> Path:
    """Per-user, per-project cards folder. `project` can be a Project instance
    or any object with `.id` + `.owner_id` / `.owner`."""
    owner = getattr(project, "owner", None) or getattr(project, "owner_id", None)
    return storage.cards_dir(owner, project.id)


def generate_card_for_student(student: Student) -> None:
    """Generate ID card synchronously for one student (updates the row in place)."""
    project = student.project
    if not project or not project.template_id:
        student.status = StudentStatus.FAILED
        student.error_message = "Project has no template"
        student.save(update_fields=["status", "error_message"])
        return

    template = project.template
    if not template or not template.converted_svg:
        student.status = StudentStatus.FAILED
        student.error_message = "Template SVG not available"
        student.save(update_fields=["status", "error_message"])
        return

    student.status = StudentStatus.PROCESSING
    student.save(update_fields=["status"])

    try:
        out_dir = output_dir_for(project, student.id)
        # SVG-only output: removed PDF/EPS rendering (and the cairosvg / inkscape
        # post-processing) so generation is faster and the storage footprint is
        # smaller. File is named after the student id so a project's cards/ folder
        # is human-browsable without drilling further.
        svg_out = str(out_dir / f"{student.id}.svg")

        template_svg = str(Path(template.converted_svg).resolve())

        student_data = {
            "student_name": student.name,
            "student_id": student.student_id,
            "father_name": student.father_name or "",
            "mother_name": student.mother_name or "",
            "class_name": student.class_name or "",
            "blood_group": student.blood_group or "",
            "address": student.address or "",
            "date_of_birth": student.date_of_birth or "",
            "mobile_number": student.mobile_number or "",
            "session": student.session or "",
            "department": student.department or "",
            "designation": student.designation or "",
            "husband_name": student.husband_name or "",
            "section": student.section or "",
            "roll": student.roll or "",
            "index_no": student.index_no or "",
            "nid_no": student.nid_no or "",
            "joining_date": student.joining_date or "",
            "guardians_mobile": student.guardians_mobile or "",
            "registration_no": student.registration_no or "",
        }

        # Background removal (deferred from upload so submission stays instant).
        photo_for_render = student.photo_path
        if photo_for_render and os.path.exists(photo_for_render):
            photo_for_render = generation.remove_photo_background(photo_for_render)

        # Crop photo to the placeholder aspect ratio (top-aligned).
        if photo_for_render and os.path.exists(photo_for_render):
            mapping = template.mapping_json or {}
            photo_w, photo_h = 0.0, 0.0
            for entry in mapping.values():
                if isinstance(entry, dict) and entry.get("field_name") == "student_photo":
                    pos = entry.get("position") or {}
                    try:
                        photo_w = float(pos.get("width", 0))
                        photo_h = float(pos.get("height", 0))
                    except (TypeError, ValueError):
                        pass
                    break
            if photo_w > 0 and photo_h > 0:
                photo_for_render = generation.crop_photo_to_aspect(photo_for_render, photo_w, photo_h)

        # Build {font_family_name: absolute_path} from every Font this user can
        # see (their own uploads + admin's global library). Without this the
        # SVG falls back to system fonts — and templates that pin Bengali
        # fonts will look wrong.
        fonts_map = {}
        try:
            qs = Font.objects.filter(Q(owner=project.owner) | Q(is_global=True))
            for f in qs:
                if f.path and os.path.exists(f.path):
                    fonts_map[Path(f.filename).stem] = f.path
                    fonts_map[f.name] = f.path  # also index by display name
        except Exception:
            fonts_map = {}

        generation.render_card_svg(
            template_svg, student_data, photo_for_render, svg_out,
            template.mapping_json or {},
            enabled_fields=project.enabled_fields,
            fonts_config=project.fonts_config,
            fonts_map=fonts_map,
        )

        student.generated_svg = svg_out
        student.generated_pdf = None
        student.generated_eps = None
        student.status = StudentStatus.DONE
        student.error_message = None
        student.save(update_fields=[
            "generated_svg", "generated_pdf", "generated_eps",
            "status", "error_message",
        ])
    except Exception:
        tb = traceback.format_exc()
        student.status = StudentStatus.FAILED
        student.error_message = tb
        student.save(update_fields=["status", "error_message"])
