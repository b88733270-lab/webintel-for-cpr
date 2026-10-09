/* WebIntel — progression de recherche en 6 étapes (fichier externe, CSP).
 * Affichage honnête : les étapes décrivent le parcours de la requête,
 * pas un résultat. Hors-ligne : message clair + arrêt, jamais de fausse
 * disponibilité. La soumission reste un GET classique (sans JS : le
 * formulaire fonctionne seul, l'écran de progression est progressif).
 */
(function () {
  "use strict";

  var ETAPES = [
    "Compréhension",
    "Découverte",
    "Collecte",
    "Analyse",
    "Vérification",
    "Synthèse"
  ];

  function afficherProgression(form) {
    var zone = document.getElementById("recherche-progression");
    if (!zone) return false;
    if (navigator.onLine === false) {
      var erreur = document.getElementById("recherche-offline");
      if (erreur) {
        erreur.hidden = false;
        erreur.scrollIntoView({ block: "nearest" });
      }
      return true; /* soumission bloquée par l'appelant */
    }
    zone.hidden = false;
    var items = zone.querySelectorAll(".steps li");
    var barre = zone.querySelector(".progress > div");
    var statut = zone.querySelector("[data-step-status]");
    var index = 0;
    function avancer() {
      items.forEach(function (li, i) {
        li.classList.toggle("is-done", i < index);
        li.classList.toggle("is-active", i === index);
      });
      if (barre) {
        barre.style.width = Math.round(((index) / ETAPES.length) * 100) + "%";
        barre.parentElement.setAttribute(
          "aria-valuenow", String(Math.round((index / ETAPES.length) * 100)));
      }
      if (statut && ETAPES[index]) {
        statut.textContent = "Étape " + (index + 1) + " sur 6 — " + ETAPES[index] + "…";
      }
      index += 1;
      if (index < ETAPES.length) {
        window.setTimeout(avancer, 650);
      } else if (statut) {
        statut.textContent = "Dernière étape — Synthèse… envoi de la requête.";
      }
    }
    avancer();
    zone.scrollIntoView({ block: "nearest" });
    return false;
  }

  function init() {
    var form = document.querySelector("[data-search-progress]");
    if (!form) return;
    form.addEventListener("submit", function (evenement) {
      if (navigator.onLine === false) {
        evenement.preventDefault();
        afficherProgression(form);
        return;
      }
      /* Laisse la navigation se faire ; l'écran suivant affiche les résultats. */
      afficherProgression(form);
    });
    var reessayer = document.querySelector("[data-retry-search]");
    if (reessayer) {
      reessayer.addEventListener("click", function () {
        window.location.reload();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
