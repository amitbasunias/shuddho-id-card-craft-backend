"""
Public, share-token client views — the customer-facing upload page where a
project's clients submit student data + photos without logging in. Identified by
the project's share_token. No JWT required.

The client-upload page branding is driven by the project's owning subscriber
(organization name) instead of a hardcoded logo (SaaS requirement #2).
"""

import csv
import io
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from subscribers.models import Subscriber

from .models import Project, Student
from .serializers import StudentSerializer, StudentWriteSerializer


def _root() -> Path:
    return Path(settings.PRODUCT_STORAGE_ROOT)


def _project_by_token(token):
    return Project.objects.filter(share_token=token).first()


def _branding_for(project):
    """Subscriber-driven branding for the client page (no hardcoded logo)."""
    sub = Subscriber.objects.filter(product_user_id=project.owner_id).first()
    if not sub:
        sub = Subscriber.objects.filter(email__iexact=getattr(project.owner, "email", "")).first()
    return {
        "organization": (sub.organization or sub.full_name) if sub else project.owner.get_username(),
        "contact_email": sub.email if sub else "",
        "contact_phone": sub.phone if sub else "",
    }


class ClientInfoView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        proj = _project_by_token(token)
        if not proj:
            return Response({"detail": "Invalid link"}, status=404)
        return Response({
            "name": proj.name,
            "description": proj.description,
            "enabled_fields": proj.enabled_fields,
            "delivery_date": proj.delivery_date,
            "status": proj.status,
            "branding": _branding_for(proj),
        })


class ClientStudentsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        proj = _project_by_token(token)
        if not proj:
            return Response({"detail": "Invalid link"}, status=404)
        return Response(StudentSerializer(proj.students.all().order_by("id"), many=True).data)


class ClientStudentPhotoView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token, student_id):
        proj = _project_by_token(token)
        if not proj:
            return Response({"detail": "Invalid link"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st or not st.photo_path or not os.path.exists(st.photo_path):
            return Response({"detail": "No photo"}, status=404)
        return FileResponse(open(st.photo_path, "rb"))


class ClientStudentUpdateView(APIView):
    permission_classes = [AllowAny]

    def put(self, request, token, student_id):
        proj = _project_by_token(token)
        if not proj:
            return Response({"detail": "Invalid link"}, status=404)
        st = proj.students.filter(id=student_id).first()
        if not st:
            return Response({"detail": "Not found"}, status=404)
        ser = StudentWriteSerializer(st, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(StudentSerializer(st).data)


class ClientSubmitStudentView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, token):
        proj = _project_by_token(token)
        if not proj:
            return Response({"detail": "Invalid link"}, status=404)
        data = {k: v for k, v in request.data.items() if k != "photo"}
        ser = StudentWriteSerializer(data=data, partial=True)
        ser.is_valid(raise_exception=True)
        st = Student.objects.create(project=proj, **ser.validated_data)
        photo = request.FILES.get("photo")
        if photo:
            from . import storage
            # Block client upload if the project's OWNER is over quota.
            try:
                storage.check_quota(proj.owner, incoming=photo.size)
            except storage.QuotaExceeded as exc:
                return Response({
                    "detail": "The institution's storage is full. "
                              "Please contact them so they can free up space.",
                    "code": "quota_exceeded",
                }, status=413)
            pdir = storage.uploads_dir(proj.owner, proj.id)
            ext = os.path.splitext(photo.name)[1].lower() or ".png"
            ppath = str(pdir / f"{uuid.uuid4().hex}{ext}")
            with open(ppath, "wb") as out:
                for chunk in photo.chunks():
                    out.write(chunk)
            st.photo_path = ppath
            st.save(update_fields=["photo_path"])
            storage.refresh_usage(proj.owner)
        return Response(StudentSerializer(st).data, status=201)


class ClientBulkUploadView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    _FIELDS = {
        "name", "student_id", "father_name", "mother_name", "class_name",
        "blood_group", "address", "date_of_birth", "mobile_number", "session",
        "department", "designation", "husband_name", "section", "roll",
        "index_no", "nid_no", "joining_date", "guardians_mobile", "registration_no",
    }

    def post(self, request, token):
        proj = _project_by_token(token)
        if not proj:
            return Response({"detail": "Invalid link"}, status=404)
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "No CSV uploaded"}, status=400)
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            text = f.read().decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        created, failed, errors = 0, 0, []
        for i, row in enumerate(reader, start=2):
            try:
                clean = {k.strip(): (v or "").strip() for k, v in row.items()
                         if k and k.strip() in self._FIELDS}
                if not clean.get("name") and not clean.get("student_id"):
                    raise ValueError("missing name/student_id")
                Student.objects.create(project=proj, **clean)
                created += 1
            except Exception as exc:
                failed += 1
                errors.append(f"Row {i}: {exc}")
        return Response({"created": created, "failed": failed, "errors": errors[:50]})
