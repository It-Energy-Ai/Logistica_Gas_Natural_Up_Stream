"""Artefatti XML ACER: impronta degli schemi verificata prima di ogni uso."""

import hashlib
import shutil

import pytest

from app import acer_xml


def _svuota_cache():
    for funzione in (acer_xml._facet, acer_xml._compiled_schema, acer_xml._byte_schema):
        funzione.cache_clear()


def test_lo_schema_alterato_blocca_export_e_catalogo(tmp_path, monkeypatch):
    """Una sola verità per lo schema: alterarlo ferma sia l'export sia i facet.

    La compilazione dello XSD e l'indice dei tipi che alimenta le tendine
    devono leggere gli stessi byte verificati per impronta: un file sostituito
    su disco non può dare due verità diverse sullo stesso schema.
    """

    copia = tmp_path / "acer"
    shutil.copytree(acer_xml.SCHEMA_DIR, copia)
    bersaglio = copia / acer_xml.SCHEMAS["gas_standard"]["filename"]
    bersaglio.write_bytes(bersaglio.read_bytes() + b"<!-- alterato -->")
    monkeypatch.setattr(acer_xml, "SCHEMA_DIR", copia)
    _svuota_cache()
    try:
        with pytest.raises(acer_xml.AcerXmlError) as exc:
            acer_xml._byte_schema("gas_standard")
        assert "Integrità dello schema ACER" in str(exc.value)
        with pytest.raises(acer_xml.AcerXmlError):
            acer_xml._compiled_schema("gas_standard")
        with pytest.raises(acer_xml.AcerXmlError):
            acer_xml._facet("gas_standard", "contractIdType")
    finally:
        _svuota_cache()


def test_il_facet_e_la_compilazione_condividono_i_byte_verificati(monkeypatch):
    """_facet e _compiled_schema passano per lo stesso controllo d'impronta."""

    letto = []
    byte_schema_originale = acer_xml._byte_schema

    def _spia(kind):
        contenuto = acer_xml.SCHEMA_DIR / acer_xml.SCHEMAS[kind]["filename"]
        letto.append(kind)
        return contenuto.read_bytes()

    _svuota_cache()
    monkeypatch.setattr(acer_xml, "_byte_schema", _spia)
    try:
        acer_xml._facet("gas_standard", "contractIdType")
        acer_xml._compiled_schema("gas_standard")
        assert letto == ["gas_standard", "gas_standard"]
    finally:
        acer_xml._byte_schema = byte_schema_originale
        _svuota_cache()


def _record_t2(**extra):
    base = {
        "report_kind": "gas_nonstandard",
        "acer_code": "A0045821W.IT",
        "counterparty": "B0011111X.IT",
        "counterparty_scheme": "ace",
        "quantity_mwh": "500",
        "quantity_unit": "MWh",
        "price_eur_mwh": "33.50",
        "price_currency": "EUR",
        "action": "new",
        "side": "buy",
        "trading_capacity": "P",
        "contract_id": "PSV-2026-0142",
        "contract_date": "2026-07-01",
        "contract_type": "FW",
        "energy_commodity": "NG",
        "delivery_point": "21YIT-SNAMRG--PX",
        "delivery_start_date": "2026-08-01",
        "delivery_end_date": "2026-08-31",
        "settlement_method": "P",
    }
    base.update(extra)
    return base


def test_verifica_xml_archiviato_usa_un_parser_sicuro():
    """L'XML archiviato si riverifica senza DTD, entità o rete."""

    documento = acer_xml.genera_xml(_record_t2())
    acer_xml.verifica_xml_archiviato(
        "gas_nonstandard", documento.xml, documento.xml_sha256
    )
    with pytest.raises(acer_xml.AcerXmlError) as exc:
        acer_xml.verifica_xml_archiviato(
            "gas_nonstandard", documento.xml + "<!-- x -->", documento.xml_sha256
        )
    assert "non corrisponde" in str(exc.value)