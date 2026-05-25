"""
Obsolete: the database router for the old shared-DB-with-FastAPI setup.

After the full port to Django ORM, Django owns the entire schema in one database,
so no routing is needed. This file is intentionally left as a no-op to avoid
import errors if anything still references it. It is NOT registered in settings.
"""
