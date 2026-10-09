/* WebIntel — comportements UI globaux (CSP stricte : aucun JS inline).
 *
 * Conventions :
 * - `form[data-confirm="..."]` : confirmation avant soumission ;
 * - `[data-nav-toggle]` / `[data-nav-close]` : ouvre/ferme le menu mobile ;
 * - `window.WebIntel.toast(message, type)` : notification éphémère ;
 * - état en ligne / hors-ligne : bandeau clair, jamais de fausse dispo ;
 * - comportements spécifiques : fichiers dédiés (ex. `recherche.js`).
 */
(function () {
  "use strict";

  function lierConfirmations(racine) {
    var formulaires = (racine || document).querySelectorAll("form[data-confirm]");
    Array.prototype.forEach.call(formulaires, function (form) {
      if (form.dataset.confirmLie === "1") return;
      form.dataset.confirmLie = "1";
      form.addEventListener("submit", function (evenement) {
        var message = form.getAttribute("data-confirm") || "Confirmer ?";
        if (!window.confirm(message)) {
          evenement.preventDefault();
        }
      });
    });
  }

  function lierNavigationMobile() {
    var bouton = document.querySelector("[data-nav-toggle]");
    var fermetures = document.querySelectorAll("[data-nav-close]");
    if (!bouton) return;
    function basculer(ouvrir) {
      var ouvert = ouvrir !== undefined
        ? ouvrir
        : !document.body.classList.contains("nav-open");
      document.body.classList.toggle("nav-open", ouvert);
      bouton.setAttribute("aria-expanded", ouvert ? "true" : "false");
      bouton.setAttribute("aria-label", ouvert ? "Fermer le menu" : "Ouvrir le menu");
    }
    bouton.addEventListener("click", function () { basculer(); });
    Array.prototype.forEach.call(fermetures, function (el) {
      el.addEventListener("click", function () { basculer(false); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") basculer(false);
    });
    /* Le menu reste ouvert sur desktop/tablette : on referme au redimensionnement. */
    window.addEventListener("resize", function () {
      if (window.innerWidth >= 768) basculer(false);
    });
  }

  function majEtatReseau() {
    var bandeau = document.getElementById("offline-live");
    if (!bandeau) return;
    var horsLigne = navigator.onLine === false;
    bandeau.hidden = !horsLigne;
    document.querySelectorAll("[data-needs-network]").forEach(function (el) {
      if (horsLigne) {
        el.setAttribute("aria-disabled", "true");
      } else {
        el.removeAttribute("aria-disabled");
      }
    });
  }

  function toast(message, type) {
    var region = document.getElementById("toast-region");
    if (!region || !message) return;
    var el = document.createElement("div");
    el.className = "toast" + (type ? " " + type : "");
    el.setAttribute("role", "status");
    var texte = document.createElement("span");
    texte.textContent = message;
    var fermer = document.createElement("button");
    fermer.type = "button";
    fermer.setAttribute("aria-label", "Fermer la notification");
    fermer.textContent = "✕";
    fermer.addEventListener("click", function () { el.remove(); });
    el.appendChild(texte);
    el.appendChild(fermer);
    region.appendChild(el);
    window.setTimeout(function () { el.remove(); }, 6000);
  }

  /* Fermeture des modales au clavier (Échap) — les modales restent dans l'écran. */
  function lierModales() {
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      document.querySelectorAll("[data-modal-open]").forEach(function (m) {
        m.hidden = true;
        m.removeAttribute("data-modal-open");
      });
    });
    document.querySelectorAll("[data-modal-close]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var cible = btn.closest(".modal-backdrop, .drawer, .bottom-sheet");
        if (cible) cible.hidden = true;
      });
    });
  }

  window.WebIntel = window.WebIntel || {};
  window.WebIntel.toast = toast;

  function init() {
    lierConfirmations(document);
    lierNavigationMobile();
    lierModales();
    majEtatReseau();
    window.addEventListener("online", majEtatReseau);
    window.addEventListener("offline", majEtatReseau);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
