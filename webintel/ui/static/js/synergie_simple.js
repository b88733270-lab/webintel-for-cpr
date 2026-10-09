/* WebIntel — page « synergie simple » : ajout dynamique de lignes de mesure.
 *
 * Extrait du template `synergie_simple.html` (chantier robustesse : la
 * Content-Security-Policy `script-src 'self'` interdit le JS inline).
 * Les boutons portent `data-ajouter-ligne`, `data-table` et `data-noms`
 * (liste de noms de champs séparés par des virgules).
 */
(function () {
  "use strict";

  function ajouterLigne(tableId, noms) {
    var table = document.getElementById(tableId);
    if (!table) {
      return;
    }
    var tr = document.createElement("tr");
    noms.forEach(function (n) {
      var td = document.createElement("td");
      var input = document.createElement("input");
      input.type = "text";
      input.name = n;
      input.size = 12;
      td.appendChild(input);
      tr.appendChild(td);
    });
    table.appendChild(tr);
  }

  function lier(racine) {
    var boutons = (racine || document).querySelectorAll("[data-ajouter-ligne]");
    Array.prototype.forEach.call(boutons, function (btn) {
      btn.addEventListener("click", function () {
        var noms = (btn.getAttribute("data-noms") || "")
          .split(",")
          .map(function (s) { return s.trim(); })
          .filter(function (s) { return s.length > 0; });
        ajouterLigne(btn.getAttribute("data-table"), noms);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { lier(document); });
  } else {
    lier(document);
  }
})();
