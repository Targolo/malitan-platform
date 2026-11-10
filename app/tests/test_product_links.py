"""
Unit tests for product design links (Tinkercad designs and 3MF files).
"""
import pytest


def _create(test_app, db, **kwargs):
    return test_app.catalogue.create_product(
        db=db,
        nom="Kit Adlakat Nerot",
        product_type=test_app.models.ProductType.PARTAGE_BENEFICES,
        prix_vente_base=120,
        prix_revient_david=15,
        prix_revient_lea=5,
        poids_grammes=150,
        temps_impression_minutes=210,
        user_id=None,
        **kwargs,
    )


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.tinkercad.com/things/6rG7J4zX0kG-kit-adlakat-nerot",
        "https://www.tinkercad.com/things/6rG7J4zX0kG-kit-adlakat-nerot/edit?returnTo=%2Fdashboard",
        "tinkercad.com/things/6rG7J4zX0kG",
        "  6rG7J4zX0kG  ",
    ],
)
def test_tinkercad_link_is_normalized_to_design_id(test_app, raw):
    assert (
        test_app.catalogue.normalize_tinkercad_links(raw)
        == "https://www.tinkercad.com/things/6rG7J4zX0kG"
    )


def test_tinkercad_links_skip_blank_lines_and_duplicates(test_app):
    raw = "6rG7J4zX0kG\n\n https://www.tinkercad.com/things/6ArtISJidL7-hadlakat-nerot \n6rG7J4zX0kG\n"
    assert test_app.catalogue.normalize_tinkercad_links(raw) == (
        "https://www.tinkercad.com/things/6rG7J4zX0kG\n"
        "https://www.tinkercad.com/things/6ArtISJidL7"
    )


@pytest.mark.parametrize("raw", ["", "   \n  ", None])
def test_empty_links_are_stored_as_none(test_app, raw):
    assert test_app.catalogue.normalize_tinkercad_links(raw) is None
    assert test_app.catalogue.normalize_3mf_files(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["https://makerworld.com/en/models/423136", "https://www.tinkercad.com/dashboard", "pas un lien"],
)
def test_non_tinkercad_link_is_rejected(test_app, raw):
    with pytest.raises(ValueError, match="Tinkercad"):
        test_app.catalogue.normalize_tinkercad_links(raw)


def test_3mf_files_keep_names_add_extension_and_skip_duplicates(test_app):
    raw = "Kit Adlakat Nerot.3mf\nTrophe\n\nKit Adlakat Nerot.3mf\n Verre kidoush metal.3MF "
    assert test_app.catalogue.normalize_3mf_files(raw) == (
        "Kit Adlakat Nerot.3mf\nTrophe.3mf\nVerre kidoush metal.3MF"
    )


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\targo\OneDrive\3D Objects\_Malitan\Trophe.3mf",
        "_Malitan/Trophe.3mf",
        "Pot crayons - Naor vert.stl",
    ],
)
def test_3mf_path_or_other_format_is_rejected(test_app, raw):
    with pytest.raises(ValueError):
        test_app.catalogue.normalize_3mf_files(raw)


def test_create_product_stores_normalized_links(db_session, test_app):
    product = _create(
        test_app,
        db_session,
        liens_tinkercad="https://www.tinkercad.com/things/6rG7J4zX0kG-kit-adlakat-nerot",
        fichiers_3mf="Kit Adlakat Nerot.3mf\nPlateau adlakat nerot",
    )

    assert product.liens_tinkercad == "https://www.tinkercad.com/things/6rG7J4zX0kG"
    assert product.liens_tinkercad_list == ["https://www.tinkercad.com/things/6rG7J4zX0kG"]
    assert product.fichiers_3mf_list == ["Kit Adlakat Nerot.3mf", "Plateau adlakat nerot.3mf"]


def test_product_without_links_has_empty_lists(db_session, test_app):
    product = _create(test_app, db_session)

    assert product.liens_tinkercad is None
    assert product.liens_tinkercad_list == []
    assert product.fichiers_3mf_list == []


def test_update_product_keeps_links_when_not_given_and_clears_on_empty(db_session, test_app):
    catalogue = test_app.catalogue
    product = _create(test_app, db_session, liens_tinkercad="6rG7J4zX0kG", fichiers_3mf="Trophe")

    catalogue.update_product(db_session, product.id, observations="note")
    assert product.liens_tinkercad == "https://www.tinkercad.com/things/6rG7J4zX0kG"
    assert product.fichiers_3mf == "Trophe.3mf"

    catalogue.update_product(db_session, product.id, liens_tinkercad="", fichiers_3mf="")
    assert product.liens_tinkercad is None
    assert product.fichiers_3mf is None


def test_update_product_rejects_invalid_link_without_saving(db_session, test_app):
    catalogue = test_app.catalogue
    product = _create(test_app, db_session, fichiers_3mf="Trophe.3mf")

    with pytest.raises(ValueError):
        catalogue.update_product(db_session, product.id, nom="Autre nom", fichiers_3mf="dossier/Trophe.3mf")

    db_session.rollback()
    db_session.refresh(product)
    assert product.nom == "Kit Adlakat Nerot"
    assert product.fichiers_3mf == "Trophe.3mf"
