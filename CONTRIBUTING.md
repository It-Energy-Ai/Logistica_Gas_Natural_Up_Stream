# Contribuire a Vettore

Grazie dell'interesse! Poche regole, ma importanti.

## Prima di aprire una PR

1. **Setup**: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt` (serve anche Node 18+).
2. **Test verdi**:
   ```bash
   .venv/bin/pytest
   node tests/logic.test.cjs
   ```
3. **⚠️ La regola che frega tutti**: il frontend (`app/static/index.html`) è **generato** da `design/design.html` tramite `build_frontend.py` — non si modifica a mano. Se tocchi il design o il builder devi rigenerare e committare il risultato:
   ```bash
   python3 build_frontend.py
   ```
   La CI fallisce se il file generato non è allineato (`git diff --exit-code app/static/index.html`).
4. La logica va in `app/static/logic.js` (le deviazioni dal design sono documentate in testa al file), il backend in `app/main.py`/`app/db.py`.

## Convenzioni

- Interfaccia, commenti, commit e documentazione in **italiano**.
- Nessun dato di scena fuori dalla modalità demo: qualsiasi valore "finto" va dietro `demoOn` (ci sono regressioni anti-fuga nei test).
- Ogni chiave nuova persistita va aggiunta sia a `PERSIST` (logic.js) sia a `VALIDATORS` (main.py), con un validatore di forma.
- Aggiorna `CHANGELOG.md` per le modifiche visibili all'utente.

## Segnalazioni

Usa i [template delle issue](https://github.com/It-Energy-Ai/Logistica_Gas_Natural_Up_Stream/issues/new/choose); per le vulnerabilità segui la [security policy](SECURITY.md) (segnalazione privata, non issue pubblica).
