from django.db import migrations, models
import django.db.models.deletion


def populate_kb_hierarchy(apps, schema_editor):
    KnowledgeBaseSection = apps.get_model("courses", "KnowledgeBaseSection")
    KnowledgeBaseModule = apps.get_model("courses", "KnowledgeBaseModule")
    KnowledgeBasePage = apps.get_model("courses", "KnowledgeBasePage")

    default_section, _ = KnowledgeBaseSection.objects.get_or_create(
        slug="general",
        defaults={
            "title": "Общий раздел",
            "description": "Раздел по умолчанию для существующих статей.",
            "order": 0,
        },
    )
    default_module, _ = KnowledgeBaseModule.objects.get_or_create(
        section=default_section,
        slug="general-module",
        defaults={
            "title": "Общий модуль",
            "description": "Модуль по умолчанию для существующих статей.",
            "order": 0,
        },
    )
    KnowledgeBasePage.objects.filter(module__isnull=True).update(module=default_module)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("courses", "0011_knowledgebasemodule_knowledgebasesection_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_kb_hierarchy, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="knowledgebasepage",
            name="module",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pages",
                to="courses.knowledgebasemodule",
            ),
        ),
    ]
