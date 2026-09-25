"""
Test 'a freddo', senza rete: verifica che una guerra finta venga
normalizzata e salvata correttamente nel database.

Uso: python -m tests.smoke_test   (dalla root del progetto)
"""
import tempfile
from pathlib import Path

from collector import db, run
from tests.fixtures import SAMPLE_REGULAR_WAR


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db.init_db(db_path)

        with db.get_conn(db_path) as conn:
            war_id = run.store_war(
                conn, SAMPLE_REGULAR_WAR,
                clan_tag="#OWNCLAN", war_type="regular",
                war_tag=None, cwl_season=None,
            )
            assert war_id is not None, "store_war ha restituito None"

        with db.get_conn(db_path) as conn:
            war = conn.execute("SELECT * FROM wars WHERE id = ?", (war_id,)).fetchone()
            assert war["opponent_name"] == "Clan Rivale"
            assert war["result"] == "win"
            assert war["war_type"] == "regular"

            attacks = conn.execute(
                "SELECT * FROM attacks WHERE war_id = ? ORDER BY attack_order", (war_id,)
            ).fetchall()
            assert len(attacks) == 4, f"attesi 4 attacchi, trovati {len(attacks)}"

            own_attacks = [a for a in attacks if a["side"] == "own"]
            opp_attacks = [a for a in attacks if a["side"] == "opponent"]
            assert len(own_attacks) == 2
            assert len(opp_attacks) == 2

            participants = conn.execute(
                "SELECT * FROM war_participants WHERE war_id = ? ORDER BY player_tag",
                (war_id,),
            ).fetchall()
            assert len(participants) == 2
            mario_part = next(p for p in participants if p["player_tag"] == "#P1")
            assert mario_part["attacks_available"] == 2
            assert mario_part["attacks_made"] == 1

            # Verifica che i TH siano stati abbinati correttamente
            mario_attack = next(a for a in own_attacks if a["attacker_tag"] == "#P1")
            assert mario_attack["attacker_th"] == 15
            assert mario_attack["defender_th"] == 15
            assert mario_attack["stars"] == 3

            # Verifica ricostruzione difesa: l'attacco di Bowser (#O1) contro
            # Mario (#P1) deve comparire come side='opponent'
            defense_on_mario = next(a for a in opp_attacks if a["defender_tag"] == "#P1")
            assert defense_on_mario["attacker_tag"] == "#O1"
            assert defense_on_mario["stars"] == 2

            players = conn.execute("SELECT * FROM players ORDER BY tag").fetchall()
            assert len(players) == 4
            mario = next(p for p in players if p["tag"] == "#P1")
            assert mario["name"] == "Mario"
            assert mario["town_hall"] == 15

        # Rilanciare store_war con la STESSA guerra non deve creare duplicati
        with db.get_conn(db_path) as conn:
            war_id_2 = run.store_war(
                conn, SAMPLE_REGULAR_WAR,
                clan_tag="#OWNCLAN", war_type="regular",
                war_tag=None, cwl_season=None,
            )
            assert war_id_2 == war_id, "una guerra già salvata non deve duplicarsi"
            n_wars = conn.execute("SELECT COUNT(*) AS n FROM wars").fetchone()["n"]
            assert n_wars == 1
            n_attacks = conn.execute("SELECT COUNT(*) AS n FROM attacks").fetchone()["n"]
            assert n_attacks == 4, "il secondo salvataggio non deve duplicare gli attacchi"

    print("OK: tutti i controlli del smoke test sono passati.")


if __name__ == "__main__":
    main()
