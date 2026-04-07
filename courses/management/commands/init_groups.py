from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from courses.models import Course, Lesson, Question, GroupProfile

class Command(BaseCommand):
    help = "Initialize LMS groups and permissions"

    def handle(self, *args, **options):
        groups = {
            "admins": {
                "models": [Course, Lesson, Question],
                "perms": ["add", "change", "delete", "view"],
            },
        }

        for group_name, config in groups.items():
            group, _ = Group.objects.get_or_create(name=group_name)

            if "models" in config:
                for model in config["models"]:
                    ct = ContentType.objects.get_for_model(model)
                    for perm in config["perms"]:
                        codename = f"{perm}_{model._meta.model_name}"
                        try:
                            permission = Permission.objects.get(
                                content_type=ct, codename=codename
                            )
                            group.permissions.add(permission)
                        except Permission.DoesNotExist:
                            pass

            self.stdout.write(self.style.SUCCESS(f"Group '{group_name}' ready"))

        tariffs = [
            ("user_min", "User Min"),
            ("user_mid", "User Mid"),
            ("user_max", "User Max"),
        ]
        for code, public_name in tariffs:
            group, _ = Group.objects.get_or_create(name=code)
            GroupProfile.objects.get_or_create(
                group=group,
                defaults={
                    "public_name": public_name,
                    "access_duration_days": 0,
                },
            )
            self.stdout.write(self.style.SUCCESS(f"Tariff '{code}' ready"))
