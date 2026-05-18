// v1.9 §3: client-side list filter for the list pages.
//
// Each filterable list is a `<table data-shield-filter>` and is wired
// to a search input (`data-shield-filter-search`) + an optional
// stage/status dropdown (`data-shield-filter-stage`) elsewhere on the
// page. We walk the table on every change event and toggle rows by
// display:none. Pure DOM filtering; the data stays in place. No
// network round-trip per keystroke.

(function () {
  'use strict';

  function normalize(s) {
    return (s || '').toString().toLowerCase().trim();
  }

  function matches(row, term, stage) {
    if (term) {
      var text = row.textContent || '';
      if (normalize(text).indexOf(term) === -1) return false;
    }
    if (stage) {
      // The stage dropdown's value matches the row's data-stage
      // attribute exactly (set by the template).
      var rowStage = normalize(row.getAttribute('data-stage'));
      if (rowStage !== normalize(stage)) return false;
    }
    return true;
  }

  function applyFilter(table) {
    var key = table.getAttribute('data-shield-filter');
    if (!key) return;
    var search = document.querySelector(
      '[data-shield-filter-search="' + key + '"]'
    );
    var stageEl = document.querySelector(
      '[data-shield-filter-stage="' + key + '"]'
    );
    var term  = search ? normalize(search.value) : '';
    var stage = stageEl ? stageEl.value : '';
    var rows = table.querySelectorAll('tbody tr');
    var shown = 0;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (matches(r, term, stage)) {
        r.style.display = '';
        shown++;
      } else {
        r.style.display = 'none';
      }
    }
    // Optional empty-state hint element keyed off the same data-attr.
    var hint = document.querySelector(
      '[data-shield-filter-empty="' + key + '"]'
    );
    if (hint) hint.style.display = shown === 0 ? '' : 'none';
  }

  function wire(table) {
    var key = table.getAttribute('data-shield-filter');
    if (!key) return;
    [
      '[data-shield-filter-search="' + key + '"]',
      '[data-shield-filter-stage="' + key + '"]',
    ].forEach(function (sel) {
      var el = document.querySelector(sel);
      if (!el) return;
      el.addEventListener('input',  function () { applyFilter(table); });
      el.addEventListener('change', function () { applyFilter(table); });
    });
    applyFilter(table);
  }

  function init() {
    var tables = document.querySelectorAll('table[data-shield-filter]');
    for (var i = 0; i < tables.length; i++) wire(tables[i]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
