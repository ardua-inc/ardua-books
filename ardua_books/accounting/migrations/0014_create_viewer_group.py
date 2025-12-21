"""
Data migration to create the Viewer group for read-only users.
"""
from django.db import migrations


def create_viewer_group(apps, schema_editor):
    """Create the Viewer group."""
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Viewer")


def remove_viewer_group(apps, schema_editor):
    """Remove the Viewer group."""
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Viewer").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0013_add_transfer_pair"),
    ]

    operations = [
        migrations.RunPython(create_viewer_group, remove_viewer_group),
    ]
