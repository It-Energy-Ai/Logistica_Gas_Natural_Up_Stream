"""Test dell'algoritmo UTI (ACER, Annex IV al TRUM).

La proprietà che conta davvero è la **simmetria**: compratore e venditore,
partendo ciascuno dal proprio record, devono ottenere lo stesso identificativo
senza scambiarsi nulla. Se cade quella, i due lati della stessa operazione non
si appaiano in ARIS.
"""

from __future__ import annotations

import itertools
import random
import re
import string

import pytest

from app import acer_xml, uti

# pattern XSD dell'UTI (uniqueTransactionIdentifierType)
PATTERN_UTI = re.compile(r"[A-Za-z0-9_-]([A-Za-z0-9_ -]*[A-Za-z0-9_-])?")


def _contratto(**extra):
    """Record visto dal COMPRATORE (acer_code compra da counterparty)."""
    base = {
        "report_kind": "gas_standard",
        "acer_code": "A0045821W.IT",
        "counterparty": "B0011111X.IT",
        "counterparty_scheme": "ace",
        "side": "buy",
        "contract_type": "FW",
        "energy_commodity": "NG",
        "settlement_method": "P",
        "transaction_at": "2026-08-01T10:15:00Z",
        "price_eur_mwh": "33.50",
        "price_currency": "EUR",
        "quantity_mwh": "2400",
        "quantity_unit": "MWh",
        "delivery_point": "21YIT-SNAMRG--PX",
        "delivery_start_date": "2026-09-01",
        "delivery_end_date": "2026-09-30",
    }
    base.update(extra)
    return base


def _specchio(record):
    """Lo stesso contratto visto dalla CONTROPARTE: ruoli e lato invertiti."""
    opposto = {"buy": "sell", "sell": "buy"}[record["side"]]
    return {
        **record,
        "acer_code": record["counterparty"],
        "counterparty": record["acer_code"],
        "side": opposto,
    }


# --- struttura -------------------------------------------------------------


def test_forma_dell_uti():
    codice = uti.genera_uti(_contratto())
    assert len(codice) == 45
    assert codice[-3:] == "001"  # progressivo a due cifre in coda
    assert PATTERN_UTI.fullmatch(codice)


def test_uti_e_deterministico():
    assert uti.genera_uti(_contratto()) == uti.genera_uti(_contratto())


# --- la proprietà centrale: le due parti ottengono lo stesso UTI ------------


def test_compratore_e_venditore_generano_lo_stesso_uti():
    compratore = _contratto()
    venditore = _specchio(compratore)
    assert compratore["acer_code"] != venditore["acer_code"]
    assert uti.genera_uti(compratore) == uti.genera_uti(venditore)


def test_simmetria_su_molte_combinazioni():
    """La simmetria non deve dipendere dai valori dei campi."""
    tipi = ["FW", "FU", "SP", "OT"]
    valute = ["EUR", "GBP", "CHF"]
    lati = ["buy", "sell"]
    for tipo, valuta, lato in itertools.product(tipi, valute, lati):
        record = _contratto(contract_type=tipo, price_currency=valuta, side=lato)
        assert uti.genera_uti(record) == uti.genera_uti(_specchio(record)), (tipo, valuta, lato)


def test_simmetria_anche_senza_prezzo_e_quantita():
    """Contratti indicizzati: l'Annex IV prevede l'omissione dei campi assenti."""
    record = _contratto(price_eur_mwh="", quantity_mwh="", quantity_unit="")
    assert uti.genera_uti(record) == uti.genera_uti(_specchio(record))


# --- sensibilità: dati diversi, UTI diverso --------------------------------


@pytest.mark.parametrize(
    "campo,valore",
    [
        ("contract_type", "FU"),
        ("energy_commodity", "EL"),
        ("settlement_method", "C"),
        ("price_eur_mwh", "33.51"),
        ("price_currency", "GBP"),
        ("quantity_mwh", "2401"),
        ("quantity_unit", "KWh"),
        ("delivery_point", "21YIT-SNAMRG--PY"),
        ("delivery_start_date", "2026-09-02"),
        ("delivery_end_date", "2026-10-31"),
        ("transaction_at", "2026-08-02T10:15:00Z"),
        ("counterparty", "C0022222Y.IT"),
    ],
)
def test_ogni_termine_economico_cambia_l_uti(campo, valore):
    assert uti.genera_uti(_contratto()) != uti.genera_uti(_contratto(**{campo: valore}))


def test_una_cifra_di_differenza_cambia_tutto_l_uti():
    """Effetto valanga: nessun prefisso in comune fra due UTI vicini."""
    a = uti.genera_uti(_contratto(price_eur_mwh="33.50"))
    b = uti.genera_uti(_contratto(price_eur_mwh="33.51"))
    comuni = sum(1 for x, y in zip(a[:42], b[:42]) if x == y)
    assert comuni < 10, f"troppi caratteri in comune ({comuni}): hash debole"


def test_l_ora_non_conta_solo_la_data_del_trade():
    """L'Annex IV usa la data dell'operazione, non l'orario."""
    mattina = _contratto(transaction_at="2026-08-01T08:00:00Z")
    sera = _contratto(transaction_at="2026-08-01T20:45:00Z")
    assert uti.genera_uti(mattina) == uti.genera_uti(sera)


# --- progressivo -----------------------------------------------------------


def test_progressivo_distingue_operazioni_identiche():
    record = _contratto()
    codici = {uti.genera_uti(record, progressivo=n) for n in range(1, 10)}
    assert len(codici) == 9
    assert all(len(c) == 45 for c in codici)


def test_progressivo_e_simmetrico():
    record = _contratto()
    for n in (1, 7, 42, 99, 500):
        assert uti.genera_uti(record, n) == uti.genera_uti(_specchio(record), n)


@pytest.mark.parametrize("valore", [0, -1, 1000, 10000, "1", 1.5, None, True])
def test_progressivo_fuori_intervallo_rifiutato(valore):
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(), valore)


def test_progressivo_visibile_in_coda():
    for n in (1, 9, 10, 99, 999):
        assert uti.genera_uti(_contratto(), n).endswith(f"{n:03d}")


# --- dati insufficienti o incoerenti ---------------------------------------


@pytest.mark.parametrize("campo", ["contract_type", "energy_commodity"])
def test_campi_indispensabili(campo):
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(**{campo: ""}))


def test_senza_data_operazione_non_si_genera():
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(transaction_at="", contract_date=""))


def test_serve_il_lato_dell_operazione():
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(side=""))
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(side="cross"))


def test_serve_il_codice_acer_di_entrambe_le_parti():
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(counterparty=""))
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(acer_code=""))


def test_controparte_identificata_con_schema_diverso_da_ace():
    """Con LEI o EIC l'algoritmo ACER non è applicabile: meglio dirlo."""
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(counterparty_scheme="lei", counterparty="1" * 20))


# --- conformità allo schema e robustezza -----------------------------------


def test_uti_sempre_conforme_al_pattern_xsd_su_input_casuali():
    """Nessun input deve produrre un UTI che l'XSD rifiuterebbe."""
    rnd = random.Random(20260802)
    alfabeto = string.ascii_letters + string.digits + " .-_/&%$£€@#'\"\\<>|"
    visti = set()
    for _ in range(400):
        casuale = "".join(rnd.choice(alfabeto) for _ in range(rnd.randint(1, 24)))
        record = _contratto(
            contract_type=casuale[:8] or "FW",
            delivery_point=casuale,
            price_eur_mwh=casuale,
            quantity_mwh=casuale,
        )
        codice = uti.genera_uti(record, rnd.randint(1, 999))
        assert len(codice) == 45
        assert PATTERN_UTI.fullmatch(codice), f"UTI non conforme: {codice!r}"
        visti.add(codice)
    assert len(visti) > 390, "troppe collisioni fra UTI diversi"


def test_uti_accettato_dalla_validazione_del_tracciato():
    """L'UTI generato deve superare i controlli usati per l'export Table 1."""
    record = _contratto()
    record["transaction_id"] = uti.genera_uti(record)
    record.update(
        {
            "contract_id": "MGP-GAS-2026-0042",
            "trading_capacity": "P",
            "action": "new",
            "marketplace_id": "XGME",
            "marketplace_scheme": "mic",
        }
    )
    campi = {e["field"] for e in acer_xml.errori_dati(record)}
    assert "transaction_id" not in campi


def test_uti_finisce_davvero_nel_file_xml():
    from lxml import etree

    record = _contratto()
    codice = uti.genera_uti(record)
    record.update(
        {
            "transaction_id": codice,
            "contract_id": "MGP-GAS-2026-0042",
            "trading_capacity": "P",
            "action": "new",
            "marketplace_id": "XGME",
            "marketplace_scheme": "mic",
        }
    )
    documento = acer_xml.genera_xml(record)
    grezzo = documento.xml.encode() if isinstance(documento.xml, str) else documento.xml
    radice = etree.fromstring(grezzo)
    trovato = radice.find(".//{*}uniqueTransactionIdentifier/{*}uniqueTransactionIdentifier")
    assert trovato is not None and trovato.text == codice


def test_spazi_accidentali_non_cambiano_l_uti():
    """Un campo copiato con spazi ai bordi non deve rompere l'accordo."""
    pulito = _contratto()
    sporco = _contratto(contract_type=" FW ", price_currency=" EUR ", delivery_point=" 21YIT-SNAMRG--PX ")
    assert uti.genera_uti(pulito) == uti.genera_uti(sporco)


def test_ordine_dei_campi_e_quello_dell_annex_iv():
    """L'ordine è parte dell'accordo: se cambia, gli UTI non coincidono più."""
    assert uti.CAMPI_UTI == (
        "buyer_acer",
        "seller_acer",
        "contract_type",
        "energy_commodity",
        "settlement_method",
        "trade_date",
        "price",
        "price_currency",
        "quantity",
        "quantity_unit",
        "delivery_point",
        "delivery_start_date",
        "delivery_end_date",
    )


def test_concatenazione_segue_lordine_ufficiale():
    """Codici, termini economici e date, senza separatori."""
    record = _contratto()
    concatenata = uti.stringa_uti(record)
    assert concatenata.startswith("A0045821W.ITB0011111X.ITFWNGP2026-08-01")
    assert "33.50EUR2400MWh" in concatenata
    assert concatenata.endswith("21YIT-SNAMRG--PX2026-09-012026-09-30")


def test_conformita_ai_vettori_ufficiali_acer():
    """Confronto con i valori calcolati dal generatore ufficiale ACER v2.3.

    Per ogni caso il file contiene la stringa concatenata e l'identificativo
    prodotto dal foglio: se la nostra impronta o il formato divergessero anche
    di un solo carattere, il test cadrebbe.
    """
    import json
    from pathlib import Path as _P

    dati = json.loads((_P(__file__).parent / "dati" / "uti_acer.json").read_text())
    vettori = dati["vettori"]
    assert len(vettori) >= 150, "vettori ufficiali mancanti"
    for v in vettori:
        assert uti.impronta_acer(v["concatenato"]) == v["hash"], v["concatenato"][:60]
        assert uti.da_concatenazione(v["concatenato"], v["progressivo"]) == v["atteso"]


def test_i_vettori_coprono_entrambi_i_tracciati():
    import json
    from pathlib import Path as _P

    dati = json.loads((_P(__file__).parent / "dati" / "uti_acer.json").read_text())
    assert {v["tipo"] for v in dati["vettori"]} == {"uti_table1", "contract_id_table2"}


def test_impronta_sempre_alfanumerica():
    """base64 userebbe + / =: il generatore ACER li sostituisce con A B C."""
    for testo in ("prova", "trade-2026", "x" * 500, "àèìòù", ""):
        impronta = uti.impronta_acer(testo)
        assert not set("+/=-") & set(impronta), impronta
        assert impronta.isalnum()
        assert len(impronta) == 44


def test_contract_id_table2_ignora_prezzo_e_quantita():
    record = _contratto()
    diverso = _contratto(price_eur_mwh="9999", quantity_mwh="1", quantity_unit="KWh")
    assert uti.genera_contract_id(record) == uti.genera_contract_id(diverso)
    assert uti.genera_uti(record) != uti.genera_uti(diverso)


def test_contract_id_e_simmetrico_fra_le_parti():
    record = _contratto()
    assert uti.genera_contract_id(record) == uti.genera_contract_id(_specchio(record))
    assert len(uti.genera_contract_id(record)) == 45


def test_codici_acer_lunghi_dodici_e_distinti():
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(acer_code="A004.IT"))
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(counterparty="B001.IT"))
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(counterparty="A0045821W.IT"))


def test_serve_il_metodo_di_regolamento():
    with pytest.raises(uti.UtiError):
        uti.genera_uti(_contratto(settlement_method=""))


def test_riempimento_se_limpronta_fosse_piu_corta():
    """Il foglio ACER riempie con 'E' fino a 42 caratteri."""
    assert uti.da_concatenazione("x", 1).endswith("001")
    assert len(uti.da_concatenazione("x", 1)) == 45
