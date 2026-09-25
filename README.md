# CoC CWL Tracker — Fase 1: Collector

Questo è il primo pezzo del progetto: uno script che interroga le API
ufficiali di Clash of Clans e salva su un database SQLite locale i dati
delle guerre (regular e CWL) del tuo clan — attacchi, stelle, distruzione,
Town Hall di attaccante e difensore. È la base su cui poi costruiremo il
motore di punteggio e la dashboard.

## Cosa fa esattamente

A ogni esecuzione (`python -m collector.run`):

1. Legge le info generali del clan e aggiorna l'anagrafica giocatori.
2. Se il clan è in una guerra regular, la salva (attacchi propri e avversari).
3. Se il clan è in CWL questo mese, scorre tutti i round già giocati e salva
   ogni guerra della stagione.

È **idempotente**: puoi rilanciarlo ogni 5 minuti (o quanto vuoi) senza
creare duplicati — aggiorna solo lo stato delle guerre già viste e
ne aggiunge di nuove.

## 1. Genera la chiave API

Le chiavi di developer.clashofclans.com sono vincolate a un IP specifico.
Devi quindi **prima decidere dove farà girare questo script** (vedi la
sezione "Dove farlo girare" più sotto), poi:

1. Vai su https://developer.clashofclans.com e crea un account/accedi.
2. "My Account" → "Create New Key".
3. Come IP address, inserisci l'IP pubblico della macchina che eseguirà lo
   script (puoi ottenerlo con `curl ifconfig.me` da quella macchina).
4. Copia il token: ti servirà nel file `.env`.

Se in futuro cambi macchina o l'IP cambia, la chiave smette di funzionare
finché non la rigeneri con il nuovo IP (errore `accessDenied.invalidIp`,
che lo script segnala esplicitamente nei log).

## 2. Setup locale

```bash
cd coc-cwl-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# apri .env e inserisci COC_API_TOKEN e CLAN_TAG
```

## 3. Prova manuale

```bash
python -m collector.run
```

Se tutto va bene vedrai nei log il nome del clan e, se c'è una guerra in
corso, i dettagli salvati. Il database viene creato in `data/coc.db`
(percorso configurabile con `DB_PATH` in `.env`).

Puoi ispezionarlo con:

```bash
sqlite3 data/coc.db "SELECT war_type, opponent_name, result, state FROM wars;"
sqlite3 data/coc.db "SELECT attacker_tag, defender_tag, stars, destruction_pct FROM attacks;"
```

## 4. Test senza rete

C'è un test "a freddo" che verifica tutta la logica di parsing e
salvataggio con dati finti, senza contattare l'API:

```bash
python -m tests.smoke_test
```

## Dove farlo girare

La chiave API richiede un IP fisso, e la tua connessione di casa è dietro
CGNAT (niente IP pubblico dedicato). Ti consiglio quindi:

- **Opzione consigliata per l'MVP**: una piccola VPS economica (va benissimo
  la taglia più piccola disponibile: il carico di questo script è minimo).
  Ci fai girare tutto — questo collector, e in seguito anche il database e
  la dashboard.
- **In alternativa**: solo questo collector sulla VPS (per avere un IP
  fisso), mentre il database e la dashboard restano sul tuo server Debian
  di casa, raggiunti via Tailscale.

## 5. Esecuzione periodica (systemd timer)

Sulla macchina scelta (es. la VPS):

```bash
sudo mkdir -p /opt/coc-cwl-tracker
sudo cp -r . /opt/coc-cwl-tracker
cd /opt/coc-cwl-tracker
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # e compilalo

sudo cp deploy/coc-collector.service /etc/systemd/system/
sudo cp deploy/coc-collector.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coc-collector.timer

# per controllare che funzioni:
sudo systemctl start coc-collector.service   # esecuzione immediata di prova
journalctl -u coc-collector.service -f
```

Il timer di esempio gira ogni 5 minuti. Durante una CWL o una guerra attiva
puoi accorciarlo (es. `OnUnitActiveSec=2min` in `coc-collector.timer`) per
non perdere attacchi fatti a ridosso della fine round.

## Struttura del progetto

```
coc-cwl-tracker/
├── collector/
│   ├── config.py       # lettura configurazione da .env
│   ├── coc_client.py   # chiamate alle API ufficiali di Clash of Clans
│   ├── db.py           # schema SQLite e funzioni di upsert
│   └── run.py          # orchestrazione: fetch → normalizza → salva
├── tests/
│   ├── fixtures.py     # dati finti per i test
│   └── smoke_test.py   # test di parsing senza rete
├── deploy/
│   ├── coc-collector.service
│   └── coc-collector.timer
├── requirements.txt
└── .env.example
```

## Fase 2 — Classifica CWL

Aggiunta senza toccare lo schema né le funzioni di scrittura del collector:
solo due funzioni di lettura in più in `collector/db.py`
(`fetch_own_attacks`, `fetch_players_by_tag`), un nuovo modulo di calcolo
(`collector/scoring.py`) e un piccolo backend (`api/`) che lo espone.

### Come viene calcolato il punteggio

Il motore separa **performance** e **reputazione**.

```
qualità_attacco = stelle + 0.3 × (distruzione% / 100)
media_corretta = shrinkage verso la media del clan per i campioni piccoli
performance = media_corretta × costanza × 25

partecipazione = attacchi_effettuati / attacchi_disponibili
confidence = war_giocate / (war_giocate + 5)
reputazione = [80% partecipazione + 20% successo attacchi] × confidence

punteggio_finale = 85% performance + 15% reputazione
```

La tabella `war_participants` salva il roster di ogni guerra e permette di
distinguere un attacco non effettuato da un giocatore che semplicemente ha
pochi dati. Per i database storici creati prima di questa modifica viene
eseguita una migrazione non distruttiva: per le guerre già presenti si può
ricostruire solo ciò che è noto dagli attacchi salvati, mentre le nuove
raccolte registrano il roster completo.

La confidence evita inoltre che una singola guerra perfetta venga considerata
equivalente a una lunga cronologia. La reputazione ha un peso limitato e non
può compensare da sola una performance offensiva nettamente inferiore.

Le fasce di valutazione (`RATING_BANDS` nello stesso file) sono soglie di
partenza: Eccellente ≥75, Ottimo ≥60, Buono ≥45, Sufficiente ≥30, sotto
"Da migliorare". Sono un punto di partenza, non un dogma: falle su e giù
quando vedi la distribuzione reale del tuo clan.

Per cambiare i pesi non serve toccare né l'API né il frontend: basta
modificare `ScoringConfig` (o passarne una istanza diversa a
`compute_ranking`).

### Dati letti dal database

`compute_ranking` legge solo dalle tabelle già esistenti:

- `attacks` (`side='own'`) — uno per ogni attacco lanciato dai tuoi giocatori
- `war_participants` — roster della guerra, attacchi disponibili ed effettuati
- `wars` — per il tipo di guerra (regular/cwl) e la data, usati per i filtri
- `players` — per il nome visualizzato

Non scrive nulla e non chiama l'API di Clash of Clans: lavora solo sui dati
che il collector ha già raccolto.

### Nuovi file

- `collector/scoring.py` — logica pura di calcolo (nessuna dipendenza da
  web framework), riusabile da API, script, o test.
- `api/main.py` — backend FastAPI con due endpoint.
- `api/templates/ranking.html` — pagina della classifica.
- `tests/test_scoring.py` — verifica la formula su tre profili di
  giocatore molto diversi (dati di test, non reali).

### Endpoint

- `GET /api/ranking` — JSON con la classifica completa (`war_type` e
  `since` come query param opzionali: `?war_type=cwl`).
- `GET /classifica` — pagina HTML con la tabella. Le intestazioni di
  colonna sono link che cambiano l'ordinamento; sotto ogni riga c'è un
  dettaglio a scomparsa con la scomposizione del punteggio.

### Come avviarla e verificarla

```bash
# nel venv già creato per il collector
pip install -r requirements.txt   # aggiunge fastapi, uvicorn, jinja2

uvicorn api.main:app --reload
```

Poi apri http://127.0.0.1:8000/classifica (o chiama
`curl http://127.0.0.1:8000/api/ranking`).

Se il database non ha ancora nessuna guerra 'own', la pagina lo dice
esplicitamente invece di mostrare una tabella vuota o inventare dati.

Prova rapida della sola logica di calcolo, senza avviare il server:

```bash
python -m tests.test_scoring
```

## Prossimi passi

1. Simulatore/selettore squadra CWL (vincoli su numero giocatori, min/max
   per Town Hall, include/escludi obbligatori) sopra alla stessa classifica.
2. Scheda di dettaglio per singolo giocatore con andamento nel tempo.
3. Statistiche difensive (ricostruite da `side='opponent'`), mostrate
   separatamente dal punteggio offensivo.
