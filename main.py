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
async def torrents_info():
    """
    Sonarr co kilkanaście sekund pyta o listę torrentów, aby zaktualizować pasek postępu.
    """
    formatted_list = []
    for tid, data in downloads.items():
        # Udajemy format danych qBittorrent
        formatted_list.append(
            {
                "hash": tid,
                "name": data["name"],
                "size": data["size"],
                "progress": data["progress"],  # Wartość od 0.0 do 1.0
                "status": data["status"],
                "save_path": data["save_path"],
                "added_on": data["added_on"],
                "upspeed": 0,
                "dlspeed": 1024 * 1024,  # Udajemy 1MB/s
            }
        )
    return formatted_list


@app.post("/api/v2/torrents/delete")
async def delete_torrent(hashes: str = Form(...)):
    """Wywoływane, gdy Sonarr usuwa zadanie z listy."""
    for h in hashes.split("|"):
        if h in downloads:
            del downloads[h]
    return "Ok."


if __name__ == "__main__":
    import uvicorn

    # Uruchomienie serwera na porcie 8080
    uvicorn.run(app, host="0.0.0.0", port=8080)
