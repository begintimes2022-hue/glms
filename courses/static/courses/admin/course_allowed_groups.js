(function () {
  function sortOptions(select) {
    const options = Array.from(select.options).sort((a, b) =>
      a.text.localeCompare(b.text, "ru")
    );
    select.innerHTML = "";
    options.forEach((opt) => select.appendChild(opt));
  }

  function buildOption(value, text, selected) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.text = text;
    opt.selected = !!selected;
    return opt;
  }

  document.addEventListener("DOMContentLoaded", function () {
    const allowed = document.getElementById("id_allowed_groups");
    if (!allowed || allowed.dataset.enhanced === "1") return;
    allowed.dataset.enhanced = "1";

    const all = Array.from(allowed.options).map((opt) => ({
      value: opt.value,
      text: opt.text,
      selected: opt.selected,
    }));

    const originalParent = allowed.parentNode;
    const nextSibling = allowed.nextSibling;

    const wrapper = document.createElement("div");
    wrapper.className = "course-groups-widget";

    const header = document.createElement("div");
    header.className = "course-groups-widget__header";
    header.textContent = "Разрешенные группы";

    const row = document.createElement("div");
    row.className = "course-groups-widget__row";

    const controls = document.createElement("div");
    controls.className = "course-groups-widget__controls";

    const available = document.createElement("select");
    available.className = "course-groups-widget__available";

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "course-groups-widget__btn";
    addBtn.textContent = "+";
    addBtn.title = "Добавить выбранную группу";

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "course-groups-widget__btn";
    removeBtn.textContent = "-";
    removeBtn.title = "Убрать выделенные группы";

    const hint = document.createElement("p");
    hint.className = "course-groups-widget__hint";
    hint.textContent = "Выберите группу в выпадающем списке и нажмите +. Для удаления выделите группу в списке слева и нажмите -.";

    allowed.innerHTML = "";
    allowed.classList.add("course-groups-widget__selected");
    allowed.setAttribute("multiple", "multiple");
    allowed.size = 8;

    all.forEach((item) => {
      if (item.selected) {
        allowed.appendChild(buildOption(item.value, item.text, true));
      } else {
        available.appendChild(buildOption(item.value, item.text, false));
      }
    });

    sortOptions(allowed);
    sortOptions(available);

    addBtn.addEventListener("click", function () {
      const value = available.value;
      if (!value) return;
      const option = available.options[available.selectedIndex];
      allowed.appendChild(buildOption(option.value, option.text, true));
      available.remove(available.selectedIndex);
      sortOptions(allowed);
      sortOptions(available);
    });

    removeBtn.addEventListener("click", function () {
      const selected = Array.from(allowed.selectedOptions);
      selected.forEach((opt) => {
        available.appendChild(buildOption(opt.value, opt.text, false));
        opt.remove();
      });
      sortOptions(allowed);
      sortOptions(available);
    });

    const form = allowed.closest("form");
    if (form) {
      form.addEventListener("submit", function () {
        Array.from(allowed.options).forEach((opt) => {
          opt.selected = true;
        });
      });
    }

    controls.appendChild(available);
    controls.appendChild(addBtn);
    controls.appendChild(removeBtn);

    row.appendChild(controls);

    wrapper.appendChild(header);
    wrapper.appendChild(row);
    wrapper.appendChild(hint);

    originalParent.insertBefore(wrapper, nextSibling);
    row.prepend(allowed);
  });
})();
