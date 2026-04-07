from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib import messages
from django.db.models import Max, Prefetch
from django.db import transaction
from django.urls import reverse
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.html import strip_tags
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank, SearchVector

from .models import (
    Course,
    Lesson,
    Question,
    LessonAttempt,
    LessonView,
    AttemptAnswer,
    LearningCourse,
    LearningCourseFinalQuestion,
    LearningCourseFinalAttempt,
    LearningCourseFinalAnswer,
    KnowledgeBaseSection,
    GroupProfile,
    UserAccess,
    PaymentOrder,
)
from .forms import RegistrationForm
from .payments import (
    PaymentGatewayError,
    activate_payment_order,
    find_payment_order_for_return,
    get_payment_order_status,
    register_payment_order,
)
from .progress import (
    annotate_lessons_with_user_progress,
    build_course_progress,
    build_learning_course_progress,
    expand_learning_course_items,
)

PASS_THRESHOLD = 85  # %
MAX_ATTEMPTS_PER_ROUND = 3


def _user_can_access_course(user, course: Course) -> bool:
    if user.is_superuser:
        return True
    return course.allowed_groups.filter(id__in=user.groups.all()).exists()


def _user_can_access_learning_course(user, learning_course: LearningCourse) -> bool:
    if user.is_superuser:
        return True
    return learning_course.allowed_groups.filter(id__in=user.groups.all()).exists()


def _is_admin_or_superuser(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(profile__is_admin_group=True).exists()


def _courses_access_state(user):
    if _is_admin_or_superuser(user):
        return "active", ""

    user_access, _ = UserAccess.objects.get_or_create(user=user)
    if not user_access.paid:
        return "unpaid", "Доступ откроется после оплаты"

    now = timezone.now()
    if user_access.access_end_at and user_access.access_end_at < now:
        return "expired", "Срок доступа истёк"

    return "active", ""


def _user_allowed_kb_lesson_ids(user):
    if user.is_superuser:
        return set(Lesson.objects.values_list("id", flat=True))

    profiles = GroupProfile.objects.filter(group__in=user.groups.all()).distinct()
    lesson_ids = set(profiles.values_list("allowed_kb_lessons__id", flat=True))
    course_ids = _user_allowed_kb_course_ids(user)
    section_ids = set(profiles.values_list("allowed_kb_sections__id", flat=True))

    if course_ids:
        lesson_ids.update(
            Lesson.objects.filter(course_id__in=course_ids).values_list("id", flat=True)
        )
    if section_ids:
        lesson_ids.update(
            Lesson.objects.filter(course__section_id__in=section_ids).values_list("id", flat=True)
        )

    lesson_ids.discard(None)
    return lesson_ids


def _user_allowed_kb_course_ids(user):
    if user.is_superuser:
        return set(Course.objects.values_list("id", flat=True))

    profiles = GroupProfile.objects.filter(group__in=user.groups.all()).distinct()
    course_ids = set(profiles.values_list("allowed_kb_courses__id", flat=True))
    section_ids = set(profiles.values_list("allowed_kb_sections__id", flat=True))
    if section_ids:
        course_ids.update(
            Course.objects
            .filter(section_id__in=section_ids)
            .values_list("id", flat=True)
        )
    course_ids.discard(None)
    return course_ids


def _user_allowed_kb_courses(user):
    if user.is_superuser:
        return (
            Course.objects
            .select_related("section")
            .prefetch_related(Prefetch("lessons", queryset=Lesson.objects.order_by("order", "id")))
            .order_by("title", "id")
        )
    course_ids = _user_allowed_kb_course_ids(user)
    return (
        Course.objects
        .filter(id__in=course_ids)
        .distinct()
        .select_related("section")
        .prefetch_related(Prefetch("lessons", queryset=Lesson.objects.order_by("order", "id")))
        .order_by("title", "id")
    )


def _user_has_kb_access(user) -> bool:
    if not user.is_authenticated:
        return False
    if _is_admin_or_superuser(user):
        return True
    access_state, _ = _courses_access_state(user)
    if access_state != "active":
        return False
    profiles = GroupProfile.objects.filter(group__in=user.groups.all()).distinct()
    return (
        profiles.filter(allowed_kb_lessons__isnull=False).exists()
        or profiles.filter(allowed_kb_courses__isnull=False).exists()
        or profiles.filter(allowed_kb_sections__isnull=False).exists()
    )


def _base_user_context(request, *, active_menu: str):
    return {
        "active_menu": active_menu,
        "has_kb_access": _user_has_kb_access(request.user),
    }


def _unlocked_lesson_ids_for_user(courses, user):
    unlocked_ids = set()
    for course in courses:
        lessons = Lesson.objects.filter(course=course).order_by("order", "id")
        lessons = list(annotate_lessons_with_user_progress(lessons, user))
        prev_viewed = True
        for lesson in lessons:
            if prev_viewed:
                unlocked_ids.add(lesson.id)
            prev_viewed = bool(getattr(lesson, "viewed", False))
    return unlocked_ids


def _get_current_round(user, lesson: Lesson) -> int:
    """
    Текущий цикл пересдачи = max(retake_round) среди попыток пользователя по этому уроку,
    либо 1, если попыток ещё не было.
    """
    r = (
        LessonAttempt.objects.filter(user=user, lesson=lesson)
        .aggregate(m=Max("retake_round"))
        .get("m")
    )
    return int(r) if r else 1


def _get_current_course_final_round(user, learning_course: LearningCourse) -> int:
    r = (
        LearningCourseFinalAttempt.objects.filter(user=user, learning_course=learning_course)
        .aggregate(m=Max("retake_round"))
        .get("m")
    )
    return int(r) if r else 1


def register(request):
    if request.user.is_authenticated:
        return redirect("courses:profile")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    tariff_group = form.cleaned_data["tariff_code"]
                    payment_order = register_payment_order(request, user, tariff_group)
            except PaymentGatewayError as exc:
                form.add_error("tariff_code", str(exc))
            else:
                return redirect(payment_order.gateway_form_url)
    else:
        form = RegistrationForm()

    return render(
        request,
        "registration/register.html",
        {
            "form": form,
        },
    )


def register_success(request):
    if request.user.is_authenticated:
        return redirect("courses:profile")
    return render(request, "registration/register_success.html")


def payment_return(request):
    order = find_payment_order_for_return(request)
    if not order:
        return render(
            request,
            "registration/payment_return.html",
            {
                "status": "error",
                "page_title": "Платёж не найден",
                "page_subtitle": "Не удалось определить заказ оплаты по параметрам возврата.",
            },
        )

    try:
        status_code, status_payload = get_payment_order_status(order)
        order.response_payload = {
            **(order.response_payload or {}),
            "status_check": status_payload,
        }
        order.save(update_fields=["response_payload"])

        if status_code != 2:
            return render(
                request,
                "registration/payment_return.html",
                {
                    "status": "pending",
                    "page_title": "Оплата не прошла",
                    "page_subtitle": "",
                    "order": order,
                    "gateway_status_code": status_code,
                },
            )

        was_paid = order.status == PaymentOrder.STATUS_PAID
        order = activate_payment_order(order)
    except PaymentGatewayError as exc:
        return render(
            request,
            "registration/payment_return.html",
            {
                "status": "error",
                "page_title": "Оплата не прошла",
                "page_subtitle": "",
                "order": order,
            },
        )

    login(request, order.user, backend="django.contrib.auth.backends.ModelBackend")

    return render(
        request,
        "registration/payment_return.html",
        {
            "status": "success",
            "page_title": "Оплата подтверждена",
            "page_subtitle": "Доступ к материалам активирован.",
            "order": order,
            "already_paid": was_paid,
            **_base_user_context(request, active_menu="profile"),
        },
    )


def _round_stats(user, lesson: Lesson, retake_round: int):
    """
    Возвращает: (failed_count, passed_exists)
    """
    qs = LessonAttempt.objects.filter(user=user, lesson=lesson, retake_round=retake_round)
    failed_count = qs.filter(passed=False).count()
    passed_exists = qs.filter(passed=True).exists()
    return failed_count, passed_exists


def _course_final_round_stats(user, learning_course: LearningCourse, retake_round: int):
    qs = LearningCourseFinalAttempt.objects.filter(
        user=user,
        learning_course=learning_course,
        retake_round=retake_round,
    )
    failed_count = qs.filter(passed=False).count()
    passed_exists = qs.filter(passed=True).exists()
    return failed_count, passed_exists


@login_required
def learning_course_list(request):
    access_state, access_message = _courses_access_state(request.user)
    if access_state != "active":
        return render(
            request,
            "courses/learning_course_list.html",
            {
                "courses": [],
                "course_access_restricted": True,
                "course_access_message": access_message,
                **_base_user_context(request, active_menu="learning_courses"),
            },
        )

    query = request.GET.get("q", "").strip()

    if request.user.is_superuser:
        courses_qs = LearningCourse.objects.all().order_by("title", "id")
    else:
        courses_qs = (
            LearningCourse.objects
            .filter(allowed_groups__in=request.user.groups.all())
            .distinct()
            .order_by("title", "id")
        )

    courses_qs = list(courses_qs)
    courses = []
    search_results = []

    for course in courses_qs:
        expanded_items = expand_learning_course_items(course, request.user)
        progress = build_learning_course_progress(expanded_items)
        if progress["course_completed"]:
            status = "completed"
        elif progress["completed_items"] > 0:
            status = "in_progress"
        else:
            status = "not_started"
        courses.append({
            "course": course,
            "status": status,
            "progress_percent": progress["progress_percent"],
            "completed_items": progress["completed_items"],
            "total_items": progress["total_items"],
        })

        if query:
            query_lc = query.lower()
            for item in expanded_items:
                if not item.is_available or not item.lesson:
                    continue
                haystack = f"{item.title}\n{item.lesson.content}".lower()
                if query_lc not in haystack:
                    continue

                content_text = strip_tags(item.lesson.content or "")
                start = content_text.lower().find(query_lc)
                if start < 0:
                    start = 0
                snippet_start = max(0, start - 60)
                snippet_end = min(len(content_text), start + max(len(query), 1) + 120)
                snippet = content_text[snippet_start:snippet_end].strip()
                if snippet_start > 0:
                    snippet = "... " + snippet
                if snippet_end < len(content_text):
                    snippet += " ..."

                search_results.append({
                    "course": course,
                    "item": item,
                    "snippet": snippet,
                })

    return render(
        request,
        "courses/learning_course_list.html",
        {
            "courses": courses,
            "search_query": query,
            "search_results": search_results,
            "course_access_restricted": False,
            **_base_user_context(request, active_menu="learning_courses"),
        },
    )


@login_required
def learning_course_detail(request, learning_course_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:learning_list")

    learning_course = get_object_or_404(LearningCourse, pk=learning_course_id)
    if not _user_can_access_learning_course(request.user, learning_course):
        return redirect("courses:learning_list")

    progress = build_learning_course_progress(expand_learning_course_items(learning_course, request.user))
    return render(
        request,
        "courses/learning_course_detail.html",
        {
            "learning_course": learning_course,
            **progress,
            "need_final_threshold_notice": request.GET.get("need_final_threshold") == "1",
            **_base_user_context(request, active_menu="learning_courses"),
        },
    )


def _get_learning_course_entry_or_404(request, learning_course_id: int, position: int):
    learning_course = get_object_or_404(LearningCourse, pk=learning_course_id)
    if not _user_can_access_learning_course(request.user, learning_course):
        return None, None, None

    items = expand_learning_course_items(learning_course, request.user)
    entry = next((item for item in items if item.sequence_index == position), None)
    return learning_course, items, entry


@login_required
def learning_course_item_detail(request, learning_course_id: int, position: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:learning_list")

    learning_course, items, entry = _get_learning_course_entry_or_404(request, learning_course_id, position)
    if learning_course is None:
        return redirect("courses:learning_list")
    if entry is None:
        return redirect("courses:learning_detail", learning_course_id=learning_course_id)
    if not entry.is_available:
        if entry.kind == "final_test":
            return redirect(
                f"{reverse('courses:learning_detail', kwargs={'learning_course_id': learning_course_id})}?need_final_threshold=1"
            )
        previous_entry = next((item for item in items if item.sequence_index == position - 1), None)
        if previous_entry:
            return redirect(
                f"{reverse('courses:learning_item', kwargs={'learning_course_id': learning_course_id, 'position': previous_entry.sequence_index})}?need_complete=1"
            )
        return redirect("courses:learning_detail", learning_course_id=learning_course_id)

    next_entry = next((item for item in items if item.sequence_index == position + 1), None)

    if entry.kind in {"test", "final_test"}:
        return redirect("courses:learning_test", learning_course_id=learning_course_id, position=position)

    return render(
        request,
        "courses/learning_course_article_detail.html",
        {
            "learning_course": learning_course,
            "entry": entry,
            "next_entry": next_entry,
            "need_complete_notice": request.GET.get("need_complete") == "1",
            **_base_user_context(request, active_menu="learning_courses"),
        },
    )


@login_required
@require_POST
def mark_learning_course_item_viewed(request, learning_course_id: int, position: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return JsonResponse({"ok": False}, status=403)

    learning_course, _, entry = _get_learning_course_entry_or_404(request, learning_course_id, position)
    if learning_course is None or entry is None or not entry.is_available or not entry.lesson:
        return JsonResponse({"ok": False}, status=403)

    LessonView.objects.get_or_create(user=request.user, lesson=entry.lesson)
    return JsonResponse({"ok": True})


@login_required
def learning_course_test(request, learning_course_id: int, position: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:learning_list")

    learning_course, items, entry = _get_learning_course_entry_or_404(request, learning_course_id, position)
    if learning_course is None:
        return redirect("courses:learning_list")
    if entry is None or entry.kind not in {"test", "final_test"}:
        return redirect("courses:learning_detail", learning_course_id=learning_course_id)
    if not entry.is_available:
        return redirect("courses:learning_item", learning_course_id=learning_course_id, position=position)

    if entry.kind == "final_test":
        questions = learning_course.final_questions.all().order_by("id")
        if questions.count() == 0:
            return redirect("courses:learning_detail", learning_course_id=learning_course_id)
        current_round = _get_current_course_final_round(request.user, learning_course)
        failed_count, passed_exists = _course_final_round_stats(request.user, learning_course, current_round)
    else:
        lesson = entry.lesson
        questions = lesson.questions.all().order_by("id")
        if questions.count() == 0:
            return redirect("courses:learning_item", learning_course_id=learning_course_id, position=position)
        current_round = _get_current_round(request.user, lesson)
        failed_count, passed_exists = _round_stats(request.user, lesson, current_round)

    locked = (failed_count >= MAX_ATTEMPTS_PER_ROUND and not passed_exists)
    if locked:
        return redirect(
            f"{reverse('courses:learning_item', kwargs={'learning_course_id': learning_course_id, 'position': position})}?locked=1"
        )

    if request.method == "POST":
        total = questions.count()
        correct = 0
        if entry.kind == "final_test":
            attempt = LearningCourseFinalAttempt.objects.create(
                user=request.user,
                learning_course=learning_course,
                retake_round=current_round,
                score=0,
                correct=0,
                total=total,
                passed=False,
            )
        else:
            attempt = LessonAttempt.objects.create(
                user=request.user,
                lesson=lesson,
                retake_round=current_round,
                score=0,
                correct=0,
                total=total,
                passed=False,
            )

        for q in questions:
            chosen = request.POST.get(f"q_{q.id}")
            chosen_norm = chosen.upper().strip() if chosen else None
            is_correct = bool(chosen_norm) and chosen_norm == q.correct_answer
            if is_correct:
                correct += 1
            if entry.kind == "final_test":
                LearningCourseFinalAnswer.objects.create(
                    attempt=attempt,
                    question=q,
                    chosen_answer=chosen_norm,
                    is_correct=is_correct,
                )
            else:
                AttemptAnswer.objects.create(
                    attempt=attempt,
                    question=q,
                    chosen_answer=chosen_norm,
                    is_correct=is_correct,
                )

        score = int((correct / total) * 100) if total else 0
        passed = score >= PASS_THRESHOLD
        attempt.score = score
        attempt.correct = correct
        attempt.passed = passed
        attempt.save(update_fields=["score", "correct", "passed"])
        return redirect("courses:learning_result", learning_course_id=learning_course_id, position=position)

    next_entry = next((item for item in items if item.sequence_index == position + 1), None)
    attempts_left = max(0, MAX_ATTEMPTS_PER_ROUND - failed_count) if not passed_exists else 0

    return render(
        request,
        "courses/learning_course_test.html",
        {
            "learning_course": learning_course,
            "entry": entry,
            "questions": questions,
            "current_round": current_round,
            "attempts_left": attempts_left,
            "pass_threshold": PASS_THRESHOLD,
            "next_entry": next_entry,
            **_base_user_context(request, active_menu="learning_courses"),
        },
    )


@login_required
def learning_course_result(request, learning_course_id: int, position: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:learning_list")

    learning_course, items, entry = _get_learning_course_entry_or_404(request, learning_course_id, position)
    if learning_course is None:
        return redirect("courses:learning_list")
    if entry is None or entry.kind not in {"test", "final_test"}:
        return redirect("courses:learning_detail", learning_course_id=learning_course_id)
    if entry.kind == "final_test":
        attempt = (
            LearningCourseFinalAttempt.objects
            .filter(user=request.user, learning_course=learning_course)
            .order_by("-created_at")
            .first()
        )
    else:
        attempt = (
            LessonAttempt.objects
            .filter(user=request.user, lesson=entry.lesson)
            .order_by("-created_at")
            .first()
        )
    next_entry = next((item for item in items if item.sequence_index == position + 1), None)
    return render(
        request,
        "courses/learning_course_result.html",
        {
            "learning_course": learning_course,
            "entry": entry,
            "attempt": attempt,
            "pass_threshold": PASS_THRESHOLD,
            "next_entry": next_entry,
            **_base_user_context(request, active_menu="learning_courses"),
        },
    )


@login_required
def course_list(request):
    access_state, access_message = _courses_access_state(request.user)
    if access_state != "active":
        return render(
            request,
            "courses/course_list.html",
            {
                "courses": [],
                "course_access_restricted": True,
                "course_access_state": access_state,
                "course_access_message": access_message,
                **_base_user_context(request, active_menu="courses"),
            },
        )

    query = request.GET.get("q", "").strip()

    # Доступные курсы
    if request.user.is_superuser:
        courses_qs = Course.objects.all().order_by("title", "id")
    else:
        user_groups = request.user.groups.all()
        courses_qs = (
            Course.objects.filter(allowed_groups__in=user_groups)
            .distinct()
            .order_by("title", "id")
        )

    courses_with_status = []
    search_results = []

    if query:
        accessible_courses = list(courses_qs)
        unlocked_ids = _unlocked_lesson_ids_for_user(accessible_courses, request.user)
        search_vector = (
            SearchVector("title", weight="A", config="russian")
            + SearchVector("content", weight="B", config="russian")
        )
        search_query = SearchQuery(query, config="russian", search_type="websearch")

        search_qs = (
            Lesson.objects
            .filter(course__in=accessible_courses, id__in=unlocked_ids)
            .annotate(search=search_vector)
            .filter(search=search_query)
            .annotate(
                rank=SearchRank(search_vector, search_query),
                snippet=SearchHeadline(
                    "content",
                    search_query,
                    config="russian",
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=24,
                    min_words=12,
                    short_word=2,
                    highlight_all=False,
                    max_fragments=2,
                    fragment_delimiter=" ... ",
                ),
            )
            .select_related("course")
            .order_by("-rank", "course__title", "order", "id")
        )

        search_results = list(search_qs)

    for course in courses_qs:
        lessons = Lesson.objects.filter(course=course).order_by("order", "id")
        lessons = annotate_lessons_with_user_progress(lessons, request.user)
        lessons = list(lessons)
        progress = build_course_progress(lessons)

        if progress["course_completed"]:
            status = "completed"
        elif progress["progress_percent"] > 0:
            status = "in_progress"
        else:
            status = "not_started"

        courses_with_status.append({
            "course": course,
            "status": status,
        })

    return render(
        request,
        "courses/course_list.html",
        {
            "courses": courses_with_status,
            "search_query": query,
            "search_results": search_results,
            "course_access_restricted": False,
            **_base_user_context(request, active_menu="courses"),
        },
    )


@login_required
def course_detail(request, course_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:list")

    course = get_object_or_404(Course, pk=course_id)

    if not _user_can_access_course(request.user, course):
        return redirect("courses:list")

    lessons_qs = Lesson.objects.filter(course=course).order_by("order", "id")
    lessons_qs = annotate_lessons_with_user_progress(lessons_qs, request.user)
    lessons = list(lessons_qs)

    prev_viewed = True
    for lesson in lessons:
        is_unlocked = prev_viewed
        lesson.is_unlocked = is_unlocked

        lesson_completed = bool(getattr(lesson, "viewed", False)) and (
            not bool(getattr(lesson, "has_test", False)) or bool(getattr(lesson, "best_passed", False))
        )
        lesson.is_completed = lesson_completed

        if not is_unlocked:
            lesson.access_state = "locked"
        elif lesson_completed:
            lesson.access_state = "completed"
        else:
            lesson.access_state = "available"

        prev_viewed = bool(getattr(lesson, "viewed", False))

    progress = build_course_progress(lessons)

    return render(
        request,
        "courses/course_detail.html",
        {
            "course": course,
            **progress,
            "pass_threshold": PASS_THRESHOLD,
            **_base_user_context(request, active_menu="courses"),
        },
    )


@login_required
def lesson_detail(request, course_id: int, lesson_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:list")

    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        return redirect("courses:list")

    lesson = get_object_or_404(Lesson, pk=lesson_id, course=course)
    lessons = list(Lesson.objects.filter(course=course).order_by("order", "id"))
    idx = next((i for i, x in enumerate(lessons) if x.id == lesson.id), None)
    prev_lesson = lessons[idx - 1] if idx is not None and idx > 0 else None
    next_lesson = lessons[idx + 1] if idx is not None and idx + 1 < len(lessons) else None

    if prev_lesson:
        prev_viewed = LessonView.objects.filter(user=request.user, lesson=prev_lesson).exists()
        if not prev_viewed:
            return redirect(
                f"{reverse('courses:lesson', kwargs={'course_id': course.id, 'lesson_id': prev_lesson.id})}?need_view=1"
            )

    # текущий цикл пересдачи
    current_round = _get_current_round(request.user, lesson)
    failed_count, passed_exists = _round_stats(request.user, lesson, current_round)

    locked = (failed_count >= MAX_ATTEMPTS_PER_ROUND and not passed_exists)

    # если пришли с relearn=1 — это означает “после 3 провалов отправили перечитать урок”
    # и теперь нужно открыть новый цикл пересдачи (снова 3 попытки)
    if request.GET.get("relearn") == "1" and locked:
        current_round = current_round + 1

    # после возможного повышения round — пересчитываем статусы
    failed_count, passed_exists = _round_stats(request.user, lesson, current_round)
    locked = (failed_count >= MAX_ATTEMPTS_PER_ROUND and not passed_exists)
    attempts_left = 0 if passed_exists else max(0, MAX_ATTEMPTS_PER_ROUND - failed_count)

    last_attempt = (
        LessonAttempt.objects.filter(user=request.user, lesson=lesson)
        .order_by("-created_at")
        .first()
    )

    questions_count = lesson.questions.count()

    return render(
        request,
        "courses/lesson_detail.html",
        {
            "course": course,
            "lesson": lesson,
            "next_lesson": next_lesson,
            "questions_count": questions_count,
            "last_attempt": last_attempt,
            "pass_threshold": PASS_THRESHOLD,
            "current_round": current_round,
            "attempts_left": attempts_left,
            "locked": locked,
            "need_view_notice": request.GET.get("need_view") == "1",
            **_base_user_context(request, active_menu="courses"),
        },
    )


@login_required
@require_POST
def mark_lesson_viewed(request, course_id: int, lesson_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return JsonResponse({"ok": False}, status=403)

    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        return JsonResponse({"ok": False}, status=403)

    lesson = get_object_or_404(Lesson, pk=lesson_id, course=course)
    LessonView.objects.get_or_create(user=request.user, lesson=lesson)
    return JsonResponse({"ok": True})


@login_required
def lesson_test(request, course_id: int, lesson_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:list")

    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        return redirect("courses:list")

    lesson = get_object_or_404(Lesson, pk=lesson_id, course=course)
    questions = lesson.questions.all().order_by("id")

    if questions.count() == 0:
        return redirect("courses:lesson", course_id=course.id, lesson_id=lesson.id)

    current_round = _get_current_round(request.user, lesson)
    failed_count, passed_exists = _round_stats(request.user, lesson, current_round)

    locked = (failed_count >= MAX_ATTEMPTS_PER_ROUND and not passed_exists)
    if locked:
        # запрещаем вход в тест, пока не “перечитал урок” (relearn=1)
        return redirect(
            f"{reverse('courses:lesson', kwargs={'course_id': course.id, 'lesson_id': lesson.id})}?locked=1"
        )

    if request.method == "POST":
        total = questions.count()
        correct = 0

        # создаём попытку сразу, чтобы иметь attempt_id для answers
        # score посчитаем после
        attempt = LessonAttempt.objects.create(
            user=request.user,
            lesson=lesson,
            retake_round=current_round,
            score=0,
            correct=0,
            total=total,
            passed=False,
        )

        for q in questions:
            chosen = request.POST.get(f"q_{q.id}")  # ожидаем "A"/"B"/"C"/"D"
            chosen_norm = chosen.upper().strip() if chosen else None

            is_correct = bool(chosen_norm) and chosen_norm == q.correct_answer
            if is_correct:
                correct += 1

            AttemptAnswer.objects.create(
                attempt=attempt,
                question=q,
                chosen_answer=chosen_norm,
                is_correct=is_correct,
            )

        score = int((correct / total) * 100) if total else 0
        passed = score >= PASS_THRESHOLD

        attempt.score = score
        attempt.correct = correct
        attempt.passed = passed
        attempt.save(update_fields=["score", "correct", "passed"])

        # если провалили, проверяем: это был 3-й провал в текущем цикле?
        if not passed:
            failed_count_after = LessonAttempt.objects.filter(
                user=request.user, lesson=lesson, retake_round=current_round, passed=False
            ).count()

            if failed_count_after >= MAX_ATTEMPTS_PER_ROUND:
                # отправляем на урок на повторное изучение и поднимаем round при входе на страницу
                return redirect(
                    f"{reverse('courses:lesson', kwargs={'course_id': course.id, 'lesson_id': lesson.id})}?relearn=1"
                )

        return redirect("courses:lesson_result", course_id=course.id, lesson_id=lesson.id)

    attempts_left = max(0, MAX_ATTEMPTS_PER_ROUND - failed_count) if not passed_exists else 0

    return render(
        request,
        "courses/lesson_test.html",
        {
            "course": course,
            "lesson": lesson,
            "questions": questions,
            "pass_threshold": PASS_THRESHOLD,
            "current_round": current_round,
            "attempts_left": attempts_left,
            **_base_user_context(request, active_menu="courses"),
        },
    )


@login_required
def lesson_result(request, course_id: int, lesson_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:list")

    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        return redirect("courses:list")

    lesson = get_object_or_404(Lesson, pk=lesson_id, course=course)

    attempt = (
        LessonAttempt.objects.filter(user=request.user, lesson=lesson)
        .order_by("-created_at")
        .first()
    )

    # для отображения остатков попыток — в рамках round последней попытки
    current_round = attempt.retake_round if attempt else _get_current_round(request.user, lesson)
    failed_count, passed_exists = _round_stats(request.user, lesson, current_round)
    attempts_left = 0 if passed_exists else max(0, MAX_ATTEMPTS_PER_ROUND - failed_count)

    return render(
        request,
        "courses/lesson_result.html",
        {
            "course": course,
            "lesson": lesson,
            "attempt": attempt,
            "pass_threshold": PASS_THRESHOLD,
            "current_round": current_round,
            "attempts_left": attempts_left,
            **_base_user_context(request, active_menu="courses"),
        },
    )


@login_required
def profile(request):
    group = None
    group_profile = None
    user_access, _ = UserAccess.objects.get_or_create(user=request.user)
    access_state, access_message = _courses_access_state(request.user)
    if not request.user.is_superuser:
        group = request.user.groups.filter(profile__isnull=False, profile__is_admin_group=False).order_by("name").first()
        if group:
            group_profile = getattr(group, "profile", None)

    return render(
        request,
        "courses/profile.html",
        {
            "group": group,
            "group_profile": group_profile,
            "user_access": user_access,
            "access_state": access_state,
            "access_message": access_message,
            **_base_user_context(request, active_menu="profile"),
        },
    )


@login_required
@require_POST
def repeat_payment(request):
    group = request.user.groups.filter(profile__isnull=False, profile__is_admin_group=False).order_by("name").first()
    if not group:
        messages.error(request, "Для вашей учетной записи не найден тариф для повторной оплаты.")
        return redirect("courses:profile")

    user_access, _ = UserAccess.objects.get_or_create(user=request.user)
    access_state, _ = _courses_access_state(request.user)
    if access_state == "active":
        messages.info(request, "Ваш тариф уже оплачен.")
        return redirect("courses:profile")

    try:
        payment_order = register_payment_order(request, request.user, group)
    except PaymentGatewayError as exc:
        messages.error(request, str(exc))
        return redirect("courses:profile")

    return redirect(payment_order.gateway_form_url)


@login_required
def kb_list(request):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:profile")

    allowed_course_ids = _user_allowed_kb_course_ids(request.user)
    allowed_lesson_ids = _user_allowed_kb_lesson_ids(request.user)
    allowed_section_ids = set() if request.user.is_superuser else set(
        GroupProfile.objects
        .filter(group__in=request.user.groups.all())
        .values_list("allowed_kb_sections__id", flat=True)
    )

    sections = (
        KnowledgeBaseSection.objects
        .prefetch_related(
            Prefetch(
                "modules",
                queryset=Course.objects.order_by("title", "id").prefetch_related(
                    Prefetch("lessons", queryset=Lesson.objects.order_by("order", "id"))
                ),
            )
        )
        .order_by("order", "title", "id")
    )

    hierarchy = []
    for section in sections:
        section_modules = []

        for course in section.modules.all():
            if request.user.is_superuser or course.id in allowed_course_ids:
                articles = list(course.lessons.all())
            else:
                articles = [article for article in course.lessons.all() if article.id in allowed_lesson_ids]

            if not articles and section.id not in allowed_section_ids and course.id not in allowed_course_ids and not request.user.is_superuser:
                continue

            section_modules.append({
                "module": course,
                "articles": articles,
            })

        if section_modules or (section.id in allowed_section_ids):
            hierarchy.append({
                "section": section,
                "modules": section_modules,
            })

    return render(
        request,
        "courses/kb_list.html",
        {
            "hierarchy": hierarchy,
            **_base_user_context(request, active_menu="kb"),
        },
    )


@login_required
def kb_lesson_detail(request, course_id: int, lesson_id: int):
    access_state, _ = _courses_access_state(request.user)
    if access_state != "active":
        return redirect("courses:profile")

    if not _user_has_kb_access(request.user):
        return redirect("courses:profile")

    allowed_lesson_ids = _user_allowed_kb_lesson_ids(request.user)
    lesson = get_object_or_404(
        Lesson.objects.select_related("course", "course__section"),
        pk=lesson_id,
        course_id=course_id,
    )

    if not request.user.is_superuser and lesson.id not in allowed_lesson_ids:
        return redirect("courses:profile")

    accessible_lessons = (
        Lesson.objects.select_related("course", "course__section")
        .order_by("course__section__order", "course__section__title", "course__title", "order", "id")
    )
    if not request.user.is_superuser:
        accessible_lessons = accessible_lessons.filter(id__in=allowed_lesson_ids)

    lesson_ids = list(accessible_lessons.values_list("id", flat=True))
    previous_lesson = None
    next_lesson = None
    try:
        current_index = lesson_ids.index(lesson.id)
        if current_index > 0:
            previous_lesson = accessible_lessons.get(id=lesson_ids[current_index - 1])
        if current_index < len(lesson_ids) - 1:
            next_lesson = accessible_lessons.get(id=lesson_ids[current_index + 1])
    except ValueError:
        previous_lesson = None
        next_lesson = None

    return render(
        request,
        "courses/kb_lesson_detail.html",
        {
            "course": lesson.course,
            "section": lesson.course.section,
            "lesson": lesson,
            "previous_lesson": previous_lesson,
            "next_lesson": next_lesson,
            **_base_user_context(request, active_menu="kb"),
        },
    )
