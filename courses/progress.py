from dataclasses import dataclass
from typing import Any

from django.db.models import Exists, OuterRef, Subquery

from .models import (
    Lesson,
    LessonAttempt,
    LessonView,
    Question,
    LearningCourseItem,
    LearningCourseFinalQuestion,
    LearningCourseFinalAttempt,
)


def annotate_lessons_with_user_progress(lessons_qs, user):
    has_test_sq = Question.objects.filter(lesson=OuterRef("pk"))
    viewed_sq = LessonView.objects.filter(user=user, lesson=OuterRef("pk"))
    attempted_sq = LessonAttempt.objects.filter(user=user, lesson=OuterRef("pk"))
    best_attempt = (
        LessonAttempt.objects.filter(user=user, lesson=OuterRef("pk")).order_by("-score", "-created_at")
    )
    return lessons_qs.annotate(
        has_test=Exists(has_test_sq),
        viewed=Exists(viewed_sq),
        attempted=Exists(attempted_sq),
        best_score=Subquery(best_attempt.values("score")[:1]),
        best_passed=Subquery(best_attempt.values("passed")[:1]),
    )


def build_course_progress(lessons):
    lessons_list = list(lessons)
    total_lessons = len(lessons_list)
    viewed_count = sum(1 for lesson in lessons_list if bool(getattr(lesson, "viewed", False)))
    viewed_percent = int((viewed_count / total_lessons) * 100) if total_lessons else 0

    # Курс считается завершенным по фактическим материалам:
    # все уроки просмотрены + все существующие тесты пройдены.
    lessons_with_tests = [lesson for lesson in lessons_list if bool(getattr(lesson, "has_test", False))]
    all_tests_passed = all(bool(getattr(lesson, "best_passed", False)) for lesson in lessons_with_tests)
    completed_articles = viewed_count
    completed_tests = sum(1 for lesson in lessons_with_tests if bool(getattr(lesson, "best_passed", False)))
    total_items = total_lessons + len(lessons_with_tests)
    completed_items = completed_articles + completed_tests
    progress_percent = int((completed_items / total_items) * 100) if total_items else 0

    final_lessons = [lesson for lesson in lessons_list if bool(getattr(lesson, "is_final", False))]
    final_test_passed = bool(final_lessons) and all(
        bool(getattr(lesson, "has_test", False)) and bool(getattr(lesson, "best_passed", False))
        for lesson in final_lessons
    )
    final_scores = [
        int(getattr(lesson, "best_score", 0) or 0)
        for lesson in final_lessons
        if getattr(lesson, "best_score", None) is not None
    ]
    final_test_percent = int(sum(final_scores) / len(final_scores)) if final_scores else 0

    course_completed = progress_percent == 100 and final_test_passed

    return {
        "lessons": lessons_list,
        "total_lessons": total_lessons,
        "viewed_count": viewed_count,
        "viewed_percent": viewed_percent,
        "total_items": total_items,
        "completed_items": completed_items,
        "progress_percent": progress_percent,
        "completed_articles": completed_articles,
        "completed_tests": completed_tests,
        "tests_total": len(lessons_with_tests),
        "tests_passed": sum(1 for lesson in lessons_with_tests if bool(getattr(lesson, "best_passed", False))),
        "all_tests_passed": all_tests_passed,
        "has_final_material": bool(final_lessons),
        "final_test_passed": final_test_passed,
        "final_test_percent": final_test_percent,
        "course_completed": course_completed,
    }


@dataclass
class ExpandedLearningCourseItem:
    sequence_index: int
    kind: str
    title: str
    lesson: Lesson | None = None
    module: Any = None
    section: Any = None
    source_item: LearningCourseItem | None = None
    source_type: str = ""
    has_test: bool = False
    viewed: bool = False
    attempted: bool = False
    best_score: int | None = None
    best_passed: bool = False
    is_completed: bool = False
    is_available: bool = False
    access_state: str = "locked"
    is_final_test: bool = False
    learning_course: Any = None


def expand_learning_course_items(learning_course, user):
    raw_items = list(
        learning_course.items.select_related(
            "section",
            "module",
            "module__section",
            "lesson",
            "lesson__course",
            "lesson__course__section",
        ).order_by("order_index", "id")
    )
    section_ids = [item.section_id for item in raw_items if item.item_type == LearningCourseItem.ITEM_SECTION and item.section_id]
    module_ids = [item.module_id for item in raw_items if item.item_type == LearningCourseItem.ITEM_MODULE and item.module_id]
    lesson_ids = [item.lesson_id for item in raw_items if item.lesson_id]

    section_lessons_map = {}
    if section_ids:
        section_lessons = (
            Lesson.objects
            .filter(course__section_id__in=section_ids)
            .select_related("course", "course__section")
            .order_by("course__section_id", "course__order", "course__title", "course_id", "order", "id")
        )
        section_lessons = list(annotate_lessons_with_user_progress(section_lessons, user))
        for lesson in section_lessons:
            section_lessons_map.setdefault(lesson.course.section_id, []).append(lesson)

    module_lessons_map = {}
    if module_ids:
        module_lessons = (
            Lesson.objects
            .filter(course_id__in=module_ids)
            .select_related("course", "course__section")
            .order_by("course__order", "course_id", "order", "id")
        )
        module_lessons = list(annotate_lessons_with_user_progress(module_lessons, user))
        for lesson in module_lessons:
            module_lessons_map.setdefault(lesson.course_id, []).append(lesson)

    lessons_map = {}
    if lesson_ids:
        annotated_lessons = (
            Lesson.objects
            .filter(id__in=lesson_ids)
            .select_related("course", "course__section")
        )
        annotated_lessons = list(annotate_lessons_with_user_progress(annotated_lessons, user))
        lessons_map = {lesson.id: lesson for lesson in annotated_lessons}

    expanded = []

    def append_expanded_lesson(lesson, source_item, source_type, module, section):
        expanded.append(
            ExpandedLearningCourseItem(
                sequence_index=len(expanded) + 1,
                kind=LearningCourseItem.ITEM_ARTICLE,
                title=lesson.title,
                lesson=lesson,
                module=module,
                section=section,
                source_item=source_item,
                source_type=source_type,
                has_test=bool(getattr(lesson, "has_test", False)),
                viewed=bool(getattr(lesson, "viewed", False)),
                attempted=bool(getattr(lesson, "attempted", False)),
                best_score=getattr(lesson, "best_score", None),
                best_passed=bool(getattr(lesson, "best_passed", False)),
                learning_course=learning_course,
            )
        )
        if bool(getattr(lesson, "has_test", False)):
            expanded.append(
                ExpandedLearningCourseItem(
                    sequence_index=len(expanded) + 1,
                    kind=LearningCourseItem.ITEM_TEST,
                    title=lesson.title,
                    lesson=lesson,
                    module=module,
                    section=section,
                    source_item=source_item,
                    source_type=source_type,
                    has_test=True,
                    viewed=bool(getattr(lesson, "viewed", False)),
                    attempted=bool(getattr(lesson, "attempted", False)),
                    best_score=getattr(lesson, "best_score", None),
                    best_passed=bool(getattr(lesson, "best_passed", False)),
                    learning_course=learning_course,
                )
            )

    for item in raw_items:
        if item.item_type == LearningCourseItem.ITEM_SECTION and item.section_id:
            for lesson in section_lessons_map.get(item.section_id, []):
                append_expanded_lesson(
                    lesson=lesson,
                    source_item=item,
                    source_type=item.item_type,
                    module=lesson.course,
                    section=item.section,
                )
            continue

        if item.item_type == LearningCourseItem.ITEM_MODULE and item.module_id:
            for lesson in module_lessons_map.get(item.module_id, []):
                append_expanded_lesson(
                    lesson=lesson,
                    source_item=item,
                    source_type=item.item_type,
                    module=item.module,
                    section=getattr(item.module, "section", None),
                )
            continue

        lesson = lessons_map.get(item.lesson_id)
        if not lesson:
            continue

        expanded.append(
            ExpandedLearningCourseItem(
                sequence_index=len(expanded) + 1,
                kind=item.item_type,
                title=lesson.title,
                lesson=lesson,
                module=lesson.course,
                section=getattr(lesson.course, "section", None),
                source_item=item,
                source_type=item.item_type,
                has_test=bool(getattr(lesson, "has_test", False)),
                viewed=bool(getattr(lesson, "viewed", False)),
                attempted=bool(getattr(lesson, "attempted", False)),
                best_score=getattr(lesson, "best_score", None),
                best_passed=bool(getattr(lesson, "best_passed", False)),
                is_final_test=item.is_final_test,
                learning_course=learning_course,
            )
        )

    final_questions_exist = LearningCourseFinalQuestion.objects.filter(learning_course=learning_course).exists()
    if final_questions_exist:
        final_attempt = (
            LearningCourseFinalAttempt.objects
            .filter(user=user, learning_course=learning_course)
            .order_by("-score", "-created_at")
            .first()
        )
        expanded.append(
            ExpandedLearningCourseItem(
                sequence_index=len(expanded) + 1,
                kind="final_test",
                title="Итоговый тест курса",
                source_type="final_test",
                best_score=getattr(final_attempt, "score", None),
                best_passed=bool(getattr(final_attempt, "passed", False)),
                attempted=final_attempt is not None,
                is_final_test=True,
                learning_course=learning_course,
            )
        )

    article_test_lessons = {}
    for entry in expanded:
        if entry.lesson and entry.kind != "final_test" and entry.has_test:
            article_test_lessons[entry.lesson.id] = entry

    article_test_scores = [
        int(item.best_score or 0)
        for item in article_test_lessons.values()
    ]
    article_tests_average = (
        int(sum(article_test_scores) / len(article_test_scores))
        if article_test_scores else 100
    )
    article_tests_threshold_met = article_tests_average >= 85

    previous_completed = True
    for entry in expanded:
        if entry.kind in {LearningCourseItem.ITEM_TEST, "final_test"}:
            entry.is_completed = entry.best_passed
        else:
            entry.is_completed = entry.viewed

        entry.is_available = entry.is_completed or previous_completed
        if entry.kind == "final_test" and not article_tests_threshold_met:
            entry.is_available = entry.is_completed

        if entry.is_completed:
            entry.access_state = "completed"
        elif entry.is_available:
            entry.access_state = "available"
        else:
            entry.access_state = "locked"

        previous_completed = entry.is_completed

    return expanded


def build_learning_course_progress(expanded_items):
    items = list(expanded_items)
    total_items = len(items)
    completed_items = sum(1 for item in items if item.is_completed)
    progress_percent = int((completed_items / total_items) * 100) if total_items else 0
    final_test = next((item for item in items if item.is_final_test), None)
    final_test_passed = bool(final_test and final_test.is_completed)
    article_test_lessons = {}
    for item in items:
        if item.lesson and item.kind != "final_test" and item.has_test:
            article_test_lessons[item.lesson.id] = item
    article_tests_scores = [int(item.best_score or 0) for item in article_test_lessons.values()]
    article_tests_average = int(sum(article_tests_scores) / len(article_tests_scores)) if article_tests_scores else 100
    article_tests_threshold_met = article_tests_average >= 85
    return {
        "items": items,
        "total_items": total_items,
        "completed_items": completed_items,
        "progress_percent": progress_percent,
        "has_final_test": final_test is not None,
        "final_test_passed": final_test_passed,
        "article_tests_average": article_tests_average,
        "article_tests_threshold_met": article_tests_threshold_met,
        "course_completed": total_items > 0 and progress_percent == 100 and final_test_passed,
    }
