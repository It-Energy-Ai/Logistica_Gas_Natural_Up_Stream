"""Verifiche del modulo EDIG@S 6.1: nomina NOMINT e risposta NOMRES.

I casi di riferimento non sono inventati: sono i messaggi di esempio
distribuiti da EASEE-gas dentro il pacchetto ufficiale, conservati in
``tests/dati/edigas/``.  La prova più stringente è la riproduzione della
struttura di quegli esempi partendo dai dati applicativi.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from lxml import etree

from app import edigas


ESEMPI = Path(__file__).parent / "dati" / "edigas"

BASE = {
    "identificativo": "NOMINT-20260803-001",
    "versione": 1,
    "tipo_documento": "01G",
    "tipo_nomina": "A02",
    "emittente_eic": "21X000000001234A",
    "emittente_ruolo": "ZSH",
    "destinatario_eic": "21X00000000567KB",
    "destinatario_ruolo": "ZSO",
    "giorno_gas": "2026-08-03",
    "conto_interno": "SHIPPER-01",
    "punto_eic": "21Z0000000001234",
    "unita": "KW1",
    "creato_il": "2026-08-02T10:00:00Z",
    "controparti": [{"conto": "CTP-ALFA", "direzione": "Z02", "quantita_giornaliera": "12500"}],
}


def nomina(**modifiche):
    dati = {**BASE, **modifiche}
    for chiave, valore in list(dati.items()):
        if valore is None:
            del dati[chiave]
    return edigas.genera_nomina(dati)


def elementi(xml: str, nome: str) -> list[str]:
    radice = etree.fromstring(xml.encode("utf-8"))
    return [(e.text or "").strip() for e in radice.iter(f"{{{edigas.SCHEMI['nomina']['namespace']}}}{nome}")]


# --------------------------------------------------------------- giorno gas


@pytest.mark.parametrize(
    "giorno,ore,intervallo",
    [
        ("2026-01-15", 24, "2026-01-15T05:00Z/2026-01-16T05:00Z"),  # ora solare
        ("2026-08-03", 24, "2026-08-03T04:00Z/2026-08-04T04:00Z"),  # ora legale
        ("2026-03-28", 23, "2026-03-28T05:00Z/2026-03-29T04:00Z"),  # passaggio a ora legale
        ("2026-10-24", 25, "2026-10-24T04:00Z/2026-10-25T05:00Z"),  # ritorno all'ora solare
    ],
)
def test_giorno_gas_segue_il_fuso_italiano(giorno, ore, intervallo):
    """Il giorno gas va dalle 06:00 alle 06:00 locali, non da un offset fisso.

    Il cambio dell'ora avviene alle 02:00 locali, cioè dentro il giorno gas
    iniziato il pomeriggio prima: sono quelli i giorni da 23 e 25 ore.
    """

    g = date.fromisoformat(giorno)
    inizio, fine = edigas.confini_giorno_gas(g)
    assert len(edigas.ore_giorno_gas(g)) == ore
    assert edigas._intervallo(inizio, fine) == intervallo


def test_il_giorno_gas_non_dipende_dal_fuso_della_macchina():
    """Un server in UTC o a Tokyo deve produrre gli stessi intervalli."""

    import subprocess
    import sys

    codice = (
        "from datetime import date; from app import edigas; "
        "i, f = edigas.confini_giorno_gas(date(2026, 10, 24)); "
        "print(edigas._intervallo(i, f), len(edigas.ore_giorno_gas(date(2026, 10, 24))))"
    )
    radice = Path(__file__).resolve().parent.parent
    esiti = set()
    for fuso in ("UTC", "America/New_York", "Asia/Tokyo"):
        esito = subprocess.run(
            [sys.executable, "-c", codice],
            cwd=radice,
            env={"PATH": os.environ.get("PATH", ""), "TZ": fuso},
            capture_output=True,
            text=True,
            check=True,
        )
        esiti.add(esito.stdout.strip())
    assert esiti == {"2026-10-24T04:00Z/2026-10-25T05:00Z 25"}


def test_le_ore_del_giorno_gas_sono_contigue_e_coprono_tutto():
    slot = edigas.ore_giorno_gas(date(2026, 10, 24))
    inizio, fine = edigas.confini_giorno_gas(date(2026, 10, 24))
    assert slot[0][0] == inizio and slot[-1][1] == fine
    assert all(a[1] == b[0] for a, b in zip(slot, slot[1:]))


def test_profilo_orario_rifiutato_se_non_combacia_col_giorno_gas():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(
            giorno_gas="2026-10-24",
            controparti=[{"conto": "CTP", "direzione": "Z02", "profilo_orario": [10] * 24}],
        )
    assert "25 ore" in exc.value.errors[0]["message"]


# -------------------------------------------------------------- generazione


def test_nomina_valida_contro_lo_schema_ufficiale():
    doc = nomina()
    assert doc.radice == "Nomination_Document"
    assert doc.versione_edigas == "6.1"
    assert doc.periodi == 1
    assert doc.xml.startswith("<?xml")
    assert len(doc.xml_sha256) == 64


def test_gli_istanti_non_hanno_secondi_e_sono_in_utc():
    """Il tracciato accetta solo YYYY-MM-DDThh:mmZ negli intervalli."""

    doc = nomina()
    for valore in elementi(doc.xml, "timeInterval") + elementi(doc.xml, "validityPeriod"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", valore)


def test_profilo_orario_genera_un_periodo_per_ora():
    doc = nomina(controparti=[{"conto": "CTP", "direzione": "Z03", "profilo_orario": list(range(24))}])
    assert doc.periodi == 24
    assert elementi(doc.xml, "quantity.amount") == [str(n) for n in range(24)]


def test_periodi_espliciti_dentro_il_giorno_gas():
    doc = nomina(
        controparti=[
            {
                "conto": "CTP",
                "periodi": [
                    {"inizio": "2026-08-03T04:00Z", "fine": "2026-08-03T10:00Z", "direzione": "Z02", "quantita": "50"},
                    {"inizio": "2026-08-03T10:00Z", "fine": "2026-08-04T04:00Z", "direzione": "Z02", "quantita": "70"},
                ],
            }
        ]
    )
    assert doc.periodi == 2
    assert doc.avvisi == ()


def test_il_documento_e_deterministico():
    assert nomina().xml_sha256 == nomina().xml_sha256


def test_rinomina_incrementa_solo_la_versione():
    """Una rinomina conserva identificativo e tipo, cambia la versione."""

    prima, seconda = nomina(versione=1), nomina(versione=2)
    assert elementi(prima.xml, "identification")[0] == elementi(seconda.xml, "identification")[0]
    assert elementi(prima.xml, "version") == ["1"] and elementi(seconda.xml, "version") == ["2"]
    assert prima.xml_sha256 != seconda.xml_sha256


# ------------------------------------------------- regole della decision table


@pytest.mark.parametrize(
    "tipo,emittente,destinatario,tipo_nomina,con_controparti",
    [
        ("01G", "ZSH", "ZSO", "A02", True),
        ("01G", "ZSH", "ZSO", "A01", True),
        ("02G", "ZSH", "ZUK", "A02", True),
        ("03G", "ZUM", "ZUK", None, True),
        ("04G", "ZSH", "ZSO", None, False),
    ],
)
def test_ogni_tipo_di_documento_produce_la_struttura_prevista(
    tipo, emittente, destinatario, tipo_nomina, con_controparti
):
    """01G/02G annidano le controparti in NominationType, 03G no, 04G non le ha."""

    extra = (
        {"controparti": [{"conto": "CTP", "direzione": "Z02", "quantita_giornaliera": "100"}]}
        if con_controparti
        else {"controparti": None, "direzione": "Z02", "quantita_giornaliera": "100"}
    )
    doc = nomina(
        tipo_documento=tipo,
        emittente_ruolo=emittente,
        destinatario_ruolo=destinatario,
        tipo_nomina=tipo_nomina,
        **extra,
    )
    ha_tipo_nomina = bool(elementi(doc.xml, "nominationCode"))
    assert ha_tipo_nomina is bool(tipo_nomina)
    assert bool(elementi(doc.xml, "externalAccount")) is con_controparti


@pytest.mark.parametrize(
    "modifiche,atteso",
    [
        ({"emittente_ruolo": "ZUM"}, "emittente dev'essere ZSH"),
        ({"destinatario_ruolo": "ZUK"}, "destinatario dev'essere ZSO"),
        ({"tipo_documento": "02G", "destinatario_ruolo": "ZUK", "tipo_nomina": "A01"}, "ammesso è A02"),
        ({"tipo_nomina": None}, "richiede il tipo di nomina"),
        ({"tipo_documento": "05G"}, "non gestito"),
    ],
)
def test_combinazioni_vietate_dalla_decision_table(modifiche, atteso):
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(**modifiche)
    assert any(atteso in e["message"] for e in exc.value.errors), exc.value.errors


def test_il_tipo_nomina_non_e_ammesso_dove_non_previsto():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(tipo_documento="03G", emittente_ruolo="ZUM", destinatario_ruolo="ZUK", tipo_nomina="A02")
    assert any("non prevede il tipo di nomina" in e["message"] for e in exc.value.errors)


def test_gli_errori_della_non_matching_non_parlano_di_controparti():
    """Con 04G non esiste nessuna controparte: dire "Controparte 1" confonderebbe."""

    with pytest.raises(edigas.EdigasError) as exc:
        nomina(tipo_documento="04G", tipo_nomina=None, controparti=None)
    messaggi = [e["message"] for e in exc.value.errors]
    campi = [e["field"] for e in exc.value.errors]
    assert any("Punto di connessione" in m for m in messaggi), messaggi
    assert not any("Controparte" in m for m in messaggi), messaggi
    assert not any(c.startswith("controparti") for c in campi), campi


def test_la_nomina_non_matching_non_accetta_controparti():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(tipo_documento="04G", tipo_nomina=None)
    assert any("non prevede controparti" in e["message"] for e in exc.value.errors)


# ----------------------------------------------------------- campi e vincoli


@pytest.mark.parametrize(
    "campo,valore,atteso",
    [
        ("identificativo", "", "obbligatorio"),
        ("identificativo", "X" * 36, "massimo 35 caratteri"),
        ("versione", 0, "da 1 a 999"),
        ("versione", 1000, "da 1 a 999"),
        ("emittente_eic", "NON-UN-EIC", "non è un codice EIC valido"),
        ("unita", "MTQ", "non ammesso dal tracciato"),
        ("giorno_gas", "2026-13-01", "data inesistente"),
        ("giorno_gas", "20260803", "attesa una data AAAA-MM-GG"),
        ("giorno_gas", "9999-12-31", "anno fuori dall'intervallo"),
        ("versione", "\u00b2", "da 1 a 999"),          # isdigit() è vero, int() no
        ("versione", "1" * 4301, "da 1 a 999"),      # oltre il limite di cifre di int()
        ("identificativo", "NOM\x00INT", "caratteri non ammessi"),
        ("identificativo", {"a": 1}, "valore semplice"),
        ("conto_interno", ["A", "B"], "valore semplice"),
        ("punto_eic", "21X000000001234A", "dev'essere Y o Z"),  # EIC di parte usato come punto
        ("conto_interno", "", "obbligatorio"),
        ("punto_eic", "", "obbligatorio"),
    ],
)
def test_campi_non_conformi_sono_respinti(campo, valore, atteso):
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(**{campo: valore})
    assert any(atteso in e["message"] for e in exc.value.errors), exc.value.errors


def test_emittente_e_destinatario_devono_differire():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(destinatario_eic=BASE["emittente_eic"])
    assert any("stesso EIC" in e["message"] for e in exc.value.errors)


@pytest.mark.parametrize("quantita", ["-1", "abc", "", "1.0000000000001"])
def test_quantita_non_valide_sono_respinte(quantita):
    with pytest.raises(edigas.EdigasError):
        nomina(controparti=[{"conto": "CTP", "direzione": "Z02", "quantita_giornaliera": quantita}])


def test_direzione_fuori_lista_respinta():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(controparti=[{"conto": "CTP", "direzione": "Z99", "quantita_giornaliera": "10"}])
    assert "Z02, Z03" in exc.value.errors[0]["message"]


def test_periodo_fuori_dal_giorno_gas_respinto():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(
            controparti=[
                {
                    "conto": "CTP",
                    "periodi": [
                        {"inizio": "2026-08-02T04:00Z", "fine": "2026-08-03T10:00Z", "direzione": "Z02", "quantita": "1"}
                    ],
                }
            ]
        )
    assert any("fuori dal giorno gas" in e["message"] for e in exc.value.errors)


def test_periodi_sovrapposti_respinti():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(
            controparti=[
                {
                    "conto": "CTP",
                    "periodi": [
                        {"inizio": "2026-08-03T04:00Z", "fine": "2026-08-03T12:00Z", "direzione": "Z02", "quantita": "1"},
                        {"inizio": "2026-08-03T10:00Z", "fine": "2026-08-03T14:00Z", "direzione": "Z02", "quantita": "2"},
                    ],
                }
            ]
        )
    assert any("sovrapposti" in e["message"] for e in exc.value.errors)


def test_una_sola_forma_di_quantita_per_controparte():
    with pytest.raises(edigas.EdigasError) as exc:
        nomina(
            controparti=[
                {"conto": "CTP", "direzione": "Z02", "quantita_giornaliera": "10", "profilo_orario": [1] * 24}
            ]
        )
    assert any("una sola forma" in e["message"] for e in exc.value.errors)


def test_copertura_parziale_avvisa_senza_bloccare():
    """Una nomina di poche ore è legittima: la lacuna è un avviso, non un errore."""

    doc = nomina(
        controparti=[
            {
                "conto": "CTP",
                "periodi": [
                    {"inizio": "2026-08-03T08:00Z", "fine": "2026-08-03T12:00Z", "direzione": "Z02", "quantita": "5"}
                ],
            }
        ]
    )
    assert doc.periodi == 1
    assert any("non è coperto per intero" in a for a in doc.avvisi)


# ------------------------------------------- vincoli derivati dagli XSD


@pytest.mark.parametrize(
    "tipo,attesi",
    [
        ("DocumentCodeType", {"01G", "02G", "03G", "04G"}),
        ("RoleCodeType", {"ZSH", "ZUM", "ZSO", "ZUK"}),
        ("GasDirectionCodeType", {"Z02", "Z03"}),
        ("UnitOfMeasureCodeType", {"KW1", "KW2", "A15"}),
        ("NominationCodeType", {"A01", "A02"}),
    ],
)
def test_i_codici_ammessi_vengono_letti_dagli_xsd(tipo, attesi):
    """La catena union Standard+Local dev'essere risolta, non ricopiata a mano."""

    assert attesi.issubset(set(edigas.codici_ammessi(tipo)))


def test_le_etichette_coprono_i_codici_dello_schema():
    assert set(edigas.UNITA) == set(edigas.codici_ammessi("UnitOfMeasureCodeType"))
    assert set(edigas.DIREZIONI) == set(edigas.codici_ammessi("GasDirectionCodeType"))
    assert set(edigas.TIPI_NOMINA) == set(edigas.codici_ammessi("NominationCodeType"))
    assert set(edigas.RUOLI) == set(edigas.codici_ammessi("RoleCodeType"))


def test_gli_schemi_si_compilano_anche_fuori_dal_repo(tmp_path):
    """Nell'eseguibile PyInstaller gli XSD finiscono altrove.

    Gli import fra schemi sono relativi (``../cclib-v6/…``): se la struttura
    delle cartelle non venisse preservata nel bundle, la validazione
    fallirebbe solo nel binario e non nei test.
    """

    import shutil

    copia = tmp_path / "app" / "schemas" / "edigas"
    shutil.copytree(edigas.SCHEMA_DIR, copia)
    for meta in edigas.SCHEMI.values():
        etree.XMLSchema(etree.parse(str(copia / meta["filename"])))


def test_lo_schema_bundle_ha_le_impronte_dichiarate():
    import hashlib

    for meta in edigas.SCHEMI.values():
        percorso = edigas.SCHEMA_DIR / meta["filename"]
        assert hashlib.sha256(percorso.read_bytes()).hexdigest() == meta["sha256"], meta["filename"]


# ------------------------------------------------ confronto con gli esempi ufficiali


def struttura(radice) -> list[str]:
    """Percorsi degli elementi, senza valori: confronta la forma del documento."""

    percorsi = []
    for el in radice.iter():
        if not isinstance(el.tag, str):
            continue
        catena = [etree.QName(a).localname for a in el.iterancestors()][::-1]
        percorsi.append("/".join([*catena, etree.QName(el).localname]))
    return percorsi


@pytest.mark.parametrize("nome", ["NOMINT-01G", "NOMINT-02G", "NOMINT-03G", "NOMINT-04G"])
def test_riproduce_la_struttura_degli_esempi_ufficiali(nome):
    """Genera dai dati applicativi e confronta la forma con l'esempio EASEE-gas."""

    ufficiale = etree.parse(str(ESEMPI / f"{nome}.xml")).getroot()
    dati = _dati_equivalenti(nome, ufficiale)
    prodotto = etree.fromstring(edigas.genera_nomina(dati).xml.encode("utf-8"))
    assert struttura(prodotto) == struttura(ufficiale)


def _dati_equivalenti(nome: str, ufficiale) -> dict:
    """Ricostruisce l'input applicativo che deve riprodurre l'esempio.

    Gli esempi usano identificativi fittizi ("IssuerEIC") che non superano il
    controllo EIC: si sostituiscono con codici reali, perché qui interessa la
    forma del documento, non i valori.
    """

    ns = {"e": edigas.SCHEMI["nomina"]["namespace"]}

    def testo(percorso, radice=ufficiale):
        el = radice.find(percorso, ns)
        return (el.text or "").strip() if el is not None else ""

    tipo = testo("e:documentCode")
    regole = edigas.TIPI_DOCUMENTO[tipo]
    inizio = testo("e:validityPeriod").partition("/")[0]
    giorno = _giorno_dal_periodo(inizio)

    controparti = []
    for esterno in ufficiale.iterfind(".//e:External_Account", ns):
        periodi = [
            {
                "inizio": testo("e:timeInterval", p).partition("/")[0],
                "fine": testo("e:timeInterval", p).partition("/")[2],
                "direzione": testo("e:direction.gasDirectionCode", p),
                "quantita": testo("e:quantity.amount", p),
            }
            for p in esterno.iterfind("e:Period", ns)
        ]
        controparti.append({"conto": testo("e:externalAccount", esterno), "periodi": periodi})

    dati = {
        "identificativo": testo("e:identification"),
        "versione": testo("e:version"),
        "tipo_documento": tipo,
        "emittente_eic": "21X000000001234A",
        "emittente_ruolo": regole["emittente"],
        "destinatario_eic": "21X00000000567KB",
        "destinatario_ruolo": regole["destinatario"],
        "giorno_gas": giorno,
        "conto_interno": testo(".//e:internalAccount"),
        "punto_eic": "21Z0000000001234",
        "unita": testo(".//e:measureUnit.unitOfMeasureCode"),
        "creato_il": testo("e:creationDateTime"),
    }
    codice_nomina = testo(".//e:nominationCode")
    if codice_nomina:
        dati["tipo_nomina"] = codice_nomina
    if controparti:
        dati["controparti"] = controparti
    else:
        punto = ufficiale.find(".//e:ConnectionPoint", ns)
        dati["periodi"] = [
            {
                "inizio": testo("e:timeInterval", p).partition("/")[0],
                "fine": testo("e:timeInterval", p).partition("/")[2],
                "direzione": testo("e:direction.gasDirectionCode", p),
                "quantita": testo("e:quantity.amount", p),
            }
            for p in punto.iterfind("e:Period", ns)
        ]
    return dati


def _giorno_dal_periodo(inizio: str) -> str:
    """Ricava il giorno gas dall'istante UTC di apertura del periodo."""

    momento = datetime.strptime(inizio, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    return momento.astimezone(edigas.FUSO_GAS).date().isoformat()


# ---------------------------------------------------- lettura della risposta


@pytest.mark.parametrize(
    "nome,righe_attese",
    [("NOMRES-01G", 12), ("NOMRES-02G", 6), ("NOMRES-03G", 3), ("NOMRES-04G", 2)],
)
def test_legge_le_risposte_ufficiali(nome, righe_attese):
    risposta = edigas.leggi_risposta((ESEMPI / f"{nome}.xml").read_bytes())
    assert risposta["valido_xsd"] is True
    assert len(risposta["righe"]) == righe_attese
    assert risposta["nomina_riferita"]["identificativo"]
    assert len(risposta["sha256"]) == 64


def test_la_risposta_non_matching_ha_le_serie_sul_punto():
    """Senza controparte le serie pendono da ConnectionPoint, non da External_Account."""

    risposta = edigas.leggi_risposta((ESEMPI / "NOMRES-04G.xml").read_bytes())
    assert risposta["righe"]
    assert all(r["controparte"] == "" for r in risposta["righe"])


def test_un_file_che_non_e_una_risposta_viene_rifiutato():
    with pytest.raises(edigas.EdigasError) as exc:
        edigas.leggi_risposta(nomina().xml)
    assert "non è una risposta di nomina" in str(exc.value)


def test_xml_malformato_rifiutato():
    with pytest.raises(edigas.EdigasError) as exc:
        edigas.leggi_risposta(b"<non-chiuso>")
    assert "non è XML valido" in str(exc.value)


def test_confronto_evidenzia_riduzione_del_trasportatore():
    """Se il trasportatore conferma meno di quanto nominato, si deve vedere."""

    doc = nomina(
        controparti=[{"conto": "CTP-ALFA", "direzione": "Z02", "quantita_giornaliera": "12500"}]
    )
    intervallo = elementi(doc.xml, "timeInterval")[0]
    risposta = {
        "righe": [
            {
                "controparte": "CTP-ALFA",
                "intervallo": intervallo,
                "direzione": "Z02",
                "quantita": "10000",
                "stati": [],
            }
        ]
    }
    scostamenti = edigas.confronta_con_nomina(doc.xml, risposta)
    assert len(scostamenti) == 1
    assert scostamenti[0]["esito"] == "ridotto"
    assert scostamenti[0]["nominato"] == "12500"


def test_confronto_segnala_i_periodi_senza_risposta():
    doc = nomina()
    scostamenti = edigas.confronta_con_nomina(doc.xml, {"righe": []})
    assert [s["esito"] for s in scostamenti] == ["senza risposta"]


def test_confronto_tace_quando_tutto_combacia():
    doc = nomina()
    risposta = {
        "righe": [
            {
                "controparte": "CTP-ALFA",
                "intervallo": elementi(doc.xml, "timeInterval")[0],
                "direzione": "Z02",
                "quantita": "12500.0",
                "stati": [],
            }
        ]
    }
    assert edigas.confronta_con_nomina(doc.xml, risposta) == []


def test_gli_istanti_senza_fuso_sono_letti_come_ora_italiana():
    """Il giorno gas è definito in locale: un istante nudo segue quella regola."""

    doc = nomina(
        controparti=[
            {
                "conto": "CTP",
                "periodi": [
                    # 08:00 italiane d'estate = 06:00Z
                    {"inizio": "2026-08-03T08:00", "fine": "2026-08-03T12:00", "direzione": "Z02", "quantita": "5"}
                ],
            }
        ]
    )
    assert elementi(doc.xml, "timeInterval") == ["2026-08-03T06:00Z/2026-08-03T10:00Z"]


def test_i_secondi_sono_troncati_prima_dei_controlli():
    """Il tracciato ha la precisione del minuto: un periodo che si annullerebbe
    dopo il troncamento va respinto, non scritto come intervallo a durata zero."""

    with pytest.raises(edigas.EdigasError) as exc:
        nomina(
            controparti=[
                {
                    "conto": "CTP",
                    "periodi": [
                        {
                            "inizio": "2026-08-03T05:00:10Z",
                            "fine": "2026-08-03T05:00:50Z",
                            "direzione": "Z02",
                            "quantita": "1",
                        }
                    ],
                }
            ]
        )
    assert any("la fine deve seguire l'inizio" in e["message"] for e in exc.value.errors)


def test_solo_la_serie_confermata_conta_nel_confronto():
    """14G, 15G e 18G non sono conferme: 15G e 18G hanno direzione speculare."""

    doc = nomina()
    intervallo = elementi(doc.xml, "timeInterval")[0]

    def riga(origine, quantita, direzione="Z02"):
        return {
            "controparte": "CTP-ALFA",
            "origine": origine,
            "intervallo": intervallo,
            "direzione": direzione,
            "quantita": quantita,
            "stati": [],
        }

    risposta = {
        "righe": [
            riga("14G", "12500"),
            riga("18G", "12500", "Z03"),
            riga("16G", "10000"),
        ]
    }
    scostamenti = edigas.confronta_con_nomina(doc.xml, risposta)
    assert [s["esito"] for s in scostamenti] == ["ridotto"]
    assert scostamenti[0]["quantita"] == "10000"


def test_confronto_delle_nomine_al_cliente_finale():
    """Con 04G i periodi pendono dal punto: vanno letti anche nel confronto."""

    doc = nomina(
        tipo_documento="04G",
        tipo_nomina=None,
        controparti=None,
        direzione="Z03",
        quantita_giornaliera="12500",
    )
    intervallo = elementi(doc.xml, "timeInterval")[0]
    risposta = {
        "righe": [
            {"controparte": "", "origine": "16G", "intervallo": intervallo, "direzione": "Z03", "quantita": "8000", "stati": []}
        ]
    }
    scostamenti = edigas.confronta_con_nomina(doc.xml, risposta)
    assert [s["esito"] for s in scostamenti] == ["ridotto"]
    assert scostamenti[0]["nominato"] == "12500"


@pytest.mark.parametrize("malformata", [{"righe": None}, {"righe": [{}]}, {"righe": ["x"]}, None, {}])
def test_il_confronto_non_esplode_su_risposte_malformate(malformata):
    edigas.confronta_con_nomina(nomina().xml, malformata)


def test_il_punto_puo_usare_il_codice_del_trasportatore():
    """Gli esempi ufficiali usano ZSO sul punto: dev'essere esprimibile."""

    doc = nomina(punto_eic="Example-ConnectionPoint", codifica_punto="ZSO")
    radice = etree.fromstring(doc.xml.encode("utf-8"))
    punto = radice.find(f".//{{{edigas.SCHEMI['nomina']['namespace']}}}ConnectionPoint")
    identificazione = punto.find(f"{{{edigas.SCHEMI['nomina']['namespace']}}}identification")
    assert identificazione.get("codingScheme") == "ZSO"
    assert identificazione.text == "Example-ConnectionPoint"


def test_lo_schema_alterato_viene_rifiutato(tmp_path, monkeypatch):
    """L'impronta restituita al client dev'essere verificata, non solo dichiarata."""

    import shutil

    copia = tmp_path / "edigas"
    shutil.copytree(edigas.SCHEMA_DIR, copia)
    bersaglio = copia / edigas.SCHEMI["nomina"]["filename"]
    bersaglio.write_bytes(bersaglio.read_bytes() + b"<!-- alterato -->")
    monkeypatch.setattr(edigas, "SCHEMA_DIR", copia)
    edigas._schema.cache_clear()
    try:
        with pytest.raises(edigas.EdigasError) as exc:
            edigas._schema("nomina")
        assert "Integrità dello schema" in str(exc.value)
    finally:
        edigas._schema.cache_clear()


def test_il_database_dei_fusi_e_una_dipendenza_dichiarata():
    """Windows non ha i fusi di sistema: senza tzdata l'app non parte proprio.

    Il calcolo del giorno gas avviene su ``Europe/Rome`` all'import del
    modulo, quindi la mancanza del database non degrada una funzione: impedisce
    l'avvio. Sulle macchine di sviluppo Linux e macOS il problema è invisibile,
    ed è per questo che va bloccato qui.
    """

    requisiti = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    riga = [r for r in requisiti.splitlines() if r.strip().startswith("tzdata")]
    assert riga, "tzdata deve restare fra le dipendenze"
    assert "win32" in riga[0], "va installato almeno su Windows"


def test_il_fuso_del_giorno_gas_e_caricabile():
    assert edigas.FUSO_GAS.key == "Europe/Rome"
    assert edigas.confini_giorno_gas(date(2026, 1, 15))[0].tzinfo is timezone.utc
