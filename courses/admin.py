from django import forms
from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin, GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError
import re
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.contrib import messages
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.db.models import Count, Q
from django.utils.html import format_html
from django.utils import timezone
from zoneinfo import ZoneInfo

from .models import (
    Course, Lesson, Question,
    LessonAttempt,
    AttemptAnswer,
    LearningCourse,
    LearningCourseItem,
    LearningCourseFinalQuestion,
    KnowledgeBaseSection,
    GroupProfile,
    PaymentOrder,
    normalize_answer_codes,
)
from .progress import annotate_lessons_with_user_progress, build_course_progress

PASS_THRESHOLD = 85


def _is_admins_or_superuser(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(profile__is_admin_group=True).exists()


def _tariff_groups_queryset():
    return Group.objects.filter(profile__isnull=False, profile__is_admin_group=False).order_by("name")


def _get_user_tariff_group(user):
    return (
        user.groups.filter(profile__isnull=False)
        .filter(profile__is_admin_group=False)
        .order_by("name")
        .first()
    )


# -----------------------------
# Question admin: Course -> Lesson chained dropdowns
# -----------------------------
class QuestionAdminForm(forms.ModelForm):
    is_correct_a = forms.BooleanField(required=False, label="Верный")
    is_correct_b = forms.BooleanField(required=False, label="Верный")
    is_correct_c = forms.BooleanField(required=False, label="Верный")
    is_correct_d = forms.BooleanField(required=False, label="Верный")

    course = forms.ModelChoiceField(
        queryset=Course.objects.all().order_by("title", "id"),
        required=False,
        label="Модуль",
        help_text="Сначала выберите модуль, чтобы отфильтровать статьи.",
    )

    class Meta:
        model = Question
        fields = ["course", "lesson", "question_text", "option_a", "option_b", "option_c", "option_d"]
        labels = {
            "lesson": "Статья",
            "question_text": "Тест",
            "option_a": "Вариант A",
            "option_b": "Вариант B",
            "option_c": "Вариант C",
            "option_d": "Вариант D",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["lesson"].queryset = Lesson.objects.none()

        # edit existing
        if self.instance and self.instance.pk and self.instance.lesson_id:
            course = self.instance.lesson.course
            self.fields["course"].initial = course
            self.fields["lesson"].queryset = Lesson.objects.filter(course=course).order_by("order", "id")
            return

        # add/change with selected course
        course_id = self.data.get("course") if "course" in self.data else None
        if course_id:
            try:
                course_id_int = int(course_id)
                self.fields["lesson"].queryset = Lesson.objects.filter(course_id=course_id_int).order_by("order", "id")
            except (TypeError, ValueError):
                self.fields["lesson"].queryset = Lesson.objects.none()

        selected = set(normalize_answer_codes(getattr(self.instance, "correct_answer", "")).split(",")) if getattr(self.instance, "correct_answer", "") else set()
        self.fields["is_correct_a"].initial = "A" in selected
        self.fields["is_correct_b"].initial = "B" in selected
        self.fields["is_correct_c"].initial = "C" in selected
        self.fields["is_correct_d"].initial = "D" in selected

    def clean(self):
        cleaned_data = super().clean()
        selected = []
        for code in ("A", "B", "C", "D"):
            if cleaned_data.get(f"is_correct_{code.lower()}"):
                selected.append(code)
        if not selected:
            raise ValidationError("Нужно выбрать хотя бы один правильный ответ.")
        if "D" in selected and not (cleaned_data.get("option_d") or "").strip():
            self.add_error("option_d", "Нельзя отметить вариант D правильным, если текст варианта D пустой.")
        cleaned_data["correct_answer"] = normalize_answer_codes(selected)
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.correct_answer = self.cleaned_data["correct_answer"]
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class QuestionInlineForm(forms.ModelForm):
    is_correct_a = forms.BooleanField(required=False, label="Верный")
    is_correct_b = forms.BooleanField(required=False, label="Верный")
    is_correct_c = forms.BooleanField(required=False, label="Верный")
    is_correct_d = forms.BooleanField(required=False, label="Верный")

    class Meta:
        model = Question
        fields = ["question_text", "option_a", "option_b", "option_c", "option_d"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected = set(normalize_answer_codes(getattr(self.instance, "correct_answer", "")).split(",")) if getattr(self.instance, "correct_answer", "") else set()
        self.fields["is_correct_a"].initial = "A" in selected
        self.fields["is_correct_b"].initial = "B" in selected
        self.fields["is_correct_c"].initial = "C" in selected
        self.fields["is_correct_d"].initial = "D" in selected

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        selected = []
        for code in ("A", "B", "C", "D"):
            if cleaned_data.get(f"is_correct_{code.lower()}"):
                selected.append(code)
        has_content = any((cleaned_data.get("question_text"), cleaned_data.get("option_a"), cleaned_data.get("option_b"), cleaned_data.get("option_c"), cleaned_data.get("option_d")))
        if has_content and not selected:
            raise ValidationError("Нужно выбрать хотя бы один правильный ответ.")
        if "D" in selected and not (cleaned_data.get("option_d") or "").strip():
            self.add_error("option_d", "Нельзя отметить вариант D правильным, если текст варианта D пустой.")
        cleaned_data["correct_answer"] = normalize_answer_codes(selected)
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.correct_answer = self.cleaned_data.get("correct_answer", "")
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class LearningCourseFinalQuestionInlineForm(forms.ModelForm):
    is_correct_a = forms.BooleanField(required=False, label="Верный")
    is_correct_b = forms.BooleanField(required=False, label="Верный")
    is_correct_c = forms.BooleanField(required=False, label="Верный")
    is_correct_d = forms.BooleanField(required=False, label="Верный")

    class Meta:
        model = LearningCourseFinalQuestion
        fields = ["question_text", "option_a", "option_b", "option_c", "option_d"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        selected = set(normalize_answer_codes(getattr(self.instance, "correct_answer", "")).split(",")) if getattr(self.instance, "correct_answer", "") else set()
        self.fields["is_correct_a"].initial = "A" in selected
        self.fields["is_correct_b"].initial = "B" in selected
        self.fields["is_correct_c"].initial = "C" in selected
        self.fields["is_correct_d"].initial = "D" in selected

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        selected = []
        for code in ("A", "B", "C", "D"):
            if cleaned_data.get(f"is_correct_{code.lower()}"):
                selected.append(code)
        has_content = any((cleaned_data.get("question_text"), cleaned_data.get("option_a"), cleaned_data.get("option_b"), cleaned_data.get("option_c"), cleaned_data.get("option_d")))
        if has_content and not selected:
            raise ValidationError("Нужно выбрать хотя бы один правильный ответ.")
        if "D" in selected and not (cleaned_data.get("option_d") or "").strip():
            self.add_error("option_d", "Нельзя отметить вариант D правильным, если текст варианта D пустой.")
        cleaned_data["correct_answer"] = normalize_answer_codes(selected)
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.correct_answer = self.cleaned_data.get("correct_answer", "")
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class KnowledgeBaseSectionAdminForm(forms.ModelForm):
    class Meta:
        model = KnowledgeBaseSection
        fields = ["title", "slug", "description", "order"]
        labels = {
            "title": "Раздел",
            "slug": "Слаг",
            "description": "Описание",
            "order": "Порядок",
        }


class GroupProfileAdminForm(forms.ModelForm):
    class Meta:
        model = GroupProfile
        fields = [
            "public_name",
            "is_admin_group",
            "access_duration_days",
            "created_by",
            "payment_amount",
            "payment_currency",
            "payment_user_name",
            "payment_password",
            "payment_description",
            "payment_language",
            "allowed_kb_lessons",
            "allowed_kb_courses",
            "allowed_kb_sections",
        ]
        labels = {
            "public_name": "Название для пользователя",
            "is_admin_group": "Группа администраторов",
            "access_duration_days": "Срок доступа, дней",
            "created_by": "Создал",
            "payment_amount": "amount",
            "payment_currency": "currency",
            "payment_user_name": "userName",
            "payment_password": "password",
            "payment_description": "description",
            "payment_language": "language",
            "allowed_kb_lessons": "Статьи базы знаний",
            "allowed_kb_courses": "Модули базы знаний",
            "allowed_kb_sections": "Разделы базы знаний",
        }
        help_texts = {
            "payment_amount": "Сумма к оплате в минимальных единицах валюты, например 2000 = 20.00.",
            "payment_currency": "Числовой код валюты, например 933.",
            "payment_user_name": "Логин для вызова register.do.",
            "payment_password": "Пароль для вызова register.do.",
            "payment_description": "Описание заказа, которое отправляется в платежный шлюз.",
            "payment_language": "Язык платежной формы, например ru или en.",
            "allowed_kb_lessons": "Доступ только к выбранным отдельным статьям базы знаний.",
            "allowed_kb_courses": "Доступ ко всем статьям выбранных модулей в базе знаний.",
            "allowed_kb_sections": "Доступ ко всем модулям и статьям выбранных разделов базы знаний.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_password"].widget = forms.PasswordInput(render_value=True)
        self.fields["allowed_kb_lessons"].queryset = Lesson.objects.select_related("course", "course__section").order_by(
            "course__section__order", "course__title", "order", "id"
        )
        self.fields["allowed_kb_courses"].queryset = Course.objects.order_by("title", "id")
        self.fields["allowed_kb_sections"].queryset = KnowledgeBaseSection.objects.order_by("order", "title", "id")


class LessonAdminForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = "__all__"
        labels = {
            "course": "Модуль",
            "title": "Название",
            "content": "Содержимое статьи",
            "order": "Порядок",
            "is_final": "Финальная статья",
        }
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "rows": 30,
                    "style": "min-height: 220px;",
                    "placeholder": (
                        "Поддерживается Markdown: # Заголовок, **жирный**, *курсив*, "
                        "списки, таблицы, изображения ![alt](url), ссылки [текст](url). "
                        "Ссылка на статью: [Текст](article:ID_статьи)."
                    ),
                }
            ),
        }
        help_texts = {
            "content": "",
            "is_final": "Отметьте, если это финальная статья.",
        }

    def clean_content(self):
        content = (self.cleaned_data.get("content") or "").strip()
        if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", content):
            raise ValidationError("Используйте Markdown-разметку. Сырый HTML запрещен.")
        return content


class LearningCourseItemInlineForm(forms.ModelForm):
    class Meta:
        model = LearningCourseItem
        fields = "__all__"
        labels = {
            "order_index": "Порядок",
            "item_type": "Тип элемента",
            "section": "Раздел",
            "module": "Модуль",
            "lesson": "Статья / тест",
            "is_final_test": "Итоговый тест курса",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["section"].queryset = KnowledgeBaseSection.objects.order_by("order", "title", "id")
        self.fields["module"].queryset = Course.objects.select_related("section").order_by("section__order", "title", "id")
        self.fields["lesson"].queryset = (
            Lesson.objects
            .select_related("course", "course__section")
            .order_by("course__section__order", "course__title", "order", "id")
        )
        self.fields["section"].label_from_instance = self._section_label
        self.fields["module"].label_from_instance = self._module_label
        self.fields["lesson"].label_from_instance = self._lesson_label
        for field_name in ("section", "module", "lesson"):
            self._disable_related_widget_actions(self.fields[field_name])

        if self.instance and self.instance.pk:
            if self.instance.item_type == LearningCourseItem.ITEM_SECTION and self.instance.section_id:
                self.fields["section"].initial = self.instance.section_id
            elif self.instance.item_type == LearningCourseItem.ITEM_MODULE and self.instance.module_id:
                self.fields["section"].initial = getattr(self.instance.module, "section_id", None)
                self.fields["module"].initial = self.instance.module_id
            elif self.instance.lesson_id:
                self.fields["section"].initial = getattr(getattr(self.instance.lesson, "course", None), "section_id", None)
                self.fields["module"].initial = getattr(self.instance.lesson, "course_id", None)
                self.fields["lesson"].initial = self.instance.lesson_id

    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get("item_type")
        section = cleaned_data.get("section")
        module = cleaned_data.get("module")
        lesson = cleaned_data.get("lesson")

        if item_type == LearningCourseItem.ITEM_SECTION:
            cleaned_data["module"] = None
            cleaned_data["lesson"] = None
        elif item_type == LearningCourseItem.ITEM_MODULE:
            cleaned_data["lesson"] = None
            if module is not None:
                cleaned_data["section"] = module.section
        elif item_type in {LearningCourseItem.ITEM_ARTICLE, LearningCourseItem.ITEM_TEST}:
            if lesson is not None:
                cleaned_data["module"] = lesson.course
                cleaned_data["section"] = lesson.course.section

        return cleaned_data

    @staticmethod
    def _disable_related_widget_actions(formfield):
        widget = formfield.widget
        for attr in ("can_add_related", "can_change_related", "can_delete_related", "can_view_related"):
            if hasattr(widget, attr):
                setattr(widget, attr, False)

    @staticmethod
    def _section_label(section: KnowledgeBaseSection):
        return section.title

    @staticmethod
    def _module_label(module: Course):
        if getattr(module, "section", None):
            return f"{module.section.title} / {module.title}"
        return module.title

    @staticmethod
    def _lesson_label(lesson: Lesson):
        section = getattr(getattr(lesson, "course", None), "section", None)
        if section:
            return f"{section.title} / {lesson.course.title} / {lesson.title}"
        return f"{lesson.course.title} / {lesson.title}"


class LearningCourseAdminForm(forms.ModelForm):
    class Meta:
        model = LearningCourse
        fields = "__all__"
        labels = {
            "title": "Название",
            "description": "Описание",
            "created_by": "Создал",
            "allowed_groups": "Доступные группы",
        }


class CourseAdminForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = "__all__"
        labels = {
            "section": "Раздел",
            "title": "Название",
            "description": "Описание",
            "created_by": "Создал",
            "allowed_groups": "Доступные группы",
        }


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    form = QuestionAdminForm
    list_display = ["question_text", "lesson", "correct_answer"]
    search_fields = ["question_text", "lesson__title", "lesson__course__title"]
    fields = [
        "course",
        "lesson",
        "question_text",
        ("option_a", "is_correct_a"),
        ("option_b", "is_correct_b"),
        ("option_c", "is_correct_c"),
        ("option_d", "is_correct_d"),
    ]

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("lesson", "lesson__course", "lesson__course__section")
        lesson_id = request.GET.get("lesson_id", "").strip()
        if lesson_id.isdigit():
            qs = qs.filter(lesson_id=int(lesson_id))
        return qs

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        selected_lesson_id = request.GET.get("lesson_id", "").strip()
        extra_context.update(
            {
                "lessons_for_filter": (
                    Lesson.objects.select_related("course", "course__section")
                    .order_by("course__section__order", "course__title", "order", "id")
                ),
                "selected_lesson_id": selected_lesson_id,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    class Media:
        js = ("courses/admin/question_course_lesson.js",)

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    form = CourseAdminForm
    list_display = ["title", "section", "created_by", "created_at"]
    list_filter = ["section", "created_at"]
    search_fields = ["title", "description", "section__title"]

    class Media:
        js = ("courses/admin/course_allowed_groups.js",)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if request.user.is_authenticated:
            initial.setdefault("created_by", request.user.pk)
        return initial

    @staticmethod
    def _disable_related_widget_actions(formfield):
        widget = formfield.widget
        for attr in ("can_add_related", "can_change_related", "can_delete_related", "can_view_related"):
            if hasattr(widget, attr):
                setattr(widget, attr, False)
        return formfield

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name in {"created_by"}:
            self._disable_related_widget_actions(formfield)
        if db_field.name == "section":
            formfield.queryset = KnowledgeBaseSection.objects.order_by("order", "title", "id")
        return formfield

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if db_field.name == "allowed_groups":
            formfield.queryset = _tariff_groups_queryset()
        return formfield

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)

    def changelist_view(self, request, extra_context=None):
        return redirect("admin:courses_table")

    def response_add(self, request, obj, post_url_continue=None):
        if "_continue" in request.POST or "_addanother" in request.POST:
            return super().response_add(request, obj, post_url_continue=post_url_continue)
        return redirect("admin:courses_table")

    def response_change(self, request, obj):
        if "_continue" in request.POST or "_addanother" in request.POST:
            return super().response_change(request, obj)
        return redirect("admin:courses_table")

    def response_delete(self, request, obj_display, obj_id):
        return redirect("admin:courses_table")


class LearningCourseItemInline(admin.TabularInline):
    model = LearningCourseItem
    form = LearningCourseItemInlineForm
    extra = 1
    fields = ("order_index", "item_type", "section", "module", "lesson")
    ordering = ("order_index", "id")
    template = "admin/edit_inline/learning_course_tabular.html"


class LearningCourseFinalQuestionInline(admin.StackedInline):
    model = LearningCourseFinalQuestion
    form = LearningCourseFinalQuestionInlineForm
    extra = 1
    template = "admin/edit_inline/question_stacked.html"
    verbose_name = "Вопрос итогового теста"
    verbose_name_plural = "Вопросы итогового теста"
    fields = ("question_text", ("option_a", "is_correct_a"), ("option_b", "is_correct_b"), ("option_c", "is_correct_c"), ("option_d", "is_correct_d"))


@admin.register(LearningCourse)
class LearningCourseAdmin(admin.ModelAdmin):
    form = LearningCourseAdminForm
    change_list_template = "admin/courses/learningcourse/change_list.html"
    list_display = ["title", "created_by", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["title", "description"]
    inlines = [LearningCourseItemInline, LearningCourseFinalQuestionInline]

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if request.user.is_authenticated:
            initial.setdefault("created_by", request.user.pk)
        return initial

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if db_field.name == "allowed_groups":
            formfield.queryset = _tariff_groups_queryset()
        return formfield

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)

    class Media:
        js = ("courses/admin/learning_course_items.js",)


class QuestionInline(admin.StackedInline):
    model = Question
    form = QuestionInlineForm
    extra = 1
    template = "admin/edit_inline/question_stacked.html"
    verbose_name = "Вопрос теста"
    verbose_name_plural = "Вопросы теста"
    fields = ("question_text", ("option_a", "is_correct_a"), ("option_b", "is_correct_b"), ("option_c", "is_correct_c"), ("option_d", "is_correct_d"))


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    form = LessonAdminForm
    list_display = ["title", "course", "section_name", "order", "is_final"]
    list_filter = ["course", "course__section", "is_final"]
    search_fields = ["title", "course__title", "course__section__title"]
    ordering = ["course__section", "course", "order", "id"]
    inlines = [QuestionInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("course", "course__section")
        section_id = request.GET.get("section_id", "").strip()
        module_id = request.GET.get("module_id", "").strip()
        if section_id.isdigit():
            qs = qs.filter(course__section_id=int(section_id))
        if module_id.isdigit():
            qs = qs.filter(course_id=int(module_id))
        return qs

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        selected_section_id = request.GET.get("section_id", "").strip()
        selected_module_id = request.GET.get("module_id", "").strip()

        sections = KnowledgeBaseSection.objects.order_by("order", "title", "id")
        modules = Course.objects.select_related("section").order_by("title", "id")
        if selected_section_id.isdigit():
            modules = modules.filter(section_id=int(selected_section_id))

        extra_context.update(
            {
                "sections_for_filter": sections,
                "modules_for_filter": modules,
                "selected_section_id": selected_section_id,
                "selected_module_id": selected_module_id,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description="Раздел", ordering="course__section__title")
    def section_name(self, obj):
        return getattr(getattr(obj, "course", None), "section", None) or "—"

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


# -----------------------------
# Attempts admin (read-only history inside admin, optional)
# -----------------------------
class AttemptAnswerInline(admin.TabularInline):
    model = AttemptAnswer
    verbose_name_plural = "Результат"
    extra = 0
    can_delete = False
    readonly_fields = ("question_ru", "chosen_answer_ru", "is_correct_ru")
    fields = ("question_ru", "chosen_answer_ru", "is_correct_ru")

    @admin.display(description="Вопрос")
    def question_ru(self, obj):
        return obj.question

    @admin.display(description="Выбранный ответ")
    def chosen_answer_ru(self, obj):
        return obj.chosen_answer

    @admin.display(description="Верно", boolean=True)
    def is_correct_ru(self, obj):
        return obj.is_correct


@admin.register(LessonAttempt)
class LessonAttemptAdmin(admin.ModelAdmin):
    change_form_template = "admin/courses/lessonattempt/change_form.html"
    list_display = ("user", "lesson", "retake_round", "score", "passed", "created_at_gmt3")
    list_filter = ("passed", "retake_round", "lesson__course")
    search_fields = ("user__username", "lesson__title", "lesson__course__title")
    ordering = ("-created_at",)
    inlines = [AttemptAnswerInline]
    readonly_fields = (
        "user_ru",
        "lesson_ru",
        "retake_round_ru",
        "score_ru",
        "correct_ru",
        "total_ru",
        "passed_ru",
    )
    fields = readonly_fields
    _gmt3_tz = ZoneInfo("Europe/Moscow")

    @admin.display(description="Пользователь")
    def user_ru(self, obj):
        if not obj or not obj.user_id:
            return "—"
        url = reverse("admin:auth_user_change", args=[obj.user_id])
        return format_html('<a href="{}">{}</a>', url, obj.user.username)

    @admin.display(description="Статья")
    def lesson_ru(self, obj):
        if not obj or not obj.lesson_id:
            return "—"
        url = reverse("admin:courses_lesson_change", args=[obj.lesson_id])
        return format_html('<a href="{}">{}</a>', url, obj.lesson)

    @admin.display(description="Раунд сдачи")
    def retake_round_ru(self, obj):
        return obj.retake_round

    @admin.display(description="Результат, %")
    def score_ru(self, obj):
        return obj.score

    @admin.display(description="Верных ответов")
    def correct_ru(self, obj):
        return obj.correct

    @admin.display(description="Всего вопросов")
    def total_ru(self, obj):
        return obj.total

    @admin.display(description="Пройден", boolean=True)
    def passed_ru(self, obj):
        return obj.passed

    @admin.display(description="Дата и время", ordering="created_at")
    def created_at_gmt3(self, obj):
        dt = timezone.localtime(obj.created_at, self._gmt3_tz)
        return dt.strftime("%H:%M:%S %d.%m.%Y GMT+3")

    def has_view_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


@admin.register(KnowledgeBaseSection)
class KnowledgeBaseSectionAdmin(admin.ModelAdmin):
    form = KnowledgeBaseSectionAdminForm
    change_list_template = "admin/courses/knowledgebasesection/change_list.html"
    list_display = ("title", "slug", "modules_count", "order", "created_at")
    search_fields = ("title", "slug")
    ordering = ("order", "title", "id")

    @admin.display(description="Модули")
    def modules_count(self, obj):
        return obj.modules.count()

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


@admin.register(PaymentOrder)
class PaymentOrderAdmin(admin.ModelAdmin):
    change_list_template = "admin/courses/paymentorder/change_list.html"
    change_form_template = "admin/courses/paymentorder/change_form.html"
    list_display = (
        "order_number",
        "created_at",
        "user",
        "tariff_group",
        "amount",
        "currency",
        "status",
        "gateway_order_id",
    )
    list_filter = ("status", "currency", "tariff_group")
    search_fields = ("user__username", "tariff_group__name", "gateway_order_id", "public_id")
    readonly_fields = (
        "public_id",
        "order_number",
        "user",
        "tariff_group",
        "amount",
        "currency",
        "description",
        "status",
        "register_url",
        "return_url",
        "gateway_order_id",
        "gateway_form_url",
        "request_payload",
        "response_payload",
        "error_message",
        "created_at",
        "paid_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


# -----------------------------
# Admin custom views: Progress + AJAX lessons by course + History
# -----------------------------
def admin_lessons_by_course(request, course_id: int):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponseForbidden("Forbidden")

    lessons = (
        Lesson.objects.filter(course_id=course_id)
        .order_by("order", "id")
        .values("id", "title", "order")
    )
    data = [{"id": l["id"], "title": l["title"], "order": l["order"]} for l in lessons]
    return JsonResponse({"results": data})


def admin_modules_by_section(request, section_id: int):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponseForbidden("Forbidden")

    modules = (
        Course.objects.filter(section_id=section_id)
        .order_by("title", "id")
        .values("id", "title")
    )
    data = [{"id": m["id"], "title": m["title"]} for m in modules]
    return JsonResponse({"results": data})


def admin_courses_progress(request):
    if not _is_admins_or_superuser(request.user):
        return HttpResponseForbidden("Forbidden")

    user_id = request.GET.get("user_id", "").strip()
    tariff_code = request.GET.get("tariff_code", "").strip()
    course_id = request.GET.get("course_id", "").strip()
    final_percent_min = request.GET.get("final_percent_min", "").strip()
    completed = request.GET.get("completed", "").strip()

    users = (
        User.objects
        .exclude(is_superuser=True)
        .exclude(groups__profile__is_admin_group=True)
        .prefetch_related("groups", "groups__profile")
        .order_by("username", "id")
        .distinct()
    )
    if user_id.isdigit():
        users = users.filter(id=int(user_id))
    if tariff_code:
        users = users.filter(groups__name=tariff_code, groups__profile__isnull=False).distinct()

    courses = Course.objects.all().prefetch_related("allowed_groups").order_by("title", "id")
    if course_id.isdigit():
        courses = courses.filter(id=int(course_id))

    rows = []

    def to_int(value: str):
        return int(value) if value.isdigit() else None

    final_percent_min_i = to_int(final_percent_min)

    for u in users:
        user_group_ids = set(u.groups.values_list("id", flat=True))
        tariff = _get_user_tariff_group(u)
        if tariff and hasattr(tariff, "profile"):
            groups_str = f"{tariff.profile.public_name} ({tariff.name})"
        else:
            groups_str = "—"

        for c in courses:
            allowed_ids = set(c.allowed_groups.values_list("id", flat=True))
            if not allowed_ids.intersection(user_group_ids):
                continue
            lessons_qs = Lesson.objects.filter(course=c).order_by("order", "id")
            lessons = annotate_lessons_with_user_progress(lessons_qs, u)
            progress = build_course_progress(lessons)

            rows.append({
                "user": u,
                "groups_str": groups_str,
                "course": c,
                "total_lessons": progress["total_lessons"],
                "passed_lessons": progress["viewed_count"],
                "passed_percent": progress["viewed_percent"],
                "final_test_percent": progress["final_test_percent"],
                "course_completed": progress["course_completed"],
                "history_url": reverse("admin:course_user_history", kwargs={"user_id": u.id, "course_id": c.id}),
            })

    def include_row(row):
        if final_percent_min_i is not None and row["final_test_percent"] < final_percent_min_i:
            return False
        if completed == "yes" and not row["course_completed"]:
            return False
        if completed == "no" and row["course_completed"]:
            return False
        return True

    rows = [row for row in rows if include_row(row)]

    users_for_filter = User.objects.all().order_by("username", "id")
    courses_for_filter = Course.objects.all().order_by("title", "id")
    groups_for_filter = _tariff_groups_queryset()

    context = {
        **admin.site.each_context(request),
        "title": "Прогресс по модулям",
        "rows": rows,
        "pass_threshold": PASS_THRESHOLD,
        "users_for_filter": users_for_filter,
        "courses_for_filter": courses_for_filter,
        "groups_for_filter": groups_for_filter,
        "filters": {
            "user_id": user_id,
            "tariff_code": tariff_code,
            "course_id": course_id,
            "final_percent_min": final_percent_min,
            "completed": completed,
        },
    }
    return TemplateResponse(request, "admin/courses_progress.html", context)


def admin_users_table(request):
    if not _is_admins_or_superuser(request.user):
        return HttpResponseForbidden("Forbidden")

    username = request.GET.get("username", "").strip()
    email = request.GET.get("email", "").strip()
    tariff_code = request.GET.get("tariff_code", "").strip()
    is_staff = request.GET.get("is_staff", "").strip()
    is_superuser = request.GET.get("is_superuser", "").strip()
    is_active = request.GET.get("is_active", "").strip()

    users = User.objects.all().prefetch_related("groups", "groups__profile").order_by("username", "id")
    if username:
        users = users.filter(username__icontains=username)
    if email:
        users = users.filter(email__icontains=email)
    if tariff_code:
        users = users.filter(groups__name=tariff_code, groups__profile__isnull=False).distinct()
    if is_staff in {"yes", "no"}:
        users = users.filter(is_staff=(is_staff == "yes"))
    if is_superuser in {"yes", "no"}:
        users = users.filter(is_superuser=(is_superuser == "yes"))
    if is_active in {"yes", "no"}:
        users = users.filter(is_active=(is_active == "yes"))

    rows = []
    for u in users:
        tariff = _get_user_tariff_group(u)
        if tariff and hasattr(tariff, "profile"):
            groups_str = f"{tariff.profile.public_name} ({tariff.name})"
        else:
            groups_str = "—"
        rows.append({"user": u, "groups_str": groups_str})

    context = {
        **admin.site.each_context(request),
        "title": "Пользователи",
        "rows": rows,
        "groups_for_filter": _tariff_groups_queryset(),
        "filters": {
            "username": username,
            "email": email,
            "tariff_code": tariff_code,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
            "is_active": is_active,
        },
    }
    return TemplateResponse(request, "admin/users_table.html", context)


def admin_courses_table(request):
    if not _is_admins_or_superuser(request.user):
        return HttpResponseForbidden("Forbidden")

    title = request.GET.get("title", "").strip()
    created_by_id = request.GET.get("created_by_id", "").strip()
    tariff_code = request.GET.get("tariff_code", "").strip()
    lessons_min = request.GET.get("lessons_min", "").strip()
    has_final = request.GET.get("has_final", "").strip()

    courses = (
        Course.objects.all()
        .select_related("created_by", "section")
        .prefetch_related("allowed_groups", "allowed_groups__profile")
        .annotate(
            lessons_count=Count("lessons", distinct=True),
            final_count=Count("lessons", filter=Q(lessons__is_final=True), distinct=True),
        )
        .order_by("title", "id")
    )

    if title:
        courses = courses.filter(title__icontains=title)
    if created_by_id.isdigit():
        courses = courses.filter(created_by_id=int(created_by_id))
    if tariff_code:
        courses = courses.filter(allowed_groups__name=tariff_code).distinct()
    if lessons_min.isdigit():
        courses = courses.filter(lessons_count__gte=int(lessons_min))
    if has_final in {"yes", "no"}:
        if has_final == "yes":
            courses = courses.filter(final_count__gt=0)
        else:
            courses = courses.filter(final_count=0)

    rows = []
    for c in courses:
        groups = [g for g in c.allowed_groups.all() if hasattr(g, "profile") and g.name != "admins"]
        groups_str = ", ".join(f"{g.profile.public_name} ({g.name})" for g in groups) or "—"
        rows.append({"course": c, "groups_str": groups_str})

    admins = (
        User.objects
        .filter(Q(is_superuser=True) | Q(groups__profile__is_admin_group=True))
        .distinct()
        .order_by("username", "id")
    )

    context = {
        **admin.site.each_context(request),
        "title": "Модули",
        "rows": rows,
        "users_for_filter": admins,
        "groups_for_filter": _tariff_groups_queryset(),
        "filters": {
            "title": title,
            "created_by_id": created_by_id,
            "tariff_code": tariff_code,
            "lessons_min": lessons_min,
            "has_final": has_final,
        },
    }
    return TemplateResponse(request, "admin/courses_table.html", context)


def admin_tariffs_table(request):
    if not _is_admins_or_superuser(request.user):
        return HttpResponseForbidden("Forbidden")

    name = request.GET.get("name", "").strip()
    paid_enabled = request.GET.get("paid_enabled", "").strip()

    groups = (
        Group.objects
        .filter(profile__isnull=False)
        .select_related("profile", "profile__created_by")
        .order_by("name", "id")
    )

    if name:
        groups = groups.filter(Q(name__icontains=name) | Q(profile__public_name__icontains=name))

    rows = []
    for group in groups:
        profile = getattr(group, "profile", None)
        payment_ready = bool(
            profile
            and profile.payment_amount > 0
            and profile.payment_currency
            and profile.payment_user_name
            and profile.payment_password
        )
        if paid_enabled == "yes" and not payment_ready:
            continue
        if paid_enabled == "no" and payment_ready:
            continue
        rows.append({
            "group": group,
            "profile": profile,
            "payment_ready": payment_ready,
        })

    context = {
        **admin.site.each_context(request),
        "title": "Группы",
        "rows": rows,
        "filters": {
            "name": name,
            "paid_enabled": paid_enabled,
        },
    }
    return TemplateResponse(request, "admin/tariffs_table.html", context)


def admin_course_user_history(request, user_id: int, course_id: int):
    if not _is_admins_or_superuser(request.user):
        return HttpResponseForbidden("Forbidden")

    u = User.objects.prefetch_related("groups").get(pk=user_id)
    c = Course.objects.get(pk=course_id)

    lessons = list(Lesson.objects.filter(course=c).order_by("order", "id"))

    attempts = (
        LessonAttempt.objects
        .filter(user=u, lesson__course=c)
        .select_related("lesson")
        .prefetch_related("answers", "answers__question")
        .order_by("lesson__order", "lesson__id", "retake_round", "created_at")
    )

    lesson_to_attempts = {lesson.id: [] for lesson in lessons}
    for a in attempts:
        lesson_to_attempts.setdefault(a.lesson_id, []).append(a)

    history_rows = [{"lesson": lesson, "attempts": lesson_to_attempts.get(lesson.id, [])} for lesson in lessons]

    context = {
        **admin.site.each_context(request),
        "title": f"История: {u.username} / {c.title}",
        "user_obj": u,
        "course": c,
        "history_rows": history_rows,
        "pass_threshold": PASS_THRESHOLD,
    }
    return TemplateResponse(request, "admin/course_user_history.html", context)


def get_admin_custom_urls():
    return [
        path(
            "users/",
            admin.site.admin_view(admin_users_table),
            name="users_table",
        ),
        path(
            "modules/table/",
            admin.site.admin_view(admin_courses_table),
            name="courses_table",
        ),
        path(
            "tariffs/",
            admin.site.admin_view(admin_tariffs_table),
            name="tariffs_table",
        ),
        path(
            "modules/progress/",
            admin.site.admin_view(admin_courses_progress),
            name="courses_progress",
        ),
        path(
            "modules/lessons-by-module/<int:course_id>/",
            admin.site.admin_view(admin_lessons_by_course),
            name="lessons_by_course",
        ),
        path(
            "modules/by-section/<int:section_id>/",
            admin.site.admin_view(admin_modules_by_section),
            name="modules_by_section",
        ),
        path(
            "modules/history/<int:user_id>/<int:course_id>/",
            admin.site.admin_view(admin_course_user_history),
            name="course_user_history",
        ),
    ]


class RegistrationUserCreationForm(UserCreationForm):
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    email = forms.EmailField(required=True)
    tariff_group = forms.ModelChoiceField(
        required=True,
        queryset=_tariff_groups_queryset(),
        label="Группа",
        empty_label=None,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "tariff_group", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tariff_group"].queryset = _tariff_groups_queryset()
        self.fields["tariff_group"].label_from_instance = self._tariff_label
        if "username" in self.fields:
            self.fields["username"].label = "Логин"

    def _tariff_groups(self):
        return list(_tariff_groups_queryset())

    def _tariff_group_ids(self):
        return [g.id for g in self._tariff_groups()]

    @staticmethod
    def _tariff_label(group: Group):
        if hasattr(group, "profile"):
            return f"{group.profile.public_name} ({group.name})"
        return group.name

    @staticmethod
    def _validate_password_rules(password: str) -> None:
        errors = []
        if len(password) < 10:
            errors.append("Пароль должен быть не короче 10 символов.")
        if not re.search(r"[A-Za-z]", password):
            errors.append("Пароль должен содержать хотя бы одну букву.")
        if not re.search(r"\d", password):
            errors.append("Пароль должен содержать хотя бы одну цифру.")
        if not re.search(r"[^A-Za-z0-9]", password):
            errors.append("Пароль должен содержать хотя бы один спецсимвол.")
        if errors:
            raise ValidationError(errors)

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1", "") or ""
        password2 = self.cleaned_data.get("password2", "") or ""
        if password1 and password2 and password1 != password2:
            raise ValidationError("Пароли не совпадают.")
        self._validate_password_rules(password1)
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.pk is None:
            user.save()
        tariff_group = self.cleaned_data.get("tariff_group")
        if tariff_group:
            tariff_group_ids = self._tariff_group_ids()
            if tariff_group_ids:
                user.groups.remove(*Group.objects.filter(id__in=tariff_group_ids))
            user.groups.add(tariff_group)
        if commit:
            self.save_m2m()
        return user


class RegistrationUserChangeForm(UserChangeForm):
    tariff_group = forms.ModelChoiceField(
        required=False,
        queryset=_tariff_groups_queryset(),
        label="Группа",
    )

    class Meta(UserChangeForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "tariff_group", "is_active", "is_staff", "is_superuser")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tariff_group"].queryset = _tariff_groups_queryset()
        self.fields["tariff_group"].label_from_instance = RegistrationUserCreationForm._tariff_label
        if "username" in self.fields:
            self.fields["username"].label = "Логин"
        if "is_staff" in self.fields:
            self.fields["is_staff"].label = "Админ"
        if self.instance and self.instance.pk:
            current_group = _get_user_tariff_group(self.instance)
            if current_group:
                self.fields["tariff_group"].initial = current_group

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.pk is None:
            user.save()
        tariff_group = self.cleaned_data.get("tariff_group")
        tariff_group_ids = [g.id for g in _tariff_groups_queryset()]
        if tariff_group_ids:
            user.groups.remove(*Group.objects.filter(id__in=tariff_group_ids))
        if tariff_group:
            user.groups.add(tariff_group)
        if commit:
            self.save_m2m()
        return user


class GroupProfileInline(admin.StackedInline):
    model = GroupProfile
    form = GroupProfileAdminForm
    can_delete = False
    extra = 0
    template = "admin/edit_inline/group_profile_stacked.html"


class CustomUserAdmin(DjangoUserAdmin):
    add_form = RegistrationUserCreationForm
    form = RegistrationUserChangeForm
    add_fieldsets = (
        (None, {"fields": ("username", "password1", "password2")}),
        ("Личные данные", {"fields": ("first_name", "last_name", "email", "tariff_group")}),
    )
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Личные данные", {"fields": ("first_name", "last_name", "email")}),
        ("Группа", {"fields": ("tariff_group",)}),
        ("Доступ", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Важные даты", {"fields": ("last_login", "date_joined")}),
    )
    filter_horizontal = ()

    def changelist_view(self, request, extra_context=None):
        return admin_users_table(request)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        tariff_groups = list(_tariff_groups_queryset().filter(user=obj))
        if len(tariff_groups) > 1:
            keep = sorted(tariff_groups, key=lambda g: g.name)[0]
            obj.groups.remove(*[g for g in tariff_groups if g.id != keep.id])
        obj.user_permissions.clear()

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


class GroupAdmin(DjangoGroupAdmin):
    inlines = [GroupProfileInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("profile", "profile__created_by")

    def changelist_view(self, request, extra_context=None):
        missing = Group.objects.filter(profile__isnull=True)
        for group in missing:
            GroupProfile.objects.get_or_create(group=group, defaults={
                "public_name": group.name,
                "access_duration_days": 0,
            })
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        profile = getattr(obj, "profile", None)
        if profile is None:
            profile, _ = GroupProfile.objects.get_or_create(group=obj, defaults={
                "public_name": obj.name,
                "access_duration_days": 0,
            })
        if not profile.created_by:
            profile.created_by = request.user if request.user.is_authenticated else None
            profile.save(update_fields=["created_by"])

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []
        return super().get_inline_instances(request, obj=obj)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:group_id>/delete-confirm/",
                self.admin_site.admin_view(self.delete_group_view),
                name="auth_group_delete_confirm",
            ),
        ]
        return custom + urls

    def delete_group_view(self, request, group_id: int):
        if not _is_admins_or_superuser(request.user):
            return HttpResponseForbidden("Forbidden")

        group = Group.objects.filter(pk=group_id).select_related("profile").first()
        if not group:
            return redirect("admin:auth_group_changelist")

        users_qs = group.user_set.all()
        if request.method == "POST":
            if users_qs.exists():
                messages.error(request, "Нельзя удалить группу: есть привязанные пользователи.")
                return redirect("admin:auth_group_delete_confirm", group_id=group.id)
            group.delete()
            messages.success(request, "Группа удалена.")
            return redirect("admin:auth_group_changelist")

        context = {
            **self.admin_site.each_context(request),
            "title": "Удаление группы",
            "group_obj": group,
            "has_users": users_qs.exists(),
            "users_count": users_qs.count(),
        }
        return TemplateResponse(request, "admin/auth/group/delete_confirm.html", context)

    def has_delete_permission(self, request, obj=None):
        return _is_admins_or_superuser(request.user)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass
admin.site.register(Group, GroupAdmin)
