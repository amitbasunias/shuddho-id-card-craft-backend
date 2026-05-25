"""Product API routes (mirror the original FastAPI paths under /api/)."""

from django.urls import path

from . import client_views as cv
from . import views as v

urlpatterns = [
    path("health", v.health),

    # Templates
    path("templates", v.TemplateListView.as_view()),
    path("templates/upload", v.TemplateUploadView.as_view()),
    path("templates/create", v.TemplateCreateView.as_view()),
    path("templates/<int:template_id>", v.TemplateDetailView.as_view()),
    path("templates/<int:template_id>/svg", v.TemplateSvgView.as_view()),
    path("templates/<int:template_id>/mapping", v.TemplateMappingView.as_view()),

    # Projects
    path("projects", v.ProjectListView.as_view()),
    path("projects/<int:project_id>", v.ProjectDetailView.as_view()),

    # Students + generation
    path("projects/<int:project_id>/students", v.ProjectStudentsView.as_view()),
    path("projects/<int:project_id>/students/bulk-delete", v.StudentBulkDeleteView.as_view()),
    path("projects/<int:project_id>/students/<int:student_id>", v.StudentDetailView.as_view()),
    path("projects/<int:project_id>/students/<int:student_id>/reprocess", v.ReprocessStudentView.as_view()),
    path("projects/<int:project_id>/students/<int:student_id>/photo", v.StudentPhotoView.as_view()),
    path("projects/<int:project_id>/students/<int:student_id>/download", v.StudentDownloadView.as_view()),
    path("projects/<int:project_id>/students/<int:student_id>/outputs", v.StudentOutputsView.as_view()),
    path("projects/<int:project_id>/generate", v.GenerateView.as_view()),
    path("projects/<int:project_id>/status", v.GenerationStatusView.as_view()),
    path("projects/<int:project_id>/download", v.ProjectDownloadView.as_view()),
    path("projects/<int:project_id>/outputs", v.ProjectOutputsView.as_view()),

    # Fonts
    path("fonts", v.FontsView.as_view()),
    path("fonts/<str:filename>", v.FontDetailView.as_view()),

    # Utility
    path("remove-bg", v.RemoveBgView.as_view()),

    # Public client (share-token) endpoints
    path("client/<str:token>/info", cv.ClientInfoView.as_view()),
    path("client/<str:token>/students", cv.ClientStudentsView.as_view()),
    path("client/<str:token>/student", cv.ClientSubmitStudentView.as_view()),
    path("client/<str:token>/bulk-upload", cv.ClientBulkUploadView.as_view()),
    path("client/<str:token>/students/<int:student_id>", cv.ClientStudentUpdateView.as_view()),
    path("client/<str:token>/students/<int:student_id>/photo", cv.ClientStudentPhotoView.as_view()),
]
