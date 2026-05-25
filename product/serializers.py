"""DRF serializers for the product (templates, projects, students)."""

from rest_framework import serializers

from .models import Project, Student, Template


class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = (
            "id", "name", "original_file", "converted_svg",
            "mapping_json", "preview_image", "created_at",
            "is_global",
        )
        read_only_fields = ("id", "created_at", "is_global")


class TemplateMappingUpdateSerializer(serializers.Serializer):
    mapping_json = serializers.JSONField()


class TemplateCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    svg_content = serializers.CharField()
    mapping_json = serializers.JSONField(required=False)


class StudentSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField(source="project.id", read_only=True)

    class Meta:
        model = Student
        fields = (
            "id", "project_id", "name", "student_id", "father_name", "mother_name",
            "class_name", "blood_group", "address", "date_of_birth", "mobile_number",
            "session", "department", "designation", "husband_name", "section",
            "roll", "index_no", "nid_no", "joining_date", "guardians_mobile",
            "registration_no", "extra_fields", "photo_path", "status",
            "error_message", "generated_svg", "generated_pdf", "generated_eps",
            "created_at",
        )
        read_only_fields = (
            "id", "project_id", "status", "error_message",
            "generated_svg", "generated_pdf", "generated_eps", "created_at",
        )


# Editable subset for create/update (mirrors StudentCreate/StudentUpdate).
_STUDENT_EDITABLE = (
    "name", "student_id", "father_name", "mother_name", "class_name",
    "blood_group", "address", "date_of_birth", "mobile_number", "session",
    "department", "designation", "husband_name", "section", "roll", "index_no",
    "nid_no", "joining_date", "guardians_mobile", "registration_no", "extra_fields",
)


class StudentWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = _STUDENT_EDITABLE
        extra_kwargs = {f: {"required": False, "allow_null": True} for f in _STUDENT_EDITABLE}


class ProjectSerializer(serializers.ModelSerializer):
    template_id = serializers.IntegerField(source="template.id", read_only=True)
    owner_id = serializers.IntegerField(source="owner.id", read_only=True)
    student_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = (
            "id", "name", "share_token", "template_id", "owner_id", "status",
            "enabled_fields", "fonts_config", "description", "delivery_date",
            "created_at", "student_count",
        )
        read_only_fields = ("id", "share_token", "owner_id", "created_at")

    def get_student_count(self, obj):
        return obj.students.count()


class ProjectCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    template_id = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    # Project-creation form on the dashboard lets the user pick which student
    # fields are visible on the client-upload page, set a delivery date, and
    # configure fonts up-front. These were missing here, so the values were
    # silently dropped and every new project showed all fields on /upload.
    enabled_fields = serializers.JSONField(required=False, allow_null=True)
    fonts_config = serializers.JSONField(required=False, allow_null=True)
    delivery_date = serializers.DateTimeField(required=False, allow_null=True)


class ProjectUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    template_id = serializers.IntegerField(required=False, allow_null=True)
    status = serializers.CharField(required=False)
    enabled_fields = serializers.JSONField(required=False)
    fonts_config = serializers.JSONField(required=False)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    delivery_date = serializers.DateTimeField(required=False, allow_null=True)


class ProjectInfoPublicSerializer(serializers.Serializer):
    """Public, share-token view of a project for the client upload page."""
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    enabled_fields = serializers.JSONField(allow_null=True)
    delivery_date = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()
