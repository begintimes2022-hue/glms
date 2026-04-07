(function () {
  function clearSelect(select, placeholder) {
    if (!select) return;
    select.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.appendChild(option);
  }

  function fillSelect(select, items, selectedValue, placeholder, labelBuilder) {
    if (!select) return;
    select.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.appendChild(option);

    items.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = labelBuilder(item);
      if (selectedValue && String(selectedValue) === String(item.id)) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  }

  async function fetchJson(url) {
    const response = await fetch(url, { method: "GET", credentials: "same-origin" });
    if (!response.ok) throw new Error("Request failed");
    return response.json();
  }

  async function loadModules(sectionId, moduleSelect, selectedModuleId) {
    clearSelect(moduleSelect, sectionId ? "Загрузка модулей..." : "— выберите раздел —");
    if (!sectionId) return;

    try {
      const data = await fetchJson(`/admin/modules/by-section/${sectionId}/`);
      const items = data && data.results ? data.results : [];
      fillSelect(
        moduleSelect,
        items,
        selectedModuleId,
        items.length ? "— выберите модуль —" : "Модулей нет",
        (item) => item.title || `Module #${item.id}`
      );
    } catch (e) {
      clearSelect(moduleSelect, "Ошибка загрузки модулей");
    }
  }

  async function loadLessons(moduleId, lessonSelect, selectedLessonId) {
    clearSelect(lessonSelect, moduleId ? "Загрузка статей..." : "— выберите модуль —");
    if (!moduleId) return;

    try {
      const data = await fetchJson(`/admin/modules/lessons-by-module/${moduleId}/`);
      const items = data && data.results ? data.results : [];
      fillSelect(
        lessonSelect,
        items,
        selectedLessonId,
        items.length ? "— выберите статью —" : "Статей нет",
        (item) => {
          const order = item.order !== null && item.order !== undefined ? String(item.order).padStart(2, "0") : "--";
          return `${order}. ${item.title || `Статья #${item.id}`}`;
        }
      );
    } catch (e) {
      clearSelect(lessonSelect, "Ошибка загрузки статей");
    }
  }

  function extractId(selectId) {
    const match = selectId && selectId.match(/-(\d+|empty)-/);
    return match ? match[1] : null;
  }

  function getRowControls(row) {
    const sectionSelect = row.querySelector('select[id$="-section"]');
    const moduleSelect = row.querySelector('select[id$="-module"]');
    const lessonSelect = row.querySelector('select[id$="-lesson"]');
    const itemTypeSelect = row.querySelector('select[id$="-item_type"]');
    return { sectionSelect, moduleSelect, lessonSelect, itemTypeSelect };
  }

  async function syncRow(row, preserveSelections) {
    const { sectionSelect, moduleSelect, lessonSelect } = getRowControls(row);
    if (!sectionSelect || !moduleSelect || !lessonSelect) return;

    const selectedModuleId = preserveSelections ? moduleSelect.value : "";
    const selectedLessonId = preserveSelections ? lessonSelect.value : "";

    await loadModules(sectionSelect.value, moduleSelect, selectedModuleId);
    await loadLessons(moduleSelect.value, lessonSelect, selectedLessonId);
  }

  function bindRow(row) {
    if (!row || row.dataset.courseItemsBound === "1") return;
    row.dataset.courseItemsBound = "1";

    const { sectionSelect, moduleSelect } = getRowControls(row);
    if (!sectionSelect || !moduleSelect) return;

    sectionSelect.addEventListener("change", async function () {
      await loadModules(sectionSelect.value, moduleSelect, "");
      const { lessonSelect } = getRowControls(row);
      clearSelect(lessonSelect, "— выберите модуль —");
    });

    moduleSelect.addEventListener("change", async function () {
      const { lessonSelect } = getRowControls(row);
      await loadLessons(moduleSelect.value, lessonSelect, "");
    });

    syncRow(row, true);
  }

  function bindAllRows() {
    document.querySelectorAll("#items-group tbody tr.form-row").forEach(bindRow);
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindAllRows();

    document.body.addEventListener("click", function () {
      window.setTimeout(bindAllRows, 50);
    });
  });
})();
