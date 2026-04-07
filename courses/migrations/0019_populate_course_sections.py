from django.db import migrations


def populate_course_sections(apps, schema_editor):
    Course = apps.get_model("courses", "Course")
    KnowledgeBaseSection = apps.get_model("courses", "KnowledgeBaseSection")

    fallback_section, _ = KnowledgeBaseSection.objects.get_or_create(
        slug="obshchii-razdel",
        defaults={
            "title": "Общий раздел",
            "description": "",
            "order": 0,
        },
    )

    for course in Course.objects.filter(section__isnull=True).all():
        related_section = (
            KnowledgeBaseSection.objects
            .filter(courses=course)
            .order_by("order", "title", "id")
            .first()
        )
        course.section_id = related_section.id if related_section else fallback_section.id
        course.save(update_fields=["section"])


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0018_course_section_groupprofile_allowed_kb_lessons"),
    ]

    operations = [
        migrations.RunPython(populate_course_sections, migrations.RunPython.noop),
    ]
