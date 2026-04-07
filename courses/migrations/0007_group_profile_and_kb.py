from django.db import migrations, models
import django.db.models.deletion


def create_group_profiles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    GroupProfile = apps.get_model("courses", "GroupProfile")

    for group in Group.objects.all():
        GroupProfile.objects.get_or_create(
            group=group,
            defaults={
                "public_name": group.name,
                "access_duration_days": 0,
            },
        )


def remove_group_profiles(apps, schema_editor):
    # No-op reverse migration: keep profiles.
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0006_lesson_is_final"),
    ]

    operations = [
        migrations.CreateModel(
            name="KnowledgeBasePage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=200, unique=True)),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["title", "id"],
            },
        ),
        migrations.CreateModel(
            name="GroupProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_name", models.CharField(max_length=200)),
                ("access_duration_days", models.PositiveIntegerField(default=0)),
                (
                    "group",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to="auth.group"),
                ),
            ],
        ),
        migrations.AddField(
            model_name="groupprofile",
            name="allowed_kb_pages",
            field=models.ManyToManyField(blank=True, related_name="allowed_groups", to="courses.knowledgebasepage"),
        ),
        migrations.RunPython(create_group_profiles, remove_group_profiles),
    ]
