(function () {
  function createOption(opt) {
    const option = document.createElement("option");
    option.value = opt.value;
    option.textContent = opt.text;
    return option;
  }

  function buildDualList(selectEl) {
    if (!selectEl || selectEl.dataset.tgDualReady === "1") {
      return;
    }
    selectEl.dataset.tgDualReady = "1";

    const wrapper = document.createElement("div");
    wrapper.className = "tg-dual";

    const left = document.createElement("div");
    left.className = "tg-dual-col";
    const leftLabel = document.createElement("div");
    leftLabel.className = "tg-dual-label";
    leftLabel.textContent = "Available";
    const leftSelect = document.createElement("select");
    leftSelect.multiple = true;
    leftSelect.size = 12;
    leftSelect.className = "tg-dual-select";
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "tg-dual-btn";
    addBtn.textContent = "+";

    const leftHeader = document.createElement("div");
    leftHeader.className = "tg-dual-header";
    leftHeader.appendChild(leftLabel);
    leftHeader.appendChild(addBtn);
    left.appendChild(leftHeader);
    left.appendChild(leftSelect);

    const right = document.createElement("div");
    right.className = "tg-dual-col";
    const rightLabel = document.createElement("div");
    rightLabel.className = "tg-dual-label";
    rightLabel.textContent = "Selected";
    const rightSelect = document.createElement("select");
    rightSelect.multiple = true;
    rightSelect.size = 12;
    rightSelect.className = "tg-dual-select";
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "tg-dual-btn tg-dual-btn-danger";
    removeBtn.textContent = "-";

    const rightHeader = document.createElement("div");
    rightHeader.className = "tg-dual-header";
    rightHeader.appendChild(rightLabel);
    rightHeader.appendChild(removeBtn);
    right.appendChild(rightHeader);
    right.appendChild(rightSelect);

    wrapper.appendChild(left);
    wrapper.appendChild(right);

    selectEl.classList.add("tg-dual-hidden");
    selectEl.style.display = "none";
    selectEl.parentElement.insertBefore(wrapper, selectEl);

    function renderFromOriginal() {
      leftSelect.innerHTML = "";
      rightSelect.innerHTML = "";
      Array.from(selectEl.options).forEach((opt) => {
        const target = opt.selected ? rightSelect : leftSelect;
        target.appendChild(createOption(opt));
      });
    }

    function syncOriginalFromRight() {
      const selectedValues = new Set(Array.from(rightSelect.options).map((o) => o.value));
      Array.from(selectEl.options).forEach((opt) => {
        opt.selected = selectedValues.has(opt.value);
      });
    }

    function move(source, target) {
      const selected = Array.from(source.selectedOptions);
      selected.forEach((opt) => {
        target.appendChild(opt);
      });
      syncOriginalFromRight();
    }

    addBtn.addEventListener("click", function () {
      move(leftSelect, rightSelect);
    });

    removeBtn.addEventListener("click", function () {
      move(rightSelect, leftSelect);
    });

    leftSelect.addEventListener("dblclick", function () {
      move(leftSelect, rightSelect);
    });

    rightSelect.addEventListener("dblclick", function () {
      move(rightSelect, leftSelect);
    });

    renderFromOriginal();
  }

  function init() {
    const select = document.querySelector('select[name="permissions"]');
    if (select) {
      buildDualList(select);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
