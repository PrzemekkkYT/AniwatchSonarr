import datetime
import email
import json
from fastapi import FastAPI, Form, UploadFile, File, Response
from fastapi.responses import PlainTextResponse
import xml.etree.ElementTree as ET
from typing import Optional
import uuid
import time

import requests

from anikoto import find_anikoto_id, search_anikoto

app = FastAPI()

# Prosta baza danych w pamięci (w produkcji użyj SQLite/JSON)
downloads = {
    "testowyhash1234567890abcdef": {
        "name": "Nazwa.Testowego.Serialu.S01E01.1080p.Web-DL",
        "progress": 0.5,  # 50% pobierania
        "size": 1500000000,  # ok. 1.5 GB
        "status": "downloading",  # status pobierania
        "save_path": "/downloads",  # ścieżka zgodna z mapowaniem w TrueNAS
        "added_on": 1715800000,
    }
}


@app.post("/api/v2/auth/login", response_class=PlainTextResponse)
async def login(response: Response):
    # Ustawiamy ciasteczko sesji, którego szuka Sonarr
    response.set_cookie(key="SID", value="fake-cookie-12345")
    # Zwracamy czysty tekst "Ok." (dokładnie tak robi qBittorrent)
    return "Ok."


@app.get("/api/v2/app/webapiVersion", response_class=PlainTextResponse)
async def web_api_version():
    # Zwracamy czysty tekst bez JSON-owych cudzysłowów
    return "2.8.2"


@app.post("/api/v2/torrents/add")
async def add_torrent(
    urls: Optional[str] = Form(None), torrents: Optional[UploadFile] = File(None)
):
    """
    To tutaj Sonarr wysyła polecenie pobrania.
    W 'urls' będzie link magnet lub URL, który Twój Indexer podał Sonarrowi.
    """
    task_id = str(uuid.uuid4())

    # Wyciągamy dane z URL (np. ID odcinka z Aniwatch)
    # Tutaj wywołaj swoją funkcję pobierającą: start_aniwatch_download(urls)

    downloads[task_id] = {
        "name": f"AniWatch_Download_{task_id[:8]}",
        "progress": 0.0,
        "size": 0,
        "status": "downloading",  # Statusy qBittorrent: downloading, uploading, completed
        "save_path": "/downloads/anime",
        "added_on": int(time.time()),
    }

    print(f"[*] Rozpoczęto pobieranie z Aniwatch: {urls}")
    return "Ok."


@app.get("/api/v2/torrents/info")
async def torrents_info(category: Optional[str] = None):
    """
    Sonarr pyta o listę pobieranych rzeczy, często filtrując po kategorii.
    Nawet jeśli lista jest pusta, musimy zwrócić poprawną strukturę JSON [].
    """
    formatted_list = []

    for tid, data in downloads.items():
        # Opcjonalnie: filtrujemy po kategorii, jeśli Sonarr o to prosi
        if category and category != data.get("category", category):
            continue

        formatted_list.append(
            {
                "hash": str(tid),  # Hash musi być ciągiem znaków (string)
                "name": str(data["name"]),
                "size": int(data["size"]),
                "progress": float(data["progress"]),  # Wartość 0.0 - 1.0
                "status": str(data["status"]),
                "save_path": str(data["save_path"]),
                "added_on": int(data["added_on"]),
                "upspeed": 0,
                "dlspeed": 1024 * 1024,
                "category": data.get(
                    "category", "tv-sonarr"
                ),  # Sonarr lubi widzieć tu kategorię
            }
        )

    # FastAPI automatycznie zamieni pustą listę [] na poprawny JSON array
    return formatted_list


@app.post("/api/v2/torrents/delete")
async def delete_torrent(hashes: str = Form(...)):
    """Wywoływane, gdy Sonarr usuwa zadanie z listy."""
    for h in hashes.split("|"):
        if h in downloads:
            del downloads[h]
    return "Ok."


@app.get("/api/v2/app/preferences")
async def get_preferences():
    """
    Sonarr pyta o preferencje klienta (np. globalne limity, ścieżki).
    Zwracamy podstawowy zestaw konfiguracji, aby przeszedł test.
    """
    return {
        "save_path": "/downloads",
        "scan_dirs": [],
        "download_in_slow_mode": False,
        "queueing_enabled": False,
        "max_active_downloads": 3,
        "max_active_torrents": 999,
        "max_active_uploads": 3,
        "alt_dl_limit": -1,
        "alt_up_limit": -1,
        "dl_limit": -1,
        "up_limit": -1,
    }


@app.get("/api/v2/torrents/categories")
async def get_categories():
    """
    Sonarr sprawdza dostępne kategorie w kliencie.
    Zwracamy pusty słownik – Sonarr sam spróbuje dodać swoją kategorię
    (np. "tv-sonarr"), jeśli będzie jej potrzebował.
    """
    return {}


@app.post("/api/v2/torrents/createCategory", response_class=PlainTextResponse)
async def create_category(
    category: str = Form(...),
    savePath: str = Form(""),  # Sonarr może (ale nie musi) to wysłać
):
    """
    Wywoływane przez Sonarra, gdy próbuje utworzyć dedykowaną kategorię.
    Zwracamy "Ok.", aby Sonarr myślał, że kategoria została zapisana.
    """
    print(f"[*] Sonarr utworzył kategorię: {category} ze ścieżką: {savePath}")
    return "Ok."


# INDEXER
INDEXER_CAPS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <categories>
    <category id="5000" name="TV"/>
    <category id="5070" name="TV/Anime"/>
  </categories>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep"/>
  </searching>
</caps>
"""


@app.get("/indexer/api")
async def torznab_indexer(
    t: str = None, q: str = None, season: int = None, ep: int = None
):
    """
    To jest endpoint Indexera. Sonarr będzie tu wysyłał zapytania o odcinki.
    """

    # 1. Obsługa zapytania o możliwości (Caps)
    if t == "caps":
        return Response(content=INDEXER_CAPS_XML, media_type="application/xml")

    # 2. Obsługa wyszukiwania (Search)
    print(f"[*] Sonarr szuka: {q} Sezon: {season} Odcinek: {ep}")

    if q:
        anikoto_id = find_anikoto_id(q)

        res = requests.get(f"https://anikotoapi.site/series/{anikoto_id}")
        anime_data = json.loads(res.text)

        ep_url = (
            f'https://anikoto.cv/watch/{anime_data["data"]["anime"]["slug"]}/ep-{ep}'
        )

        print(f"{anikoto_id} | {ep_url}")
    else:
        anikoto_id = 0
        ep_url = "url"

    s_str = f"{season:02d}" if season is not None else "00"
    e_str = f"{ep:02d}" if ep is not None else "00"
    title_query = q or "Anime"

    now = datetime.datetime.now()
    rfc_date = email.utils.format_datetime(now)

    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")

    # Teraz używamy bezpiecznych stringów
    ET.SubElement(item, "title").text = (
        f"{title_query} - S{s_str}E{e_str} - {anikoto_id} - AniWatch"
    )
    ET.SubElement(item, "guid").text = f"anikoto_{anikoto_id}"
    ET.SubElement(item, "link").text = ep_url

    ET.SubElement(item, "pubDate").text = rfc_date

    ET.SubElement(
        item,
        "enclosure",
        {
            "url": ep_url,
            "length": "1500000000",  # Przykładowy rozmiar 1.5GB
            "type": "application/x-bittorrent",
        },
    )

    # Atrybuty Torznab (ważne dla rozpoznania sezonu/odcinka)
    # Wymaga importu namespace, ale dla uproszczenia Sonarr czyta też z tytułu

    xml_data = ET.tostring(root, encoding="utf-8")
    return Response(content=xml_data, media_type="application/xml")


if __name__ == "__main__":
    import uvicorn

    # Uruchomienie serwera na porcie 8080
    uvicorn.run(app, host="0.0.0.0", port=8080)
