"""
Motore di valutazione e classifica dei giocatori per la CWL.

Il punteggio separa due concetti:
- PERFORMANCE: qualità degli attacchi, difficoltà dei bersagli, correzione per
  campioni piccoli e costanza della performance;
- REPUTAZIONE: affidabilità nel tempo, ricavata da presenza nel roster e
  utilizzo degli attacchi disponibili.

La reputazione non è un moltiplicatore dominante: contribuisce direttamente
al punteggio finale con un peso limitato. Tutti i coefficienti sono raccolti
in ScoringConfig.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from statistics import mean, pstdev

from . import db


@dataclass
class ScoringConfig:
    difficulty_coefficient: float = 0.15
    difficulty_min: float = 0.5
    difficulty_max: float = 1.75
    destruction_weight: float = 0.3
    shrinkage_k: float = 5.0
    consistency_max_penalty: float = 0.15
    scale_factor: float = 25.0
    success_star_threshold: int = 2

    # La reputazione pesa il 15% del punteggio finale: abbastanza per
    # distinguere affidabilità diverse, ma non abbastanza da compensare una
    # performance offensiva nettamente peggiore.
    reputation_weight: float = 0.15

    # Quanti dati servono prima che la reputazione abbia piena fiducia.
    reputation_confidence_wars: float = 5.0

    # Piccolo contributo dei risultati degli attacchi alla reputazione.
    # La performance resta comunque il cuore del punteggio.
    reputation_success_weight: float = 0.20


DEFAULT_CONFIG = ScoringConfig()

RATING_BANDS: list[tuple[float, str]] = [
    (75.0, "Eccellente"),
    (60.0, "Ottimo"),
    (45.0, "Buono"),
    (30.0, "Sufficiente"),
    (float("-inf"), "Da migliorare"),
]


def rating_label(score: float, bands: list[tuple[float, str]] = RATING_BANDS) -> str:
    for threshold, label in bands:
        if score >= threshold:
            return label
    return bands[-1][1]


def attack_quality(stars: int | None, destruction_pct: float | None,
                   cfg: ScoringConfig) -> float:
    stars = stars or 0
    destruction_pct = destruction_pct or 0.0
    return stars + cfg.destruction_weight * (destruction_pct / 100)


def difficulty_multiplier(attacker_th: int | None, defender_th: int | None,
                          cfg: ScoringConfig) -> float:
    if attacker_th is None or defender_th is None:
        return 1.0
    th_diff = defender_th - attacker_th
    raw = 1 + cfg.difficulty_coefficient * th_diff
    return max(cfg.difficulty_min, min(cfg.difficulty_max, raw))


@dataclass
class PlayerRanking:
    position: int
    tag: str
    name: str
    wars_played: int
    attacks_made: int
    attacks_available: int
    participation_rate: float
    participation_confidence: float
    reputation: float
    reputation_label: str
    stars_total: int
    avg_stars: float
    success_rate: float
    avg_th_diff: float
    avg_difficulty_multiplier: float
    raw_avg_quality: float
    adjusted_avg_quality: float
    consistency_factor: float
    performance_score: float
    score: float
    rating: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RankingResult:
    generated_at: str
    war_type_filter: str
    since_filter: str | None
    config: ScoringConfig
    players: list[PlayerRanking] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "war_type_filter": self.war_type_filter,
            "since_filter": self.since_filter,
            "config": asdict(self.config),
            "players": [p.as_dict() for p in self.players],
        }


def _confidence(wars_played: int, cfg: ScoringConfig) -> float:
    """Confidence progressiva: pochi dati non vengono trattati come una
    cronologia lunga, senza introdurre un bonus artificiale al volume."""
    if wars_played <= 0:
        return 0.0
    return min(1.0, wars_played / (wars_played + cfg.reputation_confidence_wars))


def _reputation_label(value: float) -> str:
    if value >= 85:
        return "Molto alta"
    if value >= 70:
        return "Alta"
    if value >= 50:
        return "Media"
    if value >= 30:
        return "Bassa"
    return "Molto bassa"


def compute_ranking(conn, war_type: str = "all", since: str | None = None,
                     cfg: ScoringConfig = DEFAULT_CONFIG) -> RankingResult:
    """Calcola la classifica usando esclusivamente i dati già in SQLite."""
    rows = db.fetch_own_attacks(conn, war_type=war_type, since=since)
    participants = db.fetch_war_participants(
        conn, war_type=war_type, since=since
    )

    result = RankingResult(
        generated_at=datetime.now(timezone.utc).isoformat(),
        war_type_filter=war_type,
        since_filter=since,
        config=cfg,
    )
    if not rows:
        return result

    # Ogni attacco viene pesato (qualità × difficoltà) una sola volta qui, e il
    # risultato viene riusato sia per la media di clan sia per i calcoli per
    # giocatore/per guerra più sotto — prima veniva ricalcolato fino a 3 volte
    # per lo stesso attacco (una volta per la media di clan, una nel ciclo per
    # giocatore, una dentro il vecchio _per_war_averages).
    weighted_attacks: list[tuple[sqlite3.Row, float, float]] = []
    for r in rows:
        m = difficulty_multiplier(r["attacker_th"], r["defender_th"], cfg)
        q = attack_quality(r["stars"], r["destruction_pct"], cfg)
        weighted_attacks.append((r, q * m, m))

    clan_avg = mean(w for _, w, _ in weighted_attacks)

    by_player: dict[str, list[tuple[sqlite3.Row, float, float]]] = defaultdict(list)
    for item in weighted_attacks:
        by_player[item[0]["attacker_tag"]].append(item)

    players_info = db.fetch_players_by_tag(conn, list(by_player.keys()))

    # Il roster può contenere giocatori che hanno effettuato zero attacchi.
    # Li consideriamo per la reputazione, ma non possono entrare nella
    # classifica offensiva perché non abbiamo una performance da valutare.
    reputation_by_tag: dict[str, dict] = {}
    for p in participants:
        reputation_by_tag[p["player_tag"]] = {
            "wars_played": p["wars_played"],
            "attacks_made": p["attacks_made"],
            "attacks_available": p["attacks_available"],
        }

    rankings: list[PlayerRanking] = []
    for tag, items in by_player.items():
        weighted, th_diffs, stars_list, multipliers = [], [], [], []
        by_war: dict[int, list[float]] = defaultdict(list)
        for a, w, m in items:
            weighted.append(w)
            multipliers.append(m)
            by_war[a["war_id"]].append(w)
            if a["attacker_th"] is not None and a["defender_th"] is not None:
                th_diffs.append(a["defender_th"] - a["attacker_th"])
            stars_list.append(a["stars"] or 0)
        war_ids = set(by_war.keys())

        n = len(items)
        raw_avg = mean(weighted)
        adjusted_avg = (raw_avg * n + clan_avg * cfg.shrinkage_k) / (n + cfg.shrinkage_k)

        per_war_avgs = [mean(values) for values in by_war.values()]
        if len(per_war_avgs) >= 2 and mean(per_war_avgs) > 0:
            cv = pstdev(per_war_avgs) / mean(per_war_avgs)
            consistency = 1 - min(
                cfg.consistency_max_penalty,
                cfg.consistency_max_penalty * cv,
            )
        else:
            consistency = 1.0

        performance_score = adjusted_avg * consistency * cfg.scale_factor
        successes = sum(1 for s in stars_list if s >= cfg.success_star_threshold)
        success_rate = successes / n if n else 0.0

        rep_data = reputation_by_tag.get(tag)
        if rep_data:
            wars_played = rep_data["wars_played"]
            attacks_made = rep_data["attacks_made"]
            attacks_available = rep_data["attacks_available"]
        else:
            # Compatibilità con vecchi DB: prima della nuova tabella roster
            # possiamo conoscere solo gli attacchi effettivamente registrati.
            wars_played = len(war_ids)
            attacks_made = n
            attacks_available = n

        participation_rate = (
            min(1.0, attacks_made / attacks_available)
            if attacks_available > 0 else 0.0
        )
        confidence = _confidence(wars_played, cfg)

        # Reputazione = affidabilità di utilizzo delle opportunità disponibili
        # + piccolo contributo ai risultati. La confidence evita che 1-2 war
        # perfette vengano trattate come una cronologia consolidata.
        raw_reputation = 100 * (
            (1 - cfg.reputation_success_weight) * participation_rate
            + cfg.reputation_success_weight * success_rate
        )
        reputation = raw_reputation * confidence

        # Combinazione additiva: la reputazione può spostare la posizione, ma
        # non può trasformare da sola una performance mediocre in eccellente.
        score = (
            performance_score * (1 - cfg.reputation_weight)
            + reputation * cfg.reputation_weight
        )

        info = players_info.get(tag)
        rankings.append(PlayerRanking(
            position=0,
            tag=tag,
            name=info["name"] if info and info["name"] else tag,
            wars_played=wars_played,
            attacks_made=attacks_made,
            attacks_available=attacks_available,
            participation_rate=round(participation_rate, 3),
            participation_confidence=round(confidence, 3),
            reputation=round(reputation, 2),
            reputation_label=_reputation_label(reputation),
            stars_total=sum(stars_list),
            avg_stars=round(mean(stars_list), 2),
            success_rate=round(success_rate, 3),
            avg_th_diff=round(mean(th_diffs), 2) if th_diffs else 0.0,
            avg_difficulty_multiplier=round(mean(multipliers), 3),
            raw_avg_quality=round(raw_avg, 3),
            adjusted_avg_quality=round(adjusted_avg, 3),
            consistency_factor=round(consistency, 3),
            performance_score=round(performance_score, 2),
            score=round(score, 2),
            rating=rating_label(score),
        ))

    rankings.sort(key=lambda p: (-p.score, -p.reputation, p.name.lower()))
    for i, p in enumerate(rankings, start=1):
        p.position = i

    result.players = rankings
    return result
