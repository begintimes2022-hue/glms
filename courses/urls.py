from django.urls import path
from . import views

app_name = "courses"

urlpatterns = [
    path("", views.profile, name="profile"),
    path("payments/return/", views.payment_return, name="payment_return"),
    path("payments/history/", views.payment_history, name="payment_history"),
    path("payments/retry/", views.repeat_payment, name="repeat_payment"),
    path("learning-courses/", views.learning_course_list, name="learning_list"),
    path("learning-courses/<int:learning_course_id>/", views.learning_course_detail, name="learning_detail"),
    path(
        "learning-courses/<int:learning_course_id>/items/<int:position>/",
        views.learning_course_item_detail,
        name="learning_item",
    ),
    path(
        "learning-courses/<int:learning_course_id>/items/<int:position>/mark-viewed/",
        views.mark_learning_course_item_viewed,
        name="mark_learning_item_viewed",
    ),
    path(
        "learning-courses/<int:learning_course_id>/items/<int:position>/test/",
        views.learning_course_test,
        name="learning_test",
    ),
    path(
        "learning-courses/<int:learning_course_id>/items/<int:position>/result/",
        views.learning_course_result,
        name="learning_result",
    ),
    path("modules/", views.course_list, name="list"),
    path("modules/<int:course_id>/", views.course_detail, name="detail"),
    path("modules/<int:course_id>/lessons/<int:lesson_id>/", views.lesson_detail, name="lesson"),
    path(
        "modules/<int:course_id>/lessons/<int:lesson_id>/mark-viewed/",
        views.mark_lesson_viewed,
        name="mark_lesson_viewed",
    ),
    path("modules/<int:course_id>/lessons/<int:lesson_id>/test/", views.lesson_test, name="lesson_test"),
    path("modules/<int:course_id>/lessons/<int:lesson_id>/result/", views.lesson_result, name="lesson_result"),
    path("knowledge-base/", views.kb_list, name="kb_list"),
    path(
        "knowledge-base/modules/<int:course_id>/articles/<int:lesson_id>/",
        views.kb_lesson_detail,
        name="kb_lesson_detail",
    ),
]
