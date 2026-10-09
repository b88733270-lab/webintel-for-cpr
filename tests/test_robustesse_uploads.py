"""Tests du durcissement des imports (chantier robustesse — P1-2 de l'audit).

- Rejet propre (400 + message français) des contenus incohérents :
  archive ZIP déguisée en .csv/.sdf, faux .xlsx, faux JSON.
- Anti-« ZIP bomb » sur les .xlsx : taille décompressée, ratio de
  compression, nombre de membres et traversal inspectés AVANT toute
  décompression complète.
- Plafond de lignes par import (tous formats).
- Non-régression : un vrai petit .xlsx s'importe toujours.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from webintel.science import import_export as import_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fausse_zip_bomb(ratio_cible: float = 1000.0) -> bytes:
    """Construit en mémoire un ZIP au ratio de compression élevé.

    5 Mo de zéros → ~5 ko compressés (ratio ≈ 1000) : détectable par le
    contrôle SANS écrire de fichier dangereux sur disque.
    """
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", b"\x00" * (5 * 1024 * 1024))
    contenu = tampon.getvalue()
    ratio = (5 * 1024 * 1024) / len(contenu)
    assert ratio > ratio_cible / 2, f"ratio insuffisant : {ratio:.0f}"
    return contenu


def _vrai_xlsx_minimal() -> bytes:
    """Un vrai .xlsx minimal (1 feuille, 2 lignes) via openpyxl."""
    from openpyxl import Workbook

    classeur = Workbook()
    feuille = classeur.active
    feuille["A1"] = "nom_scientifique"
    feuille["A2"] = "Moringa oleifera"
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


# ---------------------------------------------------------------------------
# Cohérence signature / contenu
# ---------------------------------------------------------------------------


def test_zip_deguise_en_csv_rejete():
    contenu = b"PK\x03\x04" + b"\x00" * 100
    with pytest.raises(import_service.ErreurImport, match="archive ZIP"):
        import_service.verifier_contenu(contenu, "csv")


def test_zip_deguise_en_sdf_rejete():
    contenu = b"PK\x03\x04" + b"\x00" * 100
    with pytest.raises(import_service.ErreurImport, match="archive ZIP"):
        import_service.verifier_contenu(contenu, "sdf")


def test_faux_xlsx_rejete():
    with pytest.raises(import_service.ErreurImport, match="pas un classeur Excel"):
        import_service.verifier_contenu(b"ceci n'est pas un xlsx", "xlsx")


def test_faux_json_rejete():
    with pytest.raises(import_service.ErreurImport, match="doit commencer"):
        import_service.verifier_contenu(b"pas du json du tout", "json")


def test_contenus_valides_acceptes():
    import_service.verifier_contenu(b"a,b\n1,2\n", "csv")
    import_service.verifier_contenu(b'[{"a": 1}]', "json")
    import_service.verifier_contenu(b"  \n[1, 2]", "json")
    import_service.verifier_contenu(_vrai_xlsx_minimal(), "xlsx")


# ---------------------------------------------------------------------------
# Anti-ZIP-bomb sur les .xlsx
# ---------------------------------------------------------------------------


def test_zip_bomb_ratio_rejetee():
    with pytest.raises(import_service.ErreurImport, match="ZIP bomb"):
        import_service.controler_archive_xlsx(_fausse_zip_bomb())


def test_zip_bomb_via_parse_excel():
    with pytest.raises(import_service.ErreurImport, match="ZIP bomb"):
        import_service.parse_excel(_fausse_zip_bomb())


def test_xlsx_trop_de_membres_rejete():
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as zf:
        for i in range(import_service.XLSX_MEMBRES_MAX + 1):
            zf.writestr(f"membre_{i}.xml", b"<a/>")
    with pytest.raises(import_service.ErreurImport, match="membres"):
        import_service.controler_archive_xlsx(tampon.getvalue())


def test_xlsx_traversal_rejete():
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as zf:
        zf.writestr("../../evil.xml", b"<a/>")
    with pytest.raises(import_service.ErreurImport, match="chemin dangereux"):
        import_service.controler_archive_xlsx(tampon.getvalue())


def test_vrai_xlsx_passe_le_controle_et_se_parse():
    contenu = _vrai_xlsx_minimal()
    import_service.controler_archive_xlsx(contenu)  # ne lève pas
    lignes = import_service.parse_excel(contenu)
    assert len(lignes) == 1
    assert lignes[0][1]["nom_scientifique"] == "Moringa oleifera"


# ---------------------------------------------------------------------------
# Plafonds de lignes
# ---------------------------------------------------------------------------


def test_csv_trop_de_lignes_rejete(monkeypatch):
    monkeypatch.setattr(import_service, "LIGNES_IMPORT_MAX", 10)
    contenu = ("a,b\n" + "1,2\n" * 11).encode()
    with pytest.raises(import_service.ErreurImport, match="trop volumineux"):
        import_service.parse_csv(contenu)


def test_json_trop_d_elements_rejete(monkeypatch):
    monkeypatch.setattr(import_service, "LIGNES_IMPORT_MAX", 10)
    contenu = ("[" + ",".join('{"a": 1}' for _ in range(11)) + "]").encode()
    with pytest.raises(import_service.ErreurImport, match="trop volumineux"):
        import_service.parse_json(contenu)


def test_sdf_trop_de_molecules_rejete(monkeypatch):
    monkeypatch.setattr(import_service, "LIGNES_IMPORT_MAX", 10)
    bloc = "> <Nom>\nmol\n$$$$\n"
    with pytest.raises(import_service.SDFInvalide, match="trop volumineux"):
        import_service.parse_sdf((bloc * 11).encode())


# ---------------------------------------------------------------------------
# Bout-en-bout via l'API
# ---------------------------------------------------------------------------


def test_api_import_zip_deguise_400(client):
    r = client.post(
        "/api/science/import/",
        files={"fichier": ("donnees.csv", b"PK\x03\x04" + b"\x00" * 64, "text/csv")},
        data={"type_donnees": "plante"},
    )
    assert r.status_code == 400
    assert "ZIP" in r.json()["detail"]


def test_api_import_zip_bomb_400(client):
    r = client.post(
        "/api/science/import/",
        files={"fichier": ("donnees.xlsx", _fausse_zip_bomb(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"type_donnees": "plante"},
    )
    assert r.status_code == 400
    assert "ZIP bomb" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Cohérence MIME / extension
# ---------------------------------------------------------------------------


def test_mime_image_pour_csv_rejete():
    with pytest.raises(import_service.ErreurImport, match="incompatible"):
        import_service.verifier_coherence_mime("image/png", "csv")


def test_mime_video_pour_xlsx_rejete():
    with pytest.raises(import_service.ErreurImport, match="incompatible"):
        import_service.verifier_coherence_mime("video/mp4", "xlsx")


def test_mime_octet_stream_accepte():
    # Déclaration vague : acceptée (la vraie sécurité = signature).
    import_service.verifier_coherence_mime("application/octet-stream", "csv")
    import_service.verifier_coherence_mime(None, "json")
    import_service.verifier_coherence_mime("text/csv", "csv")


def test_api_import_mime_contradictoire_400(client):
    r = client.post(
        "/api/science/import/",
        files={"fichier": ("donnees.csv", b"nom_scientifique\nMoringa\n", "image/png")},
        data={"type_donnees": "plante"},
    )
    assert r.status_code == 400
    assert "incompatible" in r.json()["detail"]


def test_api_import_mime_vague_200(client):
    r = client.post(
        "/api/science/import/",
        files={"fichier": ("donnees.csv", b"nom_scientifique\nMoringa oleifera\n", "application/octet-stream")},
        data={"type_donnees": "plante"},
    )
    assert r.status_code == 200, r.text
