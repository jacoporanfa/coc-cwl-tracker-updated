"""
Wrapper minimale attorno alle API ufficiali di Clash of Clans.

Copre solo gli endpoint che servono al tracker:
- /clans/{clanTag}
- /clans/{clanTag}/currentwar
- /clans/{clanTag}/currentwar/leaguegroup
- /clanwarleagues/wars/{warTag}

Nota bene: la chiave (COC_API_TOKEN) è vincolata a un IP/CIDR specifico
impostato quando la si genera su developer.clashofclans.com. Se questo
script gira su una macchina con un altro IP, ogni chiamata fallirà con
403 "accessDenied.invalidIp".
"""
from __future__ import annotations

from urllib.parse import quote

import requests

from . import config

BASE_URL = "https://api.clashofclans.com/v1"


class CocApiError(Exception):
    """Errore generico nella comunicazione con l'API di Clash of Clans."""


class PrivateWarLogError(CocApiError):
    """Il war log del clan è impostato come privato (o accesso negato)."""


class NotInWarError(CocApiError):
    """Il clan non è attualmente in una guerra regular."""


def _tag_path(tag: str) -> str:
    # I tag contengono '#', che nell'URL va sempre codificato come %23.
    return quote(tag, safe="")


def _get(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {config.COC_API_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)

    if resp.status_code == 200:
        return resp.json()

    if resp.status_code == 403:
        body = resp.json() if resp.content else {}
        reason = body.get("reason", "")
        if reason == "accessDenied.invalidIp":
            raise CocApiError(
                "Chiave API non valida per l'IP corrente. Rigenera la chiave su "
                "developer.clashofclans.com usando l'IP pubblico di questa macchina."
            )
        raise PrivateWarLogError(f"Accesso negato su {path}: {body}")

    if resp.status_code == 404:
        raise NotInWarError(f"Risorsa non trovata su {path}")

    if resp.status_code == 429:
        raise CocApiError("Rate limit raggiunto (429): riprova più tardi")

    if resp.status_code == 503:
        raise CocApiError("API in manutenzione (503)")

    raise CocApiError(f"Errore inatteso {resp.status_code} su {path}: {resp.text[:300]}")


def get_clan(clan_tag: str) -> dict:
    return _get(f"/clans/{_tag_path(clan_tag)}")


def get_current_war(clan_tag: str) -> dict | None:
    """Guerra regular corrente. Torna None se il clan non è in guerra
    (stato notInWar) o se il war log è privato: in entrambi i casi non c'è
    nulla di utile da salvare."""
    try:
        war = _get(f"/clans/{_tag_path(clan_tag)}/currentwar")
    except (NotInWarError, PrivateWarLogError):
        return None
    if war.get("state") in (None, "notInWar"):
        return None
    return war


def get_league_group(clan_tag: str) -> dict | None:
    """Gruppo CWL corrente. Torna None se il clan non è in CWL questo mese."""
    try:
        group = _get(f"/clans/{_tag_path(clan_tag)}/currentwar/leaguegroup")
    except (NotInWarError, PrivateWarLogError):
        return None
    if group.get("state") in (None, "notInWar"):
        return None
    return group


def get_league_war(war_tag: str) -> dict:
    return _get(f"/clanwarleagues/wars/{_tag_path(war_tag)}")
