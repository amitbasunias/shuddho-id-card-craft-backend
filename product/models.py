"""
Product domain models (ported from the FastAPI/SQLAlchemy product into Django ORM).

These are the card-generation product tables: Template, Project, Student. They
live in the same database as the SaaS subscriber/billing tables. The product
User is Django's auth user (we reuse django.contrib.auth.User instead of the
old custom users table).
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"
    COMPLETED = "completed", "Completed"


class StudentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


def _share_token() -> str:
    return secrets.token_urlsafe(16)


class Template(models.Model):
    """Card design.

    A template belongs to a single user UNLESS `is_global` is set, in which
    case it appears in every user's template list (used by the admin to
    publish shared starter designs). Existing rows from before this column
    existed default to global so nothing disappears for legacy customers.
    """
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="templates", null=True, blank=True,
        help_text="Leave blank for a global template (admin-published).",
    )
    is_global = models.BooleanField(
        default=False,
        help_text="If true, this template is visible to every subscriber.",
    )
    original_file = models.CharField(max_length=512)
    converted_svg = models.CharField(max_length=512, blank=True, null=True)
    mapping_json = models.JSONField(blank=True, null=True)
    preview_image = models.CharField(max_length=512, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Font(models.Model):
    """User-uploaded font OR admin-published global font.

    Like Template, `is_global` makes it visible to everyone. The actual file
    lives under PRODUCT_STORAGE_ROOT/fonts/<user_id>/ or .../fonts/global/.
    """
    name = models.CharField(max_length=255)
    filename = models.CharField(max_length=255)
    path = models.CharField(max_length=512)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="fonts", null=True, blank=True,
    )
    is_global = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_global", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "filename"],
                name="unique_owner_filename",
            ),
        ]

    def __str__(self):
        scope = "global" if self.is_global else (self.owner.email if self.owner else "?")
        return f"{self.name} ({scope})"


class Project(models.Model):
    name = models.CharField(max_length=255)
    share_token = models.CharField(max_length=64, unique=True, default=_share_token, db_index=True)
    template = models.ForeignKey(
        Template, on_delete=models.SET_NULL, null=True, blank=True, related_name="projects",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="projects",
    )
    status = models.CharField(max_length=50, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE)
    enabled_fields = models.JSONField(blank=True, null=True)
    fonts_config = models.JSONField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    delivery_date = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Student(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="students", db_index=True)
    name = models.CharField(max_length=255)
    student_id = models.CharField(max_length=100)
    father_name = models.CharField(max_length=255, blank=True, null=True)
    mother_name = models.CharField(max_length=255, blank=True, null=True)
    class_name = models.CharField(max_length=100, blank=True, null=True)
    blood_group = models.CharField(max_length=10, blank=True, null=True)
    address = models.CharField(max_length=500, blank=True, null=True)
    date_of_birth = models.CharField(max_length=50, blank=True, null=True)
    mobile_number = models.CharField(max_length=20, blank=True, null=True)
    session = models.CharField(max_length=100, blank=True, null=True)
    department = models.CharField(max_length=255, blank=True, null=True)
    designation = models.CharField(max_length=255, blank=True, null=True)
    husband_name = models.CharField(max_length=255, blank=True, null=True)
    section = models.CharField(max_length=50, blank=True, null=True)
    roll = models.CharField(max_length=50, blank=True, null=True)
    index_no = models.CharField(max_length=50, blank=True, null=True)
    nid_no = models.CharField(max_length=50, blank=True, null=True)
    joining_date = models.CharField(max_length=50, blank=True, null=True)
    guardians_mobile = models.CharField(max_length=20, blank=True, null=True)
    registration_no = models.CharField(max_length=50, blank=True, null=True)
    extra_fields = models.JSONField(blank=True, null=True)
    photo_path = models.CharField(max_length=512, blank=True, null=True)
    status = models.CharField(max_length=50, choices=StudentStatus.choices, default=StudentStatus.PENDING)
    error_message = models.TextField(blank=True, null=True)
    generated_svg = models.CharField(max_length=512, blank=True, null=True)
    generated_pdf = models.CharField(max_length=512, blank=True, null=True)
    generated_eps = models.CharField(max_length=512, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.student_id})"
