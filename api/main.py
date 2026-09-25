"""
Backend minimale che espone la classifica CWL calcolata da collector.scoring.

Non contiene logica di calcolo: legge dal database (via collector.db) e
delega tutta l'elaborazione a collector.scoring.compute_ranking. Se in
futuro cambi i pesi dello scoring, questo file non va toccato.

Endpoint:
- GET /api/ranking   -> JSON con i giocatori valutati
- GET /classifica    -> pagina HTML con la tabella, ordinabile dall'intestazione

Avvio in locale:
    uvicorn api.main:app --reload
poi apri http://127.0.0.1:8000/classifica
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from collector import db, scoring, run

app = FastAPI(title="CoC CWL Tracker - Ranking")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Il pulsante "Aggiorna" sostituisce il polling periodico con una chiamata su
# richiesta. Il lock evita che due click ravvicinati (o due tab aperte)
# lancino due raccolte in parallelo: stessa chiave API, stesso database.
_refresh_lock = threading.Lock()

@app.on_event("startup")
def startup() -> None:
    # Il backend deve poter partire anche prima del primo giro del collector.
    # init_db è idempotente e non modifica/distrugge i dati esistenti.
    db.init_db()


# Campi su cui la pagina HTML permette l'ordinamento cliccando l'intestazione
SORTABLE_FIELDS = {
    "score", "name", "wars_played", "attacks_made", "attacks_available",
    "participation_rate", "reputation", "stars_total", "avg_stars",
    "success_rate",
}


def _get_ranking(war_type: str, since: str | None) -> scoring.RankingResult:
    with db.get_conn() as conn:
        return scoring.compute_ranking(conn, war_type=war_type, since=since)


@app.get("/api/ranking")
def api_ranking(war_type: Literal["all", "regular", "cwl"] = "all",
                 since: str | None = None) -> dict:
    """Classifica in JSON, già ordinata per punteggio decrescente."""
    return _get_ranking(war_type, since).as_dict()


@app.get("/classifica", response_class=HTMLResponse)
def classifica_page(request: Request,
                     war_type: Literal["all", "regular", "cwl"] = "all",
                     sort: str = "score",
                     dir: Literal["asc", "desc"] = "desc") -> HTMLResponse:
    result = _get_ranking(war_type, None)
    sort_field = sort if sort in SORTABLE_FIELDS else "score"
    players = sorted(result.players, key=lambda p: getattr(p, sort_field),
                      reverse=(dir == "desc"))

    return templates.TemplateResponse(
        request,
        "ranking.html",
        {
            "players": players,
            "generated_at": result.generated_at,
            "war_type": war_type,
            "sort": sort_field,
            "dir": dir,
        },
    )


@app.post("/api/ranking/refresh")
def refresh_ranking(war_type: Literal["all", "regular", "cwl"] = "all") -> dict:
    """Aggiorna il database tramite il collector e restituisce la nuova classifica.

    Il collector è già idempotente: riutilizziamo run_once() invece di
    duplicare qui la logica di raccolta/API. Il lock serializza eventuali
    click doppi o richieste da tab diverse.
    """
    if not _refresh_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Un aggiornamento è già in corso: aspetta che finisca prima di riprovare.",
        )
    try:
        run.run_once()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Aggiornamento dati fallito: {exc}")
    finally:
        _refresh_lock.release()

    return _get_ranking(war_type, None).as_dict()
