from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .models import Course, Lesson, LessonAttempt, Question


class BaseCoursesTestCase(TestCase):
    def setUp(self):
        self.group_allowed = Group.objects.create(name="user_min")
        self.group_other = Group.objects.create(name="user_mid")

        self.user = User.objects.create_user(username="student", password="pass12345")
        self.user.groups.add(self.group_allowed)

        self.other_user = User.objects.create_user(username="outsider", password="pass12345")
        self.other_user.groups.add(self.group_other)

        self.superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="pass12345",
        )

    def login_user(self, user):
        self.client.force_login(user)

    def make_course(self, title: str, allowed_group: Group):
        course = Course.objects.create(
            title=title,
            description=f"{title} description",
            created_by=self.superuser,
        )
        course.allowed_groups.add(allowed_group)
        return course

    def make_lesson(self, course: Course, order: int, title: str, is_final: bool = False):
        return Lesson.objects.create(
            course=course,
            title=title,
            content=f"Content for {title}",
            order=order,
            is_final=is_final,
        )


class CourseAccessTests(BaseCoursesTestCase):
    def test_course_list_filtered_by_user_group(self):
        allowed_course = self.make_course("Allowed", self.group_allowed)
        blocked_course = self.make_course("Blocked", self.group_other)

        self.login_user(self.user)
        response = self.client.get(reverse("courses:list"))

        self.assertEqual(response.status_code, 200)
        ids = [item["course"].id for item in response.context["courses"]]
        self.assertIn(allowed_course.id, ids)
        self.assertNotIn(blocked_course.id, ids)

    def test_course_detail_redirects_when_user_has_no_access(self):
        blocked_course = self.make_course("Blocked", self.group_other)

        self.login_user(self.user)
        response = self.client.get(reverse("courses:detail", args=[blocked_course.id]))

        self.assertRedirects(response, reverse("courses:list"))

    def test_superuser_sees_all_courses(self):
        c1 = self.make_course("Course A", self.group_allowed)
        c2 = self.make_course("Course B", self.group_other)

        self.login_user(self.superuser)
        response = self.client.get(reverse("courses:list"))

        self.assertEqual(response.status_code, 200)
        ids = [item["course"].id for item in response.context["courses"]]
        self.assertIn(c1.id, ids)
        self.assertIn(c2.id, ids)


class CourseCompletionRuleTests(BaseCoursesTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course("Completion Course", self.group_allowed)
        self.lesson_material = self.make_lesson(self.course, 1, "Material 1")
        self.lesson_final = self.make_lesson(self.course, 2, "Final", is_final=True)
        self.question_final = Question.objects.create(
            lesson=self.lesson_final,
            question_text="2+2?",
            option_a="3",
            option_b="4",
            option_c="5",
            correct_answer="B",
        )
        self.login_user(self.user)

    def _view_all_materials(self):
        self.client.get(reverse("courses:lesson", args=[self.course.id, self.lesson_material.id]))
        self.client.get(reverse("courses:lesson", args=[self.course.id, self.lesson_final.id]))

    def _pass_final_test(self):
        return self.client.post(
            reverse("courses:lesson_test", args=[self.course.id, self.lesson_final.id]),
            data={f"q_{self.question_final.id}": "B"},
        )

    def test_not_completed_until_all_materials_viewed_and_final_passed(self):
        response = self.client.get(reverse("courses:detail", args=[self.course.id]))
        self.assertFalse(response.context["course_completed"])

        self._view_all_materials()
        response = self.client.get(reverse("courses:detail", args=[self.course.id]))
        self.assertFalse(response.context["course_completed"])

        self._pass_final_test()
        response = self.client.get(reverse("courses:detail", args=[self.course.id]))
        self.assertTrue(response.context["course_completed"])

    def test_final_test_passed_but_not_all_materials_viewed_is_not_completed(self):
        self._pass_final_test()
        response = self.client.get(reverse("courses:detail", args=[self.course.id]))
        self.assertFalse(response.context["course_completed"])


class RetakeAndLockingTests(BaseCoursesTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course("Retake Course", self.group_allowed)
        self.lesson_final = self.make_lesson(self.course, 1, "Final", is_final=True)
        self.question = Question.objects.create(
            lesson=self.lesson_final,
            question_text="Capital of France?",
            option_a="Paris",
            option_b="London",
            option_c="Rome",
            correct_answer="A",
        )
        self.login_user(self.user)

    def test_three_failed_attempts_lock_test(self):
        test_url = reverse("courses:lesson_test", args=[self.course.id, self.lesson_final.id])

        for _ in range(2):
            response = self.client.post(test_url, data={f"q_{self.question.id}": "B"})
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("courses:lesson_result", args=[self.course.id, self.lesson_final.id]), response.url)

        response = self.client.post(test_url, data={f"q_{self.question.id}": "B"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("relearn=1", response.url)

        blocked = self.client.get(test_url)
        self.assertEqual(blocked.status_code, 302)
        self.assertIn("locked=1", blocked.url)

        attempts = LessonAttempt.objects.filter(user=self.user, lesson=self.lesson_final)
        self.assertEqual(attempts.count(), 3)
        self.assertEqual(attempts.filter(retake_round=1).count(), 3)
        self.assertEqual(attempts.filter(passed=False).count(), 3)


class KeyPagesSmokeTests(BaseCoursesTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course("Pages Course", self.group_allowed)
        self.lesson = self.make_lesson(self.course, 1, "Lesson")
        self.question = Question.objects.create(
            lesson=self.lesson,
            question_text="Pick A",
            option_a="A",
            option_b="B",
            option_c="C",
            correct_answer="A",
        )
        self.login_user(self.user)

    def test_core_pages_return_200(self):
        urls = [
            reverse("courses:detail", args=[self.course.id]),
            reverse("courses:lesson", args=[self.course.id, self.lesson.id]),
            reverse("courses:lesson_test", args=[self.course.id, self.lesson.id]),
            reverse("courses:lesson_result", args=[self.course.id, self.lesson.id]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    def test_option_d_is_rendered_and_can_be_submitted(self):
        self.question.option_d = "D"
        self.question.correct_answer = "D"
        self.question.save(update_fields=["option_d", "correct_answer"])

        test_url = reverse("courses:lesson_test", args=[self.course.id, self.lesson.id])
        response = self.client.get(test_url)
        self.assertContains(response, "D) D")

        response = self.client.post(test_url, data={f"q_{self.question.id}": "D"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("courses:lesson_result", args=[self.course.id, self.lesson.id]), response.url)
