"""
Configurazione del collector.

Tutti i parametri arrivano da variabili d'ambiente (caricate anche da un file
.env nella root del progetto, se presente). Vedi .env.example.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _clean_tag(tag: str) -> str:
    tag = tag.strip().upper()
    if tag and not tag.startswith("#"):
        tag = "#" + tag
    return tag


COC_API_TOKEN = os.environ.get("COC_API_TOKEN", "")
CLAN_TAG = _clean_tag(os.environ.get("CLAN_TAG", ""))
DB_PATH = os.environ.get("DB_PATH", "data/coc.db")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "15"))


def validate() -> None:
    """Controlla che la configurazione minima sia presente.

    Viene chiamata esplicitamente da run.py (non a import-time), così i
    moduli restano importabili anche nei test senza dover avere per forza
    un token reale in ambiente.
    """
    if not COC_API_TOKEN:
        raise RuntimeError("COC_API_TOKEN mancante: impostalo nel file .env")
    if not CLAN_TAG or CLAN_TAG == "#":
        raise RuntimeError("CLAN_TAG mancante: impostalo nel file .env (es. #2VP0J0VV)")
