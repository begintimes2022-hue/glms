from django.db import migrations


def mark_admin_groups(apps, schema_editor):
    GroupProfile = apps.get_model("courses", "GroupProfile")
    GroupProfile.objects.filter(group__name="admins").update(is_admin_group=True)


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0024_groupprofile_is_admin_group"),
    ]

    operations = [
        migrations.RunPython(mark_admin_groups, migrations.RunPython.noop),
    ]
