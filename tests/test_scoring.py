"""
Test del motore di scoring (collector/scoring.py).

Non chiama nessuna API: inserisce direttamente nel database, con lo stesso
schema usato dal collector, tre giocatori con profili molto diversi
(coerenti con gli esempi discussi in fase di analisi):

- Player A: poche guerre, sempre attacchi di alta qualità contro bersagli
  pari o più alti di TH.
- Player B: tante guerre, tanti attacchi, ma quasi sempre contro bersagli
  più deboli.
- Player C: pochissimi attacchi, molto incostanti (un attacco perfetto, uno
  pessimo, uno mediocre).

Verifica che il punteggio finale rifletta la qualità pesata per difficoltà
e non il volume di attacchi, e che l'ordinamento risultante sia quello
atteso.

Uso: python -m tests.test_scoring   (dalla root del progetto)
"""
import tempfile
from pathlib import Path

from collector import db, scoring


def _make_war(conn, natural_key: str, start_time: str) -> int:
    return db.upsert_war(
        conn,
        natural_key=natural_key,
        war_type="regular",
        war_tag=None,
        cwl_season=None,
        clan_tag="#OWNCLAN",
        opponent_tag="#OPP",
        opponent_name="Avversario",
        team_size=2,
        result="win",
        state="warEnded",
        start_time=start_time,
        end_time=start_time,
        captured_at=start_time,
    )


def _add_attacks(conn, war_id: int, attacker_tag: str, attacks: list[tuple[int, float, int]]) -> None:
    """attacks: lista di (stelle, distruzione%, differenza_TH_avversario_vs_attaccante)"""
    rows = []
    for i, (stars, destruction, th_diff) in enumerate(attacks):
        attacker_th = 14
        rows.append({
            "side": "own",
            "attacker_tag": attacker_tag,
            "attacker_th": attacker_th,
            "defender_tag": f"#DEF{i}",
            "defender_th": attacker_th + th_diff,
            "stars": stars,
            "destruction_pct": destruction,
            "attack_order": i,
            "duration_seconds": 120,
        })
    # In questo test ogni guerra ha un solo attaccante, quindi replace_attacks
    # (che sostituisce tutti gli attacchi della guerra) si può usare così com'è.
    db.replace_attacks(conn, war_id, rows)


def _set_participant(conn, war_id: int, tag: str, attacks_made: int,
                      attacks_available: int = 1) -> None:
    db.replace_war_participants(conn, war_id, [{
        "player_tag": tag,
        "attacks_available": attacks_available,
        "attacks_made": attacks_made,
    }])


def _setup_players(conn) -> None:
    db.upsert_player(conn, "#A", "Player A", 14, "member", "2026-09-01")
    db.upsert_player(conn, "#B", "Player B", 14, "member", "2026-09-01")
    db.upsert_player(conn, "#C", "Player C", 14, "member", "2026-09-01")




def test_reputation_distinguishes_participation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "reputation_test.db")
        db.init_db(db_path)
        with db.get_conn(db_path) as conn:
            _setup_players(conn)

            # D: ottima performance ma perde molte opportunità (2/10 attacchi).
            for i in range(10):
                w = _make_war(conn, f"d-war-{i}", f"2026-09-{i+1:02d}")
                _add_attacks(conn, w, "#A", [(3, 100, 0)] if i < 2 else [])
                _set_participant(conn, w, "#A", 1 if i < 2 else 0)

            # E: stessa qualità negli attacchi, ma li usa tutti (10/10).
            for i in range(10):
                w = _make_war(conn, f"e-war-{i}", f"2026-10-{i+1:02d}")
                _add_attacks(conn, w, "#B", [(3, 100, 0)])
                _set_participant(conn, w, "#B", 1)

        with db.get_conn(db_path) as conn:
            result = scoring.compute_ranking(conn)

        players = {p.tag: p for p in result.players}
        assert players["#B"].attacks_available == 10
        assert players["#A"].attacks_available == 10
        assert players["#B"].participation_rate == 1.0
        assert players["#A"].participation_rate == 0.2
        assert players["#B"].reputation > players["#A"].reputation
        assert players["#B"].score > players["#A"].score


def main() -> None:
    test_reputation_distinguishes_participation()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "scoring_test.db")
        db.init_db(db_path)

        with db.get_conn(db_path) as conn:
            _setup_players(conn)

            # --- Player A: 10 attacchi su 5 guerre, quasi sempre ottimi ---
            for i in range(4):
                w = _make_war(conn, f"a-war-{i}", f"2026-09-0{i+1}")
                _add_attacks(conn, w, "#A", [(3, 98, 0), (3, 98, 0)])
            w = _make_war(conn, "a-war-4", "2026-09-05")
            _add_attacks(conn, w, "#A", [(3, 100, 1), (3, 100, 1)])

            # --- Player B: 15 attacchi su 8 guerre, sempre bersagli deboli ---
            for i in range(5):
                w = _make_war(conn, f"b-war-{i}", f"2026-09-1{i}")
                _add_attacks(conn, w, "#B", [(2, 70, -2), (2, 70, -2)])
            for i in range(2):
                w = _make_war(conn, f"b-war-extra-{i}", f"2026-09-2{i}")
                _add_attacks(conn, w, "#B", [(2, 70, -3), (2, 70, -3)])
            w = _make_war(conn, "b-war-last", "2026-09-25")
            _add_attacks(conn, w, "#B", [(2, 70, -3)])

            # --- Player C: 3 attacchi su 3 guerre, molto incostante ---
            w = _make_war(conn, "c-war-0", "2026-09-01")
            _add_attacks(conn, w, "#C", [(3, 100, 0)])
            w = _make_war(conn, "c-war-1", "2026-09-02")
            _add_attacks(conn, w, "#C", [(0, 20, 0)])
            w = _make_war(conn, "c-war-2", "2026-09-03")
            _add_attacks(conn, w, "#C", [(1, 40, -1)])

        with db.get_conn(db_path) as conn:
            result = scoring.compute_ranking(conn)

        players = {p.tag: p for p in result.players}
        assert set(players) == {"#A", "#B", "#C"}

        # Conteggi di base
        assert players["#A"].wars_played == 5
        assert players["#A"].attacks_made == 10
        assert players["#B"].wars_played == 8
        assert players["#B"].attacks_made == 15
        assert players["#C"].wars_played == 3
        assert players["#C"].attacks_made == 3

        # Il giocatore che attacca bersagli più difficili con più successo
        # deve vincere, nonostante meno attacchi totali.
        ordered_tags = [p.tag for p in result.players]
        assert ordered_tags[0] == "#A", f"atteso #A in testa, trovato: {ordered_tags}"
        assert players["#A"].score > players["#B"].score
        assert players["#A"].score > players["#C"].score

        # Il volume puro di attacchi facili (B) non deve garantire un
        # punteggio nettamente superiore a un campione piccolo (C): sono
        # nello stesso ordine di grandezza, non separati da un fattore 2x+.
        assert abs(players["#B"].score - players["#C"].score) < 15

        # Il position field riflette l'ordinamento
        assert result.players[0].position == 1
        assert result.players[-1].position == len(result.players)

        # Le fasce di valutazione sono coerenti con le soglie configurate
        assert players["#A"].rating in ("Eccellente", "Ottimo")

        # Filtro per tipo di guerra: qui sono tutte 'regular', quindi
        # chiedendo solo 'cwl' il risultato deve essere vuoto.
        with db.get_conn(db_path) as conn:
            empty = scoring.compute_ranking(conn, war_type="cwl")
            assert empty.players == []

    print("OK: tutti i controlli del test di scoring sono passati.")
    for p in result.players:
        print(f"  {p.position}. {p.name:10s} score={p.score:6.2f}  "
              f"({p.rating}) — guerre={p.wars_played} attacchi={p.attacks_made} "
              f"stelle={p.stars_total} diff_TH_media={p.avg_th_diff}")


if __name__ == "__main__":
    main()
