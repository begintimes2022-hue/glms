from django.db import migrations, models
import django.db.models.deletion


def copy_page_section_from_module(apps, schema_editor):
    KnowledgeBasePage = apps.get_model("courses", "KnowledgeBasePage")
    for page in KnowledgeBasePage.objects.select_related("module", "module__section").all():
        if page.module_id and not page.section_id:
            page.section_id = page.module.section_id
            page.save(update_fields=["section"])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("courses", "0012_populate_kb_hierarchy_and_require_module"),
    ]

    operations = [
        migrations.AddField(
            model_name="knowledgebasepage",
            name="section",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pages",
                to="courses.knowledgebasesection",
            ),
        ),
        migrations.RunPython(copy_page_section_from_module, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="knowledgebasepage",
            name="section",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pages",
                to="courses.knowledgebasesection",
            ),
        ),
        migrations.AlterModelOptions(
            name="course",
            options={"verbose_name": "Модуль", "verbose_name_plural": "Модули"},
        ),
        migrations.AlterModelOptions(
            name="knowledgebasepage",
            options={
                "ordering": ["section__order", "order", "title", "id"],
                "verbose_name": "Статья",
                "verbose_name_plural": "Статьи",
            },
        ),
        migrations.RemoveField(
            model_name="knowledgebasepage",
            name="module",
        ),
        migrations.DeleteModel(
            name="KnowledgeBaseModule",
        ),
    ]

