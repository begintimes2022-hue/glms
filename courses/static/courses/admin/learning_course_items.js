(function () {
  let sectionsCache = null;

  function relabelAddButton() {
    document.querySelectorAll('#items-group .add-row a').forEach(function (link) {
      const text = (link.textContent || '').trim();
      if (text.indexOf('Add another') !== -1 && text.indexOf('Элемент курса') !== -1) {
        link.textContent = 'Добавить элемент курса';
      }
    });
  }

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
    if (!response.ok) {
      throw new Error("Request failed");
    }
    return response.json();
  }

  async function loadSections(sectionSelect, selectedSectionId) {
    if (!sectionSelect) return;
    clearSelect(sectionSelect, "Загрузка разделов...");

    try {
      if (!sectionsCache) {
        const data = await fetchJson("/admin/modules/sections/");
        sectionsCache = data && data.results ? data.results : [];
      }

      fillSelect(
        sectionSelect,
        sectionsCache,
        selectedSectionId,
        sectionsCache.length ? "— выберите раздел —" : "Разделов нет",
        (item) => item.title || `Раздел #${item.id}`
      );
    } catch (e) {
      clearSelect(sectionSelect, "Ошибка загрузки разделов");
    }
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
        (item) => item.title || `Модуль #${item.id}`
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

  function getRowControls(row) {
    return {
      sectionSelect: row.querySelector('select[id$="-section"]'),
      moduleSelect: row.querySelector('select[id$="-module"]'),
      lessonSelect: row.querySelector('select[id$="-lesson"]'),
    };
  }

  function bindDelete(row) {
    if (!row || row.dataset.courseItemsDeleteBound === "1") return;
    const deleteCheckbox = row.querySelector('input[type="checkbox"][id$="-DELETE"]');
    if (!deleteCheckbox) return;

    row.dataset.courseItemsDeleteBound = "1";

    deleteCheckbox.addEventListener("change", function () {
      if (deleteCheckbox.checked) {
        row.classList.add("tg-inline-row-hidden");
      } else {
        row.classList.remove("tg-inline-row-hidden");
      }
    });
  }

  async function prepareRow(row, preserveSelections) {
    if (!row) return;
    const { sectionSelect, moduleSelect, lessonSelect } = getRowControls(row);
    if (!sectionSelect || !moduleSelect || !lessonSelect) return;

    const selectedSectionId = preserveSelections ? sectionSelect.value : "";
    const selectedModuleId = preserveSelections ? moduleSelect.value : "";
    const selectedLessonId = preserveSelections ? lessonSelect.value : "";

    await loadSections(sectionSelect, selectedSectionId);
    await loadModules(sectionSelect.value, moduleSelect, selectedModuleId);
    await loadLessons(moduleSelect.value, lessonSelect, selectedLessonId);
  }

  function bindRow(row) {
    if (!row || row.dataset.courseItemsBound === "1") return;
    if (row.classList.contains("empty-form")) return;
    const { sectionSelect, moduleSelect } = getRowControls(row);
    if (!sectionSelect || !moduleSelect) return;

    row.dataset.courseItemsBound = "1";
    bindDelete(row);

    sectionSelect.addEventListener("change", async function () {
      await loadModules(sectionSelect.value, moduleSelect, "");
      const { lessonSelect } = getRowControls(row);
      clearSelect(lessonSelect, "— выберите модуль —");
    });

    moduleSelect.addEventListener("change", async function () {
      const { lessonSelect } = getRowControls(row);
      await loadLessons(moduleSelect.value, lessonSelect, "");
    });
  }

  async function initExistingRows() {
    const rows = Array.from(document.querySelectorAll("#items-group tbody tr.form-row:not(.empty-form)"));
    for (const row of rows) {
      bindRow(row);
      await prepareRow(row, true);
    }
  }

  async function initNewRow(row) {
    if (!row) return;
    bindRow(row);
    await prepareRow(row, false);
  }

  document.addEventListener("DOMContentLoaded", function () {
    relabelAddButton();
    initExistingRows();

    document.addEventListener("formset:added", function (event) {
      const row = event.target;
      window.setTimeout(function () {
        relabelAddButton();
        initNewRow(row);
      }, 0);
    });

    const tbody = document.querySelector("#items-group tbody");
    if (tbody && window.MutationObserver) {
      const observer = new MutationObserver(function (mutations) {
        relabelAddButton();
        mutations.forEach(function (mutation) {
          mutation.addedNodes.forEach(function (node) {
            if (!(node instanceof HTMLElement)) return;
            if (node.matches && node.matches("tr.form-row") && !node.classList.contains("empty-form")) {
              initNewRow(node);
            }
          });
        });
      });
      observer.observe(tbody, { childList: true, subtree: false });
    }
  });
})();
