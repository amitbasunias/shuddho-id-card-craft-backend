"""
Authenticated product API (DRF) — templates, projects, students, generation,
storage, fonts, background removal. Ported from the FastAPI product.

Access control: all endpoints require an authenticated user (JWT). Object-level
ownership is enforced (a user only sees/edits their own projects). A subscription
gate check (active + billing current) guards the heavier actions.
"""

import os
import shutil
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import models
from django.http import FileResponse, HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from subscribers.models import Subscriber

from . import generation, services, storage
from .models import Font, Project, Student, StudentStatus, Template
from .serializers import (
    ProjectCreateSerializer,
    ProjectSerializer,
    ProjectUpdateSerializer,
    StudentSerializer,
    StudentWriteSerializer,
    TemplateCreateSerializer,
    TemplateMappingUpdateSerializer,
    TemplateSerializer,
)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _root() -> Path:
    return Path(settings.PRODUCT_STORAGE_ROOT)


def _dir(*parts) -> Path:
    d = _root().joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_has_access(user) -> bool:
    # Staff / superusers always pass: they manage the SaaS itself.
    if user.is_staff or user.is_superuser:
        return True
    sub = (Subscriber.objects.filter(email__iexact=user.email).first()
           or Subscriber.objects.filter(product_user_email__iexact=user.email).first())
    return bool(sub and sub.has_product_access)


def _gate_response():
    """Standard 402 Payment Required body — frontend uses this to show the
    "Subscribe to continue" card on Projects/Templates/Generation pages."""
    return Response(
        {
            "detail": "Active subscription required.",
            "code": "subscription_required",
        },
        status=402,
    )


def _require_access(user):
    """Return None if user can use the product, otherwise an HTTP response."""
    return None if _user_has_access(user) else _gate_response()


def _quota_response(exc):
    """HTTP 413 Payload Too Large with a body the frontend can parse."""
    return Response({
        "detail": (
            f"Storage quota exceeded ({round(exc.used / 1024 / 1024, 1)} MB used "
            f"of {round(exc.quota / 1024 / 1024)} MB). Delete some data or "
            f"contact support to raise the cap."
        ),
        "code": "quota_exceeded",
        "used_bytes": exc.used,
        "quota_bytes": exc.quota,
    }, status=413)


def _enforce_quota(user, *, incoming: int = 0):
    """Returns an HTTP response if the user has run out of room, else None."""
    if user.is_staff or user.is_superuser:
        return None
    try:
        storage.check_quota(user, incoming=incoming)
    except storage.QuotaExceeded as exc:
        return _quota_response(exc)
    return None


def _owned_project(user, project_id):
    """Project lookup with ownership check — staff/superusers can reach any
    project so the SaaS owner can audit / download on a customer's behalf."""
    qs = Project.objects.filter(id=project_id)
    if not (user.is_staff or user.is_superuser):
        qs = qs.filter(owner=user)
    return qs.first()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _visible_templates(user):
    """A user sees their own templates + every global one."""
    if user.is_staff or user.is_superuser:
        return Template.objects.all()
    return Template.objects.filter(models.Q(owner=user) | models.Q(is_global=True))


def _owned_template(user, template_id):
    """Anything the user can edit / delete — only ones THEY own. Global
    templates can be READ by everyone but only changed by an admin (staff)."""
    if user.is_staff or user.is_superuser:
        return Template.objects.filter(id=template_id).first()
    return Template.objects.filter(id=template_id, owner=user).first()


class TemplateListView(APIView):
    def get(self, request):
        return Response(TemplateSerializer(_visible_templates(request.user), many=True).data)


class TemplateUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        gate = _require_access(request.user)
        if gate is not None: return gate
        f = request.FILES.get("file")
        name = request.data.get("name") or (f.name if f else "Template")
        if not f:
            return Response({"detail": "No file uploaded"}, status=400)
        # Decide upload destination up front (per-user OR global if staff opts in)
        wants_global_pre = (
            (request.user.is_staff or request.user.is_superuser)
            and str(request.data.get("is_global", "")).lower() in ("1", "true", "yes")
        )
        # Per-user uploads count toward the quota; global ones don't.
        if not wants_global_pre:
            block = _enforce_quota(request.user, incoming=f.size)
            if block is not None: return block
        owner_for_dirs = None if wants_global_pre else request.user
        tdir = storage.templates_dir_for(owner_for_dirs, is_global=wants_global_pre)
        ext = os.path.splitext(f.name)[1].lower()
        token = uuid.uuid4().hex
        original_path = str(tdir / f"{token}{ext}")
        with open(original_path, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        # Convert to SVG (handles .eps/.ai/.pdf/.svg via inkscape/cairosvg).
        svg_dir = storage.templates_dir_for(owner_for_dirs, is_global=wants_global_pre, svg=True)
        svg_path = str(svg_dir / f"{token}.svg")
        try:
            generation.convert_to_svg(original_path, svg_path)
            mapping = generation.extract_placeholder_ids(svg_path)
        except Exception as exc:
            return Response({"detail": f"Conversion failed: {exc}"}, status=400)
        # Same flag we evaluated above; recomputed only because the old
        # variable name is referenced by tests.
        wants_global = wants_global_pre
        tpl = Template.objects.create(
            name=name,
            owner=None if wants_global else request.user,
            is_global=wants_global,
            original_file=original_path,
            converted_svg=svg_path, mapping_json=mapping,
        )
        if not wants_global:
            storage.refresh_usage(request.user)
        return Response(TemplateSerializer(tpl).data, status=201)


class TemplateCreateView(APIView):
    def post(self, request):
        gate = _require_access(request.user)
        if gate is not None: return gate
        ser = TemplateCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        wants_global = (
            (request.user.is_staff or request.user.is_superuser)
            and bool(request.data.get("is_global"))
        )
        owner_for_dirs = None if wants_global else request.user
        tdir = storage.templates_dir_for(owner_for_dirs, is_global=wants_global, svg=True)
        token = uuid.uuid4().hex
        svg_path = str(tdir / f"{token}.svg")
        with open(svg_path, "w", encoding="utf-8") as out:
            out.write(ser.validated_data["svg_content"])
        mapping = ser.validated_data.get("mapping_json")
        if mapping is None:
            try:
                mapping = generation.extract_placeholder_ids(svg_path)
            except Exception:
                mapping = {}
        tpl = Template.objects.create(
            name=ser.validated_data["name"],
            owner=None if wants_global else request.user,
            is_global=wants_global,
            original_file=svg_path,
            converted_svg=svg_path, mapping_json=mapping,
        )
        return Response(TemplateSerializer(tpl).data, status=201)


class TemplateDetailView(APIView):
    def get(self, request, template_id):
        tpl = _visible_templates(request.user).filter(id=template_id).first()
        if not tpl:
            return Response({"detail": "Not found"}, status=404)
        return Response(TemplateSerializer(tpl).data)

    def delete(self, request, template_id):
        tpl = _owned_template(request.user, template_id)
        if not tpl:
            return Response({"detail": "Not found"}, status=404)
        tpl.delete()
        return Response({"detail": "deleted"})


class TemplateSvgView(APIView):
    def get(self, request, template_id):
        tpl = _visible_templates(request.user).filter(id=template_id).first()
        if not tpl or not tpl.converted_svg or not os.path.exists(tpl.converted_svg):
            return Response({"detail": "SVG not available"}, status=404)
        with open(tpl.converted_svg, "r", encoding="utf-8") as fh:
            return HttpResponse(fh.read(), content_type="image/svg+xml")


class TemplateMappingView(APIView):
    def put(self, request, template_id):
        tpl = _owned_template(request.user, template_id)
        if not tpl:
            return Response({"detail": "Not found"}, status=404)
        ser = TemplateMappingUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        tpl.mapping_json = ser.validated_data["mapping_json"]
        tpl.save(update_fields=["mapping_json"])
        return Response(TemplateSerializer(tpl).data)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectListView(APIView):
    def get(self, request):
        qs = Project.objects.filter(owner=request.user)
        return Response(ProjectSerializer(qs, many=True).data)

    def post(self, request):
        gate = _require_access(request.user)
        if gate is not None: return gate
        ser = ProjectCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        tpl = None
        if ser.validated_data.get("template_id"):
            # Only let the user attach their own template or a global one.
            tpl = _visible_templates(request.user).filter(
                id=ser.validated_data["template_id"]
            ).first()
        proj = Project.objects.create(
            name=ser.validated_data["name"],
            description=ser.validated_data.get("description") or "",
            template=tpl, owner=request.user,
            enabled_fields=ser.validated_data.get("enabled_fields"),
            fonts_config=ser.validated_data.get("fonts_config"),
            delivery_date=ser.validated_data.get("delivery_date"),
        )
        return Response(ProjectSerializer(proj).data, status=201)


class ProjectDetailView(APIView):
    def get(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        return Response(ProjectSerializer(proj).data)

    def put(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        ser = ProjectUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if "template_id" in data:
            proj.template = Template.objects.filter(id=data["template_id"]).first()
        for f in ("name", "status", "enabled_fields", "fonts_config",
                  "description", "delivery_date"):
            if f in data:
                setattr(proj, f, data[f])
        proj.save()
        return Response(ProjectSerializer(proj).data)

    def delete(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        proj.delete()
        return Response(status=204)


# ---------------------------------------------------------------------------
# Students + generation
# ---------------------------------------------------------------------------

class ProjectStudentsView(APIView):
    def get(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        qs = proj.students.all().order_by("id")
        return Response(StudentSerializer(qs, many=True).data)


class StudentDetailView(APIView):
    def put(self, request, project_id, student_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st:
            return Response({"detail": "Not found"}, status=404)
        ser = StudentWriteSerializer(st, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(StudentSerializer(st).data)

    def delete(self, request, project_id, student_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st:
            return Response({"detail": "Not found"}, status=404)
        st.delete()
        return Response(status=204)


class StudentBulkDeleteView(APIView):
    def post(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        ids = request.data.get("ids")
        qs = proj.students.all()
        if ids:
            qs = qs.filter(id__in=ids)
        count = qs.count()
        qs.delete()
        return Response({"deleted": count})


class GenerateView(APIView):
    def post(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        if not _user_has_access(request.user):
            return _gate_response()
        block = _enforce_quota(request.user)
        if block is not None: return block
        mode = request.query_params.get("mode", "pending")
        qs = proj.students.all()
        if mode == "pending":
            qs = qs.filter(status__in=[StudentStatus.PENDING, StudentStatus.FAILED])
        for st in qs:
            services.generate_card_for_student(st)
        storage.refresh_usage(request.user)
        return Response({"detail": "Generation complete"}, status=202)


class GenerationStatusView(APIView):
    def get(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        students = proj.students.all()
        def c(s):
            return students.filter(status=s).count()
        return Response({
            "project_id": proj.id,
            "total": students.count(),
            "pending": c(StudentStatus.PENDING),
            "processing": c(StudentStatus.PROCESSING),
            "completed": c(StudentStatus.DONE),
            "failed": c(StudentStatus.FAILED),
        })


class ReprocessStudentView(APIView):
    def post(self, request, project_id, student_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st:
            return Response({"detail": "Not found"}, status=404)
        services.generate_card_for_student(st)
        return Response(StudentSerializer(st).data, status=202)


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

# SVG is the only supported output format. The `format` query param is now
# ignored — kept silently to avoid breaking any pre-existing share links.

class StudentDownloadView(APIView):
    def get(self, request, project_id, student_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Project not found or not yours."},
                            status=404)
        st = proj.students.filter(id=student_id).first()
        if not st:
            return Response({"detail": "Student row not found."}, status=404)

        # Self-heal: if the row is `done` but the file vanished (cleanup ran,
        # storage moved, etc.) try to regenerate on the spot rather than
        # making the user click "Regenerate" and come back.
        path = st.generated_svg
        if not path or not os.path.exists(path):
            try:
                services.generate_card_for_student(st)
                st.refresh_from_db()
                path = st.generated_svg
            except Exception:
                path = None

        if not path or not os.path.exists(path):
            return Response(
                {"detail": "SVG file is missing. Try regenerating this card."},
                status=404,
            )
        return FileResponse(open(path, "rb"), as_attachment=True,
                            filename=f"{st.student_id}.svg",
                            content_type="image/svg+xml")


class ProjectDownloadView(APIView):
    def get(self, request, project_id):
        # Diagnostic: respond to ?probe=1 with a unique marker so we can prove
        # which copy of the code the live runserver is actually executing.
        if request.query_params.get("probe") == "1":
            return Response({"alive": True, "build_marker": "v2-storage-rewire"})
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Project not found or not yours."},
                            status=404)
        buf = BytesIO()
        added = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for st in proj.students.all():
                path = st.generated_svg
                # Same self-heal as single download — if a row is done but
                # the file vanished, regenerate before zipping.
                if not (path and os.path.exists(path)) and st.status == StudentStatus.DONE:
                    try:
                        services.generate_card_for_student(st)
                        st.refresh_from_db()
                        path = st.generated_svg
                    except Exception:
                        path = None
                if path and os.path.exists(path):
                    zf.write(path, arcname=f"{st.student_id}.svg")
                    added += 1
        if added == 0:
            return Response(
                {"detail": "No generated SVGs to download yet. Generate cards first."},
                status=404,
            )
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{proj.name}.zip"'
        return resp


class StudentPhotoView(APIView):
    def get(self, request, project_id, student_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st or not st.photo_path or not os.path.exists(st.photo_path):
            return Response({"detail": "No photo"}, status=404)
        return FileResponse(open(st.photo_path, "rb"))


# ---------------------------------------------------------------------------
# Storage cleanup
# ---------------------------------------------------------------------------

class ProjectOutputsView(APIView):
    def delete(self, request, project_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        out = storage.cards_dir(proj.owner, proj.id)
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        proj.students.update(status=StudentStatus.PENDING, generated_svg=None,
                             generated_pdf=None, generated_eps=None)
        return Response({"detail": "Outputs cleared"})


class StudentOutputsView(APIView):
    def delete(self, request, project_id, student_id):
        proj = _owned_project(request.user, project_id)
        if not proj:
            return Response({"detail": "Not found"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st:
            return Response({"detail": "Not found"}, status=404)
        out = storage.cards_dir(proj.owner, proj.id) / f"{st.id}.svg"
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        st.status = StudentStatus.PENDING
        st.generated_svg = st.generated_pdf = st.generated_eps = None
        st.save(update_fields=["status", "generated_svg", "generated_pdf", "generated_eps"])
        return Response({"detail": "Outputs cleared"})


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def _visible_fonts(user):
    """Each user sees the global font library + anything they uploaded."""
    if user.is_staff or user.is_superuser:
        return Font.objects.all()
    return Font.objects.filter(models.Q(owner=user) | models.Q(is_global=True))


class FontsView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        qs = _visible_fonts(request.user)
        return Response([
            {
                "id": f.id,
                "name": f.name,
                "filename": f.filename,
                "is_global": f.is_global,
                "is_mine": (f.owner_id == request.user.id),
            }
            for f in qs
        ])

    def post(self, request):
        gate = _require_access(request.user)
        if gate is not None: return gate
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "No file"}, status=400)
        wants_global = (
            (request.user.is_staff or request.user.is_superuser)
            and str(request.data.get("is_global", "")).lower() in ("1", "true", "yes")
        )
        if not wants_global:
            block = _enforce_quota(request.user, incoming=f.size)
            if block is not None: return block
        owner = None if wants_global else request.user
        fdir = storage.fonts_dir_for(owner, is_global=wants_global)
        path = fdir / f.name
        with open(path, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        owner = None if wants_global else request.user
        obj, _ = Font.objects.update_or_create(
            owner=owner, filename=f.name,
            defaults={"name": f.name, "path": str(path), "is_global": wants_global},
        )
        if not wants_global:
            storage.refresh_usage(request.user)
        return Response({
            "detail": "uploaded",
            "name": obj.filename,
            "is_global": obj.is_global,
        }, status=201)


class FontDetailView(APIView):
    """Get / delete a font by filename. Global fonts are read-only for users;
    only their owners (or staff) can delete them."""

    def _get(self, request, filename):
        return _visible_fonts(request.user).filter(filename=filename).first()

    def get(self, request, filename):
        obj = self._get(request, filename)
        if not obj or not os.path.exists(obj.path):
            return Response({"detail": "Not found"}, status=404)
        return FileResponse(open(obj.path, "rb"))

    def delete(self, request, filename):
        obj = self._get(request, filename)
        if not obj:
            return Response(status=204)
        is_owner = obj.owner_id == request.user.id
        if obj.is_global and not (request.user.is_staff or request.user.is_superuser):
            return Response({"detail": "Global font; ask an admin."}, status=403)
        if not is_owner and not (request.user.is_staff or request.user.is_superuser):
            return Response({"detail": "Not yours."}, status=403)
        try:
            if obj.path and os.path.exists(obj.path):
                os.remove(obj.path)
        except OSError:
            pass
        obj.delete()
        return Response(status=204)


# ---------------------------------------------------------------------------
# Background removal
# ---------------------------------------------------------------------------

class RemoveBgView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "No file"}, status=400)
        tmpdir = storage.tmp_dir()
        src = str(tmpdir / f"{uuid.uuid4().hex}_{f.name}")
        with open(src, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        try:
            result = generation.remove_photo_background(src)
        except Exception as exc:
            return Response({"detail": f"Background removal failed: {exc}"}, status=400)
        return FileResponse(open(result, "rb"), as_attachment=True,
                            filename="no_bg.png")


@api_view(["GET"])
@permission_classes([])
def health(request):
    return Response({"status": "ok"})
