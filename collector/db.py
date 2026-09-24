"""
Layer di accesso al database SQLite.

Schema:
- players: anagrafica giocatori (persiste anche se lasciano il clan)
- wars:    una riga per ogni guerra tracciata (regular o cwl)
- attacks: un attacco per riga, sia 'own' (i tuoi) sia 'opponent' (avversari,
           serve a ricostruire le difese subite dai tuoi giocatori)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tag TEXT PRIMARY KEY,
    name TEXT,
    town_hall INTEGER,
    role TEXT,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS wars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    natural_key TEXT UNIQUE NOT NULL,
    war_type TEXT NOT NULL CHECK (war_type IN ('regular', 'cwl')),
    war_tag TEXT,
    cwl_season TEXT,
    clan_tag TEXT NOT NULL,
    opponent_tag TEXT,
    opponent_name TEXT,
    team_size INTEGER,
    result TEXT,
    state TEXT,
    start_time TEXT,
    end_time TEXT,
    captured_at TEXT
);

CREATE TABLE IF NOT EXISTS war_participants (
    war_id INTEGER NOT NULL REFERENCES wars(id) ON DELETE CASCADE,
    player_tag TEXT NOT NULL REFERENCES players(tag),
    attacks_available INTEGER NOT NULL DEFAULT 1,
    attacks_made INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (war_id, player_tag)
);

CREATE INDEX IF NOT EXISTS idx_war_participants_player
    ON war_participants(player_tag);

CREATE TABLE IF NOT EXISTS attacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    war_id INTEGER NOT NULL REFERENCES wars(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('own', 'opponent')),
    attacker_tag TEXT,
    attacker_th INTEGER,
    defender_tag TEXT,
    defender_th INTEGER,
    stars INTEGER,
    destruction_pct REAL,
    attack_order INTEGER,
    duration_seconds INTEGER
);

CREATE INDEX IF NOT EXISTS idx_attacks_war ON attacks(war_id);
CREATE INDEX IF NOT EXISTS idx_attacks_attacker ON attacks(attacker_tag);
CREATE INDEX IF NOT EXISTS idx_attacks_defender ON attacks(defender_tag);
"""


@contextmanager
def get_conn(db_path: str | None = None):
    """Apre una connessione per l'uso corrente e la chiude alla fine del
    blocco `with` — è il pattern corretto per sqlite3 (le connessioni non
    sono condivisibili tra thread) e con FastAPI ogni richiesta gira nel suo
    thread, quindi ognuna apre la propria connessione.

    Con l'aggiornamento manuale dal pulsante, il collector (che scrive) può
    girare mentre qualcuno sta guardando /classifica (che legge): WAL
    permette lettori e scrittore di convivere senza bloccarsi a vicenda, e
    busy_timeout fa aspettare invece di fallire subito se capita comunque un
    breve conflitto.
    """
    path = db_path or config.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _backfill_legacy_participants(conn)


def _backfill_legacy_participants(conn) -> None:
    """Migrazione non distruttiva per i DB creati prima di war_participants.

    Per le guerre storiche conosciamo con certezza chi ha attaccato, ma non
    possiamo ricostruire chi era nel roster e ha lasciato l'attacco inutilizzato.
    Per questo il fallback usa 1 attacco disponibile per ogni giocatore che
    compare negli attacchi propri. Le nuove raccolte, invece, salvano il roster
    completo e quindi distinguono correttamente gli attacchi mancanti.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO war_participants
            (war_id, player_tag, attacks_available, attacks_made)
        SELECT a.war_id, a.attacker_tag, 1, COUNT(*)
        FROM attacks a
        WHERE a.side = 'own' AND a.attacker_tag IS NOT NULL
        GROUP BY a.war_id, a.attacker_tag
        """
    )


def upsert_player(conn, tag: str, name: str | None, town_hall: int | None,
                   role: str | None, seen_at: str) -> None:
    conn.execute(
        """
        INSERT INTO players (tag, name, town_hall, role, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
            name = COALESCE(excluded.name, players.name),
            town_hall = COALESCE(excluded.town_hall, players.town_hall),
            role = COALESCE(excluded.role, players.role),
            last_seen_at = excluded.last_seen_at
        """,
        (tag, name, town_hall, role, seen_at),
    )


def upsert_war(conn, *, natural_key: str, war_type: str, war_tag: str | None,
               cwl_season: str | None, clan_tag: str, opponent_tag: str | None,
               opponent_name: str | None, team_size: int | None,
               result: str | None, state: str | None, start_time: str | None,
               end_time: str | None, captured_at: str) -> int:
    conn.execute(
        """
        INSERT INTO wars (
            natural_key, war_type, war_tag, cwl_season, clan_tag,
            opponent_tag, opponent_name, team_size, result, state,
            start_time, end_time, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(natural_key) DO UPDATE SET
            result = excluded.result,
            state = excluded.state,
            end_time = excluded.end_time,
            captured_at = excluded.captured_at
        """,
        (natural_key, war_type, war_tag, cwl_season, clan_tag,
         opponent_tag, opponent_name, team_size, result, state,
         start_time, end_time, captured_at),
    )
    row = conn.execute(
        "SELECT id FROM wars WHERE natural_key = ?", (natural_key,)
    ).fetchone()
    return row["id"]


def fetch_own_attacks(conn, war_type: str | None = None,
                       since: str | None = None) -> list[sqlite3.Row]:
    """Attacchi lanciati dai propri giocatori (side='own'), con il contesto
    di guerra necessario per lo scoring. Usata dal motore di valutazione in
    scoring.py.

    war_type: None/'all' = nessun filtro, oppure 'regular'/'cwl'.
    since:    ISO 8601, filtra le guerre con start_time >= since.
    """
    query = """
        SELECT
            a.attacker_tag, a.attacker_th, a.defender_th,
            a.stars, a.destruction_pct, a.war_id,
            w.war_type, w.start_time, w.state
        FROM attacks a
        JOIN wars w ON w.id = a.war_id
        WHERE a.side = 'own'
    """
    params: list = []
    if war_type and war_type != "all":
        query += " AND w.war_type = ?"
        params.append(war_type)
    if since:
        query += " AND w.start_time >= ?"
        params.append(since)
    query += " ORDER BY w.start_time"
    return conn.execute(query, params).fetchall()


def fetch_players_by_tag(conn, tags: list[str]) -> dict[str, sqlite3.Row]:
    """Anagrafica dei giocatori richiesti, indicizzata per tag."""
    if not tags:
        return {}
    placeholders = ",".join("?" for _ in tags)
    rows = conn.execute(
        f"SELECT tag, name, town_hall, role FROM players WHERE tag IN ({placeholders})",
        tags,
    ).fetchall()
    return {row["tag"]: row for row in rows}


def replace_attacks(conn, war_id: int, attacks: list[dict]) -> None:
    """Sostituisce tutti gli attacchi di una guerra con lo snapshot più
    recente (l'API restituisce sempre l'elenco completo finora, non un
    delta, quindi il modo più semplice e corretto è cancellare e
    reinserire)."""
    conn.execute("DELETE FROM attacks WHERE war_id = ?", (war_id,))
    if not attacks:
        return
    conn.executemany(
        """
        INSERT INTO attacks (
            war_id, side, attacker_tag, attacker_th, defender_tag, defender_th,
            stars, destruction_pct, attack_order, duration_seconds
        ) VALUES (:war_id, :side, :attacker_tag, :attacker_th, :defender_tag,
                  :defender_th, :stars, :destruction_pct, :attack_order,
                  :duration_seconds)
        """,
        [{**a, "war_id": war_id} for a in attacks],
    )


def replace_war_participants(conn, war_id: int, participants: list[dict]) -> None:
    """Salva lo snapshot del roster della guerra.

    Ogni membro della guerra ha normalmente un attacco disponibile; il dato
    viene comunque passato esplicitamente per lasciare il modello estendibile.
    """
    conn.execute("DELETE FROM war_participants WHERE war_id = ?", (war_id,))
    if not participants:
        return
    conn.executemany(
        """
        INSERT INTO war_participants
            (war_id, player_tag, attacks_available, attacks_made)
        VALUES (:war_id, :player_tag, :attacks_available, :attacks_made)
        """,
        [{**p, "war_id": war_id} for p in participants],
    )


def fetch_war_participants(conn, war_type: str | None = None,
                           since: str | None = None) -> list[sqlite3.Row]:
    """Aggrega per giocatore le opportunità di attacco nel periodo."""
    query = """
        SELECT
            wp.player_tag,
            COUNT(DISTINCT wp.war_id) AS wars_played,
            SUM(wp.attacks_available) AS attacks_available,
            SUM(wp.attacks_made) AS attacks_made
        FROM war_participants wp
        JOIN wars w ON w.id = wp.war_id
        WHERE 1=1
    """
    params: list = []
    if war_type and war_type != "all":
        query += " AND w.war_type = ?"
        params.append(war_type)
    if since:
        query += " AND w.start_time >= ?"
        params.append(since)
    query += " GROUP BY wp.player_tag"
    return conn.execute(query, params).fetchall()
