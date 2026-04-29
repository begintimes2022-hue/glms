from html import escape
import re

import markdown
from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe

from courses.models import Lesson

register = template.Library()


_ARTICLE_LINK_RE = re.compile(r"\[([^\]]+)\]\(article:(\d+)\)")
_ANCHOR_TAG_RE = re.compile(r"<a\b(?![^>]*\btarget=)([^>]*)>", flags=re.IGNORECASE)


def _replace_article_links(source: str, route_name: str) -> str:
    lesson_ids = {int(m.group(2)) for m in _ARTICLE_LINK_RE.finditer(source)}
    if not lesson_ids:
        return source

    lessons = {
        lesson.id: lesson
        for lesson in Lesson.objects.filter(id__in=lesson_ids).select_related("course")
    }

    def repl(match):
        title = match.group(1)
        lesson_id = int(match.group(2))
        lesson = lessons.get(lesson_id)
        if not lesson:
            return match.group(0)
        url = reverse(route_name, kwargs={"course_id": lesson.course_id, "lesson_id": lesson.id})
        return f"[{title}]({url})"

    return _ARTICLE_LINK_RE.sub(repl, source)


def _force_links_new_tab(html: str) -> str:
    return _ANCHOR_TAG_RE.sub(r'<a\1 target="_blank" rel="noopener noreferrer">', html)


@register.filter(name="lesson_markdown")
def lesson_markdown(value):
    source = escape(value or "")
    source = _replace_article_links(source, "courses:lesson")
    html = markdown.markdown(
        source,
        extensions=[
            "extra",
            "sane_lists",
            "tables",
        ],
    )
    html = _force_links_new_tab(html)
    return mark_safe(html)


@register.filter(name="lesson_markdown_kb")
def lesson_markdown_kb(value):
    source = escape(value or "")
    source = _replace_article_links(source, "courses:kb_lesson_detail")
    html = markdown.markdown(
        source,
        extensions=[
            "extra",
            "sane_lists",
            "tables",
        ],
    )
    html = _force_links_new_tab(html)
    return mark_safe(html)
