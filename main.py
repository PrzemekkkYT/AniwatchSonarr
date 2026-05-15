from fastapi import FastAPI, Form, UploadFile, File, Response
from fastapi.responses import PlainTextResponse
from typing import Optional
import uuid
import time

app = FastAPI()

# Prosta baza danych w pamięci (w produkcji użyj SQLite/JSON)
downloads = {}


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


if __name__ == "__main__":
    import uvicorn

    # Uruchomienie serwera na porcie 8080
    uvicorn.run(app, host="0.0.0.0", port=8080)
