"""
Punto di ingresso del collector.

Esegue UN solo giro di raccolta dati e termina (pensato per essere lanciato
periodicamente da un systemd timer o da cron — vedi README.md). Ogni run:

1. Legge le informazioni generali del clan.
2. Legge la guerra regular corrente, se c'è.
3. Legge il gruppo CWL corrente e, per ogni round già giocato, la guerra
   corrispondente.

I dati vengono normalizzati e scritti su SQLite in modo idempotente: si può
rilanciare lo script tutte le volte che si vuole senza creare duplicati.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import coc_client, config, db

logger = logging.getLogger("collector")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_members(war_clan: dict) -> list[dict]:
    members = []
    for m in war_clan.get("members", []):
        members.append({
            "tag": m["tag"],
            "name": m.get("name"),
            # attenzione: nei payload di guerra il campo è tutto minuscolo,
            # diverso da townHallLevel usato su /players/{tag}
            "town_hall": m.get("townhallLevel"),
        })
    return members


def _extract_attacks(attacking_side: dict, defending_side: dict, side: str) -> list[dict]:
    """side='own' per gli attacchi lanciati dai tuoi giocatori,
    side='opponent' per quelli lanciati dagli avversari (= le tue difese)."""
    th_by_tag = {
        m["tag"]: m.get("townhallLevel")
        for m in attacking_side.get("members", []) + defending_side.get("members", [])
    }
    attacks = []
    for m in attacking_side.get("members", []):
        for a in m.get("attacks", []):
            attacks.append({
                "side": side,
                "attacker_tag": a["attackerTag"],
                "attacker_th": th_by_tag.get(a["attackerTag"]),
                "defender_tag": a["defenderTag"],
                "defender_th": th_by_tag.get(a["defenderTag"]),
                "stars": a.get("stars"),
                "destruction_pct": a.get("destructionPercentage"),
                "attack_order": a.get("order"),
                "duration_seconds": a.get("duration"),
            })
    return attacks


def store_war(conn, war_json: dict, *, clan_tag: str, war_type: str,
              war_tag: str | None, cwl_season: str | None) -> int | None:
    """Normalizza e salva una guerra (regular o cwl). Restituisce l'id della
    guerra salvata, o None se non c'era nulla da salvare."""
    state = war_json.get("state")
    if state in (None, "notInWar"):
        return None

    clan = war_json["clan"]
    opponent = war_json["opponent"]

    # Per le guerre CWL l'API non garantisce che 'clan' sia il nostro:
    # normalizziamo così 'clan' è sempre il clan che stiamo tracciando.
    if clan.get("tag") != clan_tag and opponent.get("tag") == clan_tag:
        clan, opponent = opponent, clan

    if war_type == "regular":
        natural_key = f"regular:{clan_tag}:{war_json.get('preparationStartTime')}"
    else:
        natural_key = f"cwl:{war_tag}"

    war_id = db.upsert_war(
        conn,
        natural_key=natural_key,
        war_type=war_type,
        war_tag=war_tag,
        cwl_season=cwl_season,
        clan_tag=clan_tag,
        opponent_tag=opponent.get("tag"),
        opponent_name=opponent.get("name"),
        team_size=war_json.get("teamSize"),
        result=war_json.get("result"),
        state=state,
        start_time=war_json.get("startTime"),
        end_time=war_json.get("endTime"),
        captured_at=_now_iso(),
    )

    for side_clan in (clan, opponent):
        for m in _extract_members(side_clan):
            db.upsert_player(conn, m["tag"], m["name"], m["town_hall"], None, _now_iso())

    attacks = _extract_attacks(clan, opponent, "own") + \
        _extract_attacks(opponent, clan, "opponent")
    db.replace_attacks(conn, war_id, attacks)

    # Salviamo anche il roster: è fondamentale per distinguere un giocatore
    # che ha avuto un'opportunità e non l'ha usata da uno con pochi dati.
    participants = [
        {
            "player_tag": m["tag"],
            # Regular wars allow two attacks per member; CWL allows one.
            "attacks_available": 2 if war_type == "regular" else 1,
            "attacks_made": len(m.get("attacks", [])),
        }
        for m in clan.get("members", [])
    ]
    db.replace_war_participants(conn, war_id, participants)

    logger.info(
        "Guerra salvata (%s): %s vs %s — stato=%s, %d attacchi",
        war_type, clan.get("name"), opponent.get("name"), state, len(attacks),
    )
    return war_id


def sync_clan_members(conn, clan_tag: str) -> dict:
    clan_info = coc_client.get_clan(clan_tag)
    for m in clan_info.get("memberList", []):
        db.upsert_player(conn, m["tag"], m.get("name"), None, m.get("role"), _now_iso())
    return clan_info


def run_once() -> None:
    config.validate()
    db.init_db()
    clan_tag = config.CLAN_TAG

    with db.get_conn() as conn:
        clan_info = sync_clan_members(conn, clan_tag)
        logger.info(
            "Clan: %s (livello %s, %d membri)",
            clan_info.get("name"), clan_info.get("clanLevel"), clan_info.get("members", 0),
        )

        current_war = coc_client.get_current_war(clan_tag)
        if current_war:
            store_war(conn, current_war, clan_tag=clan_tag, war_type="regular",
                      war_tag=None, cwl_season=None)

        league_group = coc_client.get_league_group(clan_tag)
        if league_group:
            season = league_group.get("season")
            for round_ in league_group.get("rounds", []):
                for war_tag in round_.get("warTags", []):
                    if war_tag == "#0":
                        continue  # round non ancora disputato
                    try:
                        war_json = coc_client.get_league_war(war_tag)
                    except coc_client.CocApiError as exc:
                        logger.warning("Guerra CWL %s non recuperabile: %s", war_tag, exc)
                        continue
                    if clan_tag not in (war_json["clan"]["tag"], war_json["opponent"]["tag"]):
                        continue  # round di un'altra squadra del gruppo CWL
                    store_war(conn, war_json, clan_tag=clan_tag, war_type="cwl",
                              war_tag=war_tag, cwl_season=season)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run_once()
    except Exception:
        logger.exception("Il collector si è interrotto per un errore")
        raise
