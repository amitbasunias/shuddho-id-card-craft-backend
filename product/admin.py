"""Admin views for the product (templates / fonts / projects).

Admin uses these to publish *global* templates and fonts that every subscriber
sees automatically, and to inspect per-user data.
"""
from django.contrib import admin

from .models import Font, Project, Student, Template


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_global", "created_at")
    list_filter = ("is_global", "created_at")
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner",)
    list_editable = ("is_global",)
    fields = (
        "name", "owner", "is_global",
        "original_file", "converted_svg", "preview_image", "mapping_json",
        "created_at",
    )
    readonly_fields = ("created_at",)


@admin.register(Font)
class FontAdmin(admin.ModelAdmin):
    list_display = ("name", "filename", "owner", "is_global", "created_at")
    list_filter = ("is_global",)
    search_fields = ("name", "filename", "owner__email")
    autocomplete_fields = ("owner",)
    list_editable = ("is_global",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "template", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner", "template")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("name", "student_id", "project", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "student_id", "project__name")
    autocomplete_fields = ("project",)
