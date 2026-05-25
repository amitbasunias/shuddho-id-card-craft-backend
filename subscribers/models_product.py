"""
Compatibility helpers bridging the SaaS 'subscribers' app to the real product
data, now that the product is fully ported to Django ORM.

Previously these were read-only mirror models over the FastAPI tables. After the
full port, the product login is Django's auth User and projects/students are
real Django models in the 'product' app. These helpers keep the subscriber admin
working (live usage + create/reset product login) against the real tables.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model

User = get_user_model()


def usage_for_email(email: str) -> dict:
    """Return {'user_id', 'projects', 'students'} for a login email, or zeros."""
    email = (email or "").strip().lower()
    if not email:
        return {"user_id": None, "projects": None, "students": None}
    try:
        from product.models import Project, Student  # local import avoids app-loading order issues
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return {"user_id": None, "projects": 0, "students": 0}
        projects = Project.objects.filter(owner_id=user.id)
        project_ids = list(projects.values_list("id", flat=True))
        students = Student.objects.filter(project_id__in=project_ids).count() if project_ids else 0
        return {"user_id": user.id, "projects": projects.count(), "students": students}
    except Exception:
        return {"user_id": None, "projects": None, "students": None}


def upsert_product_user(email: str, password: str | None = None, role: str = "admin"):
    """
    Create or update a product login (Django auth User).

    Uses Django's proper password hashing (set_password). Returns the User or
    None if something goes wrong. The 'role' arg is accepted for backward
    compatibility; staff/superuser flags are left to the admin.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        user = User.objects.filter(email__iexact=email).first()
        if user:
            if password:
                user.set_password(password)
                user.save(update_fields=["password"])
            return user
        username = email
        user = User(username=username, email=email)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user
    except Exception:
        return None


# Backward-compatible name: some code referenced ProductUser directly.
ProductUser = User
