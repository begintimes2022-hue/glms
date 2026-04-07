(function () {
  function clearLessons(lessonSelect, placeholderText) {
    lessonSelect.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = placeholderText || "— выберите курс —";
    lessonSelect.appendChild(opt);
  }

  async function loadLessons(courseId, lessonSelect, selectedLessonId) {
    clearLessons(lessonSelect, "Загрузка статей...");
    if (!courseId) return;

    try {
      const url = `/admin/modules/lessons-by-module/${courseId}/`;
      const resp = await fetch(url, { method: "GET", credentials: "same-origin" });
      if (!resp.ok) {
        clearLessons(lessonSelect, "Ошибка загрузки статей");
        return;
      }

      const data = await resp.json();
      const items = data && data.results ? data.results : [];

      lessonSelect.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = items.length ? "— выберите статью —" : "Статей нет";
      lessonSelect.appendChild(placeholder);

      items.forEach((item) => {
        const opt = document.createElement("option");
        opt.value = item.id;

        const order = item.order !== null && item.order !== undefined ? String(item.order).padStart(2, "0") : "--";
        const title = item.title || item.text || `Lesson #${item.id}`;
        opt.textContent = `${order}. ${title}`;

        if (selectedLessonId && String(selectedLessonId) === String(item.id)) {
          opt.selected = true;
        }
        lessonSelect.appendChild(opt);
      });
    } catch (e) {
      clearLessons(lessonSelect, "Ошибка загрузки статей");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const courseSelect = document.getElementById("id_course");
    const lessonSelect = document.getElementById("id_lesson");
    if (!courseSelect || !lessonSelect) return;

    courseSelect.addEventListener("change", function () {
      const courseId = this.value;
      if (!courseId) {
        clearLessons(lessonSelect, "— выберите курс —");
        return;
      }
      loadLessons(courseId, lessonSelect, null);
    });

    const initialCourse = courseSelect.value;
    const initialLesson = lessonSelect.value;
    if (initialCourse) {
      loadLessons(initialCourse, lessonSelect, initialLesson);
    } else {
      clearLessons(lessonSelect, "— выберите курс —");
    }
  });
})();
