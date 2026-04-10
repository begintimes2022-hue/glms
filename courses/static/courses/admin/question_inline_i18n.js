(function () {
  function relabel() {
    document.querySelectorAll('.inline-group .add-row a').forEach(function (link) {
      var text = (link.textContent || '').trim();
      if (text.indexOf('Add another') !== -1 && text.indexOf('Вопрос итогового теста') !== -1) {
        link.textContent = 'Добавить вопрос итогового теста';
      } else if (text.indexOf('Add another') !== -1 && text.indexOf('Вопрос теста') !== -1) {
        link.textContent = 'Добавить вопрос';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', relabel);
  document.addEventListener('formset:added', relabel);

  var observer = new MutationObserver(relabel);
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
