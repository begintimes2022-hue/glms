from django.db import models
from django.contrib.auth.models import User, Group
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
import uuid


ANSWER_CHOICES = ("A", "B", "C", "D")


def normalize_answer_codes(values) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        raw_values = values.split(",")
    else:
        raw_values = values

    normalized = []
    seen = set()
    for value in raw_values:
        if value is None:
            continue
        code = str(value).strip().upper()
        if code in ANSWER_CHOICES and code not in seen:
            normalized.append(code)
            seen.add(code)
    return ",".join(normalized)


class Course(models.Model):
    section = models.ForeignKey(
        "KnowledgeBaseSection",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    allowed_groups = models.ManyToManyField(
        Group,
        blank=True,
        help_text="Группы, которым доступен этот модуль.",
        related_name="allowed_courses",
    )

    class Meta:
        verbose_name = "Модуль"
        verbose_name_plural = "Модули"

    def __str__(self):
        return self.title


class LearningCourse(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_learning_courses")
    created_at = models.DateTimeField(auto_now_add=True)
    allowed_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="allowed_learning_courses",
    )

    class Meta:
        verbose_name = "Курс"
        verbose_name_plural = "Курсы"

    def __str__(self):
        return self.title


class LearningCourseItem(models.Model):
    ITEM_ARTICLE = "article"
    ITEM_SECTION = "section"
    ITEM_MODULE = "module"
    ITEM_TEST = "test"
    ITEM_TYPE_CHOICES = [
        (ITEM_SECTION, "Раздел"),
        (ITEM_MODULE, "Модуль"),
        (ITEM_ARTICLE, "Статья"),
        (ITEM_TEST, "Тест"),
    ]

    learning_course = models.ForeignKey(
        LearningCourse,
        on_delete=models.CASCADE,
        related_name="items",
    )
    order_index = models.PositiveIntegerField(default=0)
    item_type = models.CharField(max_length=16, choices=ITEM_TYPE_CHOICES)
    section = models.ForeignKey(
        "KnowledgeBaseSection",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="learning_course_items",
    )
    module = models.ForeignKey(
        Course,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="learning_course_items",
    )
    lesson = models.ForeignKey(
        "Lesson",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="learning_course_items",
    )
    is_final_test = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Элемент курса"
        verbose_name_plural = "Элементы курса"
        ordering = ["order_index", "id"]

    def clean(self):
        super().clean()
        errors = {}

        if self.item_type == self.ITEM_SECTION:
            if not self.section_id:
                errors["section"] = "Для элемента типа 'Раздел' нужно выбрать раздел."
            if self.module_id:
                errors["module"] = "Для элемента типа 'Раздел' нельзя выбирать модуль."
            if self.lesson_id:
                errors["lesson"] = "Для элемента типа 'Раздел' нельзя выбирать статью."
            if self.is_final_test:
                errors["is_final_test"] = "Итоговый тест можно назначить только для элемента типа 'Тест'."
        elif self.item_type == self.ITEM_MODULE:
            if not self.module_id:
                errors["module"] = "Для элемента типа 'Модуль' нужно выбрать модуль."
            if self.lesson_id:
                errors["lesson"] = "Для элемента типа 'Модуль' нельзя выбирать статью."
            if self.section_id and self.module_id and self.module.section_id != self.section_id:
                errors["section"] = "Выбранный раздел не соответствует выбранному модулю."
        elif self.item_type == self.ITEM_ARTICLE:
            if not self.lesson_id:
                errors["lesson"] = "Для элемента типа 'Статья' нужно выбрать статью."
            if self.module_id and self.lesson_id and self.lesson.course_id != self.module_id:
                errors["module"] = "Выбранный модуль не соответствует выбранной статье."
            if self.section_id and self.lesson_id and self.lesson.course.section_id != self.section_id:
                errors["section"] = "Выбранный раздел не соответствует выбранной статье."
        elif self.item_type == self.ITEM_TEST:
            if not self.lesson_id:
                errors["lesson"] = "Для элемента типа 'Тест' нужно выбрать статью, к которой привязан тест."
            if self.lesson_id and not self.lesson.questions.exists():
                errors["lesson"] = "У выбранной статьи нет вопросов, поэтому ее нельзя использовать как тест."
            if self.module_id and self.lesson_id and self.lesson.course_id != self.module_id:
                errors["module"] = "Выбранный модуль не соответствует выбранной статье."
            if self.section_id and self.lesson_id and self.lesson.course.section_id != self.section_id:
                errors["section"] = "Выбранный раздел не соответствует выбранной статье."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        if self.item_type == self.ITEM_SECTION and self.section_id:
            return f"{self.learning_course}: {self.section}"
        if self.item_type == self.ITEM_MODULE and self.module_id:
            return f"{self.learning_course}: {self.module}"
        if self.lesson_id:
            return f"{self.learning_course}: {self.lesson}"
        return f"{self.learning_course}: {self.get_item_type_display()}"


class Lesson(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=200)
    content = models.TextField()
    order = models.IntegerField(default=0)
    is_final = models.BooleanField(
        default=False,
        help_text="Отметьте, если это финальная статья.",
    )

    def clean(self):
        super().clean()
        if not self.is_final:
            return
        clash_exists = Lesson.objects.filter(course=self.course, is_final=True).exclude(pk=self.pk).exists()
        if clash_exists:
            raise ValidationError({"is_final": "В модуле может быть только одна финальная статья."})

    class Meta:
        verbose_name = "Статья"
        verbose_name_plural = "Статьи"
        indexes = [
            GinIndex(
                SearchVector("title", weight="A", config="russian")
                + SearchVector("content", weight="B", config="russian"),
                name="lesson_search_gin",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.course.title})"


class Question(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="questions")
    question_text = models.CharField(max_length=300)
    option_a = models.CharField(max_length=100)
    option_b = models.CharField(max_length=100)
    option_c = models.CharField(max_length=100)
    option_d = models.CharField(max_length=100, blank=True, default="")
    correct_answer = models.CharField(max_length=7)

    class Meta:
        verbose_name = "Тест"
        verbose_name_plural = "Тесты"

    def clean(self):
        super().clean()
        self.correct_answer = normalize_answer_codes(self.correct_answer)
        if not self.correct_answer:
            raise ValidationError("Нужно выбрать хотя бы один правильный ответ.")
        if "D" in self.correct_answer.split(",") and not self.option_d.strip():
            raise ValidationError({"option_d": "Нельзя выбрать вариант D, если он пустой."})

    def is_selection_correct(self, selected_codes) -> bool:
        return bool(normalize_answer_codes(selected_codes)) and normalize_answer_codes(selected_codes) == self.correct_answer

    def __str__(self):
        return self.question_text[:50]


class LearningCourseFinalQuestion(models.Model):
    learning_course = models.ForeignKey(
        LearningCourse,
        on_delete=models.CASCADE,
        related_name="final_questions",
    )
    question_text = models.CharField(max_length=300)
    option_a = models.CharField(max_length=100)
    option_b = models.CharField(max_length=100)
    option_c = models.CharField(max_length=100)
    option_d = models.CharField(max_length=100, blank=True, default="")
    correct_answer = models.CharField(max_length=7)

    class Meta:
        verbose_name = "Вопрос итогового теста"
        verbose_name_plural = "Вопросы итогового теста"

    def clean(self):
        super().clean()
        self.correct_answer = normalize_answer_codes(self.correct_answer)
        if not self.correct_answer:
            raise ValidationError("Нужно выбрать хотя бы один правильный ответ.")
        if "D" in self.correct_answer.split(",") and not self.option_d.strip():
            raise ValidationError({"option_d": "Нельзя выбрать вариант D, если он пустой."})

    def is_selection_correct(self, selected_codes) -> bool:
        return bool(normalize_answer_codes(selected_codes)) and normalize_answer_codes(selected_codes) == self.correct_answer

    def __str__(self):
        return self.question_text[:50]


class LessonAttempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_attempts")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="attempts")

    # цикл пересдачи: 1,2,3... (после 3 провалов создаём новый цикл)
    retake_round = models.PositiveSmallIntegerField(default=1)

    score = models.PositiveSmallIntegerField()  # 0..100
    correct = models.PositiveSmallIntegerField(default=0)
    total = models.PositiveSmallIntegerField(default=0)
    passed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "lesson", "retake_round", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.lesson.title} (round {self.retake_round}): {self.score}%"


class AttemptAnswer(models.Model):
    """
    Храним ответы пользователя в рамках конкретной попытки.
    Это нужно для истории (admins/root).
    """
    attempt = models.ForeignKey(LessonAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="attempt_answers")

    chosen_answer = models.CharField(max_length=7, blank=True, null=True)

    is_correct = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="uniq_attempt_question_answer")
        ]

    def __str__(self):
        return f"{self.attempt} | Q{self.question_id} -> {self.chosen_answer} ({'ok' if self.is_correct else 'no'})"


class LearningCourseFinalAttempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="learning_course_final_attempts")
    learning_course = models.ForeignKey(
        LearningCourse,
        on_delete=models.CASCADE,
        related_name="final_attempts",
    )
    retake_round = models.PositiveSmallIntegerField(default=1)
    score = models.PositiveSmallIntegerField(default=0)
    correct = models.PositiveSmallIntegerField(default=0)
    total = models.PositiveSmallIntegerField(default=0)
    passed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "learning_course", "retake_round", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.learning_course.title} (round {self.retake_round}): {self.score}%"


class LearningCourseFinalAnswer(models.Model):
    attempt = models.ForeignKey(
        LearningCourseFinalAttempt,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        LearningCourseFinalQuestion,
        on_delete=models.CASCADE,
        related_name="attempt_answers",
    )
    chosen_answer = models.CharField(max_length=7, blank=True, null=True)
    is_correct = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="uniq_learning_course_final_answer")
        ]

    def __str__(self):
        return f"{self.attempt} | Q{self.question_id} -> {self.chosen_answer} ({'ok' if self.is_correct else 'no'})"


class LessonView(models.Model):
    """
    Фиксируем факт, что пользователь открыл (просмотрел) урок.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_views")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="views")
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "lesson"], name="uniq_user_lesson_view")
        ]
        ordering = ["-viewed_at"]

    def __str__(self):
        return f"{self.user.username} viewed {self.lesson.title}"


class KnowledgeBaseSection(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Раздел"
        verbose_name_plural = "Разделы"
        ordering = ["order", "title", "id"]

    def __str__(self):
        return self.title


class GroupProfile(models.Model):
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="profile")
    public_name = models.CharField(max_length=200)
    is_admin_group = models.BooleanField(default=False)
    access_duration_days = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_group_profiles",
    )
    allowed_kb_lessons = models.ManyToManyField(
        Lesson,
        blank=True,
        related_name="allowed_group_profiles_for_kb",
    )
    allowed_kb_courses = models.ManyToManyField(
        Course,
        blank=True,
        related_name="allowed_group_profiles_for_kb",
    )
    allowed_kb_sections = models.ManyToManyField(
        KnowledgeBaseSection,
        blank=True,
        related_name="allowed_group_profiles",
    )
    payment_amount = models.PositiveIntegerField(default=0)
    payment_currency = models.CharField(max_length=3, default="933")
    payment_user_name = models.CharField(max_length=255, blank=True, default="")
    payment_password = models.CharField(max_length=255, blank=True, default="")
    payment_return_url = models.CharField(max_length=500, default="/payments/return/")
    payment_description = models.CharField(max_length=255, blank=True, default="")
    payment_language = models.CharField(max_length=8, default="ru")

    def __str__(self):
        return f"{self.public_name} ({self.group.name})"


class UserAccess(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="access")
    paid = models.BooleanField(default=False)
    access_start_at = models.DateTimeField(null=True, blank=True)
    access_end_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Access: {self.user.username}"


class PaymentOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Ожидает оплаты"),
        (STATUS_PAID, "Оплачен"),
        (STATUS_ERROR, "Ошибка регистрации"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    order_number = models.BigIntegerField(unique=True, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payment_orders")
    tariff_group = models.ForeignKey(Group, on_delete=models.PROTECT, related_name="payment_orders")
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="933")
    description = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    register_url = models.URLField(max_length=500)
    return_url = models.URLField(max_length=500)
    gateway_order_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    gateway_form_url = models.URLField(max_length=1000, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Платежный заказ"
        verbose_name_plural = "Платежные заказы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заказ {self.public_id} / {self.user.username}"


@receiver(post_save, sender=Group)
def _ensure_group_profile(sender, instance: Group, created: bool, **kwargs):
    if created:
        GroupProfile.objects.get_or_create(
            group=instance,
            defaults={
                "public_name": instance.name,
                "access_duration_days": 0,
            },
        )


@receiver(post_save, sender=User)
def _ensure_user_access(sender, instance: User, created: bool, **kwargs):
    if created:
        UserAccess.objects.get_or_create(user=instance)
