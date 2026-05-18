/* Capability-list table editor.
 *
 * Attaches add-row / remove-row / on-submit handlers to every
 * `.shield-capability-table-editor` on the page. The inline-script
 * version of this lived in the Jinja partial originally, but
 * SHIELD's CSP is `script-src 'self'` (no 'unsafe-inline'), so it
 * was silently blocked and the form submitted with an empty hidden
 * JSON field — surfacing as "Items JSON is malformed: Expecting
 * value: line 1 column 1 (char 0)" on /finalize.
 *
 * Loading via `<script src="…/capability_table_editor.js" defer>`
 * is allowed by the CSP and runs after parse on DOMContentLoaded.
 */
(function () {
  "use strict";

  function init() {
    document
      .querySelectorAll(".shield-capability-table-editor")
      .forEach(function (root) {
        var tbody = root.querySelector(".shield-capability-table__body");
        var jsonInput = root.querySelector(".shield-capability-table__json");
        if (!tbody || !jsonInput) return;

        function newRow() {
          var tr = document.createElement("tr");
          tr.className = "shield-capability-row";
          var cols = [
            "name",
            "vendor",
            "category",
            "function",
            "annual_cost_usd",
            "license_count",
            "notes",
          ];
          cols.forEach(function (col) {
            var td = document.createElement("td");
            var inp = document.createElement("input");
            inp.className = "usa-input usa-input--small";
            inp.type =
              col === "annual_cost_usd" || col === "license_count"
                ? "number"
                : "text";
            inp.dataset.col = col;
            td.appendChild(inp);
            tr.appendChild(td);
          });
          var actTd = document.createElement("td");
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "usa-button usa-button--unstyled shield-capability-row__remove";
          btn.setAttribute("aria-label", "Remove row");
          btn.textContent = "×";
          actTd.appendChild(btn);
          tr.appendChild(actTd);
          return tr;
        }

        var addBtn = root.querySelector(".shield-capability-table__add");
        if (addBtn) {
          addBtn.addEventListener("click", function () {
            tbody.appendChild(newRow());
          });
        }

        // Delegated remove handler.
        tbody.addEventListener("click", function (e) {
          var btn = e.target.closest(".shield-capability-row__remove");
          if (!btn) return;
          var tr = btn.closest("tr");
          if (tr && tr.parentNode) tr.parentNode.removeChild(tr);
        });

        // Serialize the visible table to the hidden JSON input on
        // form submit. Rows without `name` are dropped (matches the
        // server-side filter).
        var form = root.closest("form");
        if (form) {
          form.addEventListener("submit", function () {
            var items = [];
            tbody.querySelectorAll(".shield-capability-row").forEach(function (tr) {
              var row = {};
              tr.querySelectorAll("input[data-col]").forEach(function (inp) {
                var v = inp.value;
                if (v === "") return;
                if (inp.type === "number") {
                  var n = parseInt(v, 10);
                  if (!isNaN(n)) row[inp.dataset.col] = n;
                } else {
                  row[inp.dataset.col] = v;
                }
              });
              if (row.name) items.push(row);
            });
            // Always set the hidden field — even an empty list
            // serializes to "[]", which the server can parse.
            jsonInput.value = JSON.stringify(items);
          });
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
