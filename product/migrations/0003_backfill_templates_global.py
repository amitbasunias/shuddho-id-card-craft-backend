"""Flip any pre-existing Template rows to is_global=True.

Before this migration, Template had no owner / is_global columns. After the
schema change every legacy row would otherwise be `owner=NULL, is_global=False`
which would hide it from every user's list. We preserve them as shared
starter designs by setting is_global=True on anything that doesn't have an
owner yet. (Idempotent: re-running this on an already-migrated DB is fine.)
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Template = apps.get_model("product", "Template")
    Template.objects.filter(owner__isnull=True, is_global=False).update(is_global=True)


class Migration(migrations.Migration):
    dependencies = [
        ('product', '0002_template_is_global_template_owner_font'),
    ]
    operations = [
        migrations.RunPython(forwards, reverse_code=migrations.RunPython.noop),
    ]
