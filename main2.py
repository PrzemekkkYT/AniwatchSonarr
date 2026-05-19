import asyncio
import datetime
import email
import hashlib
import html
import os
import re
from sqlite3 import OperationalError
import time
from typing import Optional
from fastapi import FastAPI, Form, Response
from fastapi.responses import PlainTextResponse
import urllib

import requests
import yt_dlp
from anikoto import find_anikoto_id, get_anime_by_id
from orm import TorrentTask, init_db
from headers import random_user_agent

import xml.etree.ElementTree as ET

app = FastAPI()
init_db()

GB = 1024 * 1024 * 1024


def get_source(episode_embed_id: str | int):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random_user_agent(),
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://megaplay.buzz",
        }
    )

    print("1")
    r_embed = session.get(f"https://megaplay.buzz/stream/s-2/{episode_embed_id}/sub")
    print("2")

    id_ = re.search(r" data-id=\"(\d+)\"", r_embed.text).group(1)

    print("3")
    r = session.get("https://megaplay.buzz/stream/getSources", params={"id": id_})
    print("4")
    r_data = r.json()

    if "sources" in r_data:
        return r_data.get("sources", {}).get("file", "")

    return None


LAST_DB_WRITE_TIME = 0.0


async def download_episode(task: TorrentTask):

    def progress_hook(d, task_hash: str):
        global LAST_DB_WRITE_TIME
        status = d.get("status")

        if status == "downloading":
            now = time.time()

            # Ograniczenie zapisu: wykonaj update tylko, jeśli minęła minimum 1 sekunda
            if now - LAST_DB_WRITE_TIME >= 1.0:
                # Bezpieczne wyciąganie rozmiaru pliku (dla m3u8 total_bytes często to None)
                total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded_bytes = d.get("downloaded_bytes", 0)

                # Obliczanie postępu i brakujących bajtów
                progress = (
                    float(downloaded_bytes / total_bytes) if total_bytes > 0 else 0.0
                )
                amount_left = total_bytes - downloaded_bytes

                # Wyciąganie prędkości i czasu do końca
                dlspeed = int(d.get("speed") or 0)
                eta = int(d.get("eta") or 0)

                try:
                    # Szybki update w bazie SQLite
                    TorrentTask.update(
                        progress=progress,
                        size=total_bytes,
                        amount_left=amount_left,
                        dlspeed=dlspeed,
                        eta=eta,
                        state="downloading",
                    ).where(TorrentTask.hash == task_hash).execute()

                    # Aktualizacja czasu ostatniego zapisu po udanej operacji
                    LAST_DB_WRITE_TIME = now

                except OperationalError:
                    # Jeśli baza była akurat zablokowana przez zapytanie info z Sonarra,
                    # ignorujemy to – kolejny hook za ułamek sekundy ponowi próbę.
                    pass

        elif status == "finished":
            # Status FINISHED musi zapisać się bezwarunkowo, bo to sygnał dla Sonarra
            attempts = 3
            while attempts > 0:
                try:
                    TorrentTask.update(
                        progress=1.0,
                        amount_left=0,
                        dlspeed=0,
                        eta=0,
                        state="uploading",  # Zielone światło dla Sonarra do importu
                    ).where(TorrentTask.hash == task_hash).execute()
                    break  # Udany zapis, przerywamy pętlę retry
                except OperationalError:
                    attempts -= 1
                    time.sleep(0.2)  # Krótki odpoczynek przed ponowną próbą zapisu

        elif status == "error":
            # Przechwytywanie błędów z yt-dlp
            error_msg = str(d.get("error", "Nienazwany błąd yt-dlp"))
            try:
                TorrentTask.update(state="error", error_message=error_msg).where(
                    TorrentTask.hash == task_hash
                ).execute()
            except OperationalError:
                pass

    ydl_opts = {
        "http_headers": {
            "referer": "https://megaplay.buzz/",
            "origin": "https://megaplay.buzz/",
            "user-agent": random_user_agent(),
            "priority": "u=1, i",
            "accept": "*/*",
        },
        "concurrent_fragment_downloads": 10,
        "hls_prefer_native": {"m3u8": "native", "dash": "native"},
        "nocheckcertificate": True,
        "ignoreerrors": True,
        "quiet": True,
        "noprogress": False,
        "no_warnings": True,
        "logger": None,
        "fixup": "detect_or_warn",
        "progress_hooks": [lambda d: progress_hook(d, task.hash)],
        "paths": {"temp": "temp", "home": task.save_path},
        "outtmpl": f"{task.name[:-4]}.%(ext)s",
        "generic": {
            "impersonate": "Edge",
        },
    }

    def run_yt_dlp():
        # Pobieramy źródło i odpalamy yt-dlp w synchronicznym bloku
        source_url = get_source(task.episode_embed_id)
        if source_url:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([source_url])
        else:
            # Zmień status zadania na error, jeśli nie udało się wyciągnąć m3u8
            TorrentTask.update(
                state="error", error_message="Nie udało się pobrać linku m3u8"
            ).where(TorrentTask.hash == task.hash).execute()

    # Uruchamiamy cały proces w osobnym wątku, pętla FastAPI jest wolna!
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_yt_dlp)


async def manage_queue():
    # Sprawdzamy, ile zadań aktualnie się pobiera
    current_downloads = (
        TorrentTask.select().where(TorrentTask.state == "downloading").count()
    )

    MAX_CONCURRENT_DOWNLOADS = 2

    if current_downloads < MAX_CONCURRENT_DOWNLOADS:
        slots_available = MAX_CONCURRENT_DOWNLOADS - current_downloads

        # Pobieramy najstarsze zakolejkowane zadania
        queued_tasks = (
            TorrentTask.select()
            .where(TorrentTask.state == "queued")
            .order_by(TorrentTask.added_on.asc())
            .limit(slots_available)
        )

        for task in queued_tasks:
            task.state = "downloading"
            task.save()
            print(f"[*] Uruchamiam pobieranie z kolejki dla: {task.name}")
            asyncio.create_task(download_episode_and_trigger_next(task))


async def download_episode_and_trigger_next(task: TorrentTask):
    # Wywołujemy Twój dotychczasowy proces pobierania
    await download_episode(task)

    # Kiedy download_episode się zakończy (niezależnie czy sukces, czy błąd),
    # wywołujemy manage_queue ponownie, aby wskoczyło następne zadanie!
    await manage_queue()


@app.post("/api/v2/auth/login", response_class=PlainTextResponse)
async def login(response: Response):
    response.set_cookie(key="SID", value="fake-cookie-12345")
    return "Ok."


@app.get("/api/v2/app/webapiVersion", response_class=PlainTextResponse)
async def web_api_version():
    return "2.8.2"


@app.post("/api/v2/torrents/add")
async def add_torrent(urls: str = Form(...)):
    parsed_url = urllib.parse.urlparse(urls)
    params = urllib.parse.parse_qs(parsed_url.query)

    payload: str = params["dn"][0]
    torrent_name, anikoto_id, single_ep = payload.split("|||")

    single_ep = single_ep.lower() == "true"

    # 1. Próbujemy dopasować format pojedynczego odcinka: S01E05
    # 1. Próbujemy dopasować format pojedynczego odcinka: S01E05
    match_ep = re.search(r"^(.*?)\s*-\s*S(\d+)E(\d+)", torrent_name)

    # 2. Próbujemy dopasować format całego sezonu: Season 01
    match_season = re.search(r"^(.*?)\s*-\s*Season\s*(\d+)", torrent_name)

    if match_ep:
        name = match_ep.group(1).strip()  # Wyciągnie: "Tytuł Anime"
        season = match_ep.group(2)  # Wyciągnie: "01"
        episode = match_ep.group(3)  # Wyciągnie: "05"
    elif match_season:
        name = match_season.group(1).strip()  # Wyciągnie: "Tytuł Anime"
        season = match_season.group(2)  # Wyciągnie: "01"
        episode = "1"  # Domyślny start, ale i tak pętla to nadpisze z API
    else:
        # Zabezpieczenie awaryjne
        name = torrent_name
        season, episode = "1", "1"

    # season, episode = season_episode.replace("S", "").split("E")

    found_anime = get_anime_by_id(anikoto_id)

    anime_data = found_anime.get("anime", {})

    episodes = found_anime.get("episodes", []) or []

    if single_ep:
        embeds: list[dict] = [
            {
                "embed_id": next(
                    (
                        ep.get("episode_embed_id")
                        for ep in episodes
                        if ep.get("number") == int(episode)
                    ),
                    None,
                ),
                "episode": int(episode),
            }
        ]
    else:
        embeds = [
            {"embed_id": ep.get("episode_embed_id"), "episode": ep.get("number")}
            for ep in episodes
        ]

    valid_embeds = [e for e in embeds if e.get("embed_id") is not None]

    slug = anime_data.get("slug", "unknown")

    title = html.unescape(anime_data.get("title", name))

    for embed in valid_embeds:
        episode_url = f"https://anikoto.cz/watch/{slug}/ep-{embed['episode']}"

        print(episode_url)

        task_hash = hashlib.md5(episode_url.encode("utf-8")).hexdigest()
        clean_episode_name = (
            f"{name} - S{int(season):02d}E{embed['episode']:02d} - 1080p - WEBDL.mp4"
        )

        ep_num = embed["episode"]

        existing_task = TorrentTask.get_or_none(TorrentTask.hash == task_hash)
        if existing_task:
            print(
                f"[-] Pomijam odcinek {ep_num} - istnieje już w bazie ze statusem: {existing_task.state}"
            )
            continue

        print(f"[*] Dodawanie NOWEGO zadania do bazy: {clean_episode_name}")

        # --- KROK 2: BEZPIECZNY GET_OR_CREATE ---
        # Szukamy TYLKO po hashu. Jeśli nie znajdzie, tworzy rekord z danymi z 'defaults'
        task, created = TorrentTask.get_or_create(
            hash=task_hash,
            defaults={
                "title": title,
                "name": clean_episode_name,
                "source_url": episode_url,
                "anime_id": anikoto_id,
                "episode_embed_id": embed["embed_id"],
                "episode_num": ep_num,
                "season_num": int(season),
                "save_path": f"/downloads/{anikoto_id}",
                "state": "queued",
            },
        )

    asyncio.create_task(manage_queue())

    return Response(content="Ok.", media_type="text/plain")


@app.get("/api/v2/torrents/info")
async def torrents_info(category: Optional[str] = None):
    info = list(TorrentTask.select().dicts())
    print(info)
    return info


@app.post("/api/v2/torrents/delete")
async def delete_torrent(hashes: str = Form(...)):
    hash_list = hashes.split("|")

    for hash in hash_list:
        task: TorrentTask | None = TorrentTask.get_or_none(TorrentTask.hash == hash)
        hash_path = f"{task.save_path}/{task.name}"

        if os.path.exists(hash_path):
            os.remove(hash_path)
        if task is not None:
            TorrentTask.delete().where(TorrentTask.hash == hash).execute()

    # TorrentTask.delete().where(TorrentTask.hash.in_(hash_list)).execute()
    return Response(content="Ok.", media_type="text/plain")


import os
import shutil
from fastapi import Form, Response, status
from peewee import OperationalError


@app.post("/api/v2/torrents/delete")
async def delete_torrent(hashes: str = Form(...), deleteFiles: str = Form("false")):
    # Sonarr może przysłać kilka hashów połączonych znakiem | (np. "hash1|hash2")
    hash_list = hashes.split("|")

    # Konwersja tekstowego "true"/"false" z formularza na Boolean Pythona
    should_delete_files = deleteFiles.lower() == "true"

    print(
        f"[*] Otrzymano żądanie usunięcia dla hashów: {hash_list} (Czyszczenie plików: {should_delete_files})"
    )

    for task_hash in hash_list:
        try:
            # 1. Pobieramy zadanie z bazy, żeby znać ścieżki i nazwę pliku
            task: TorrentTask | None = TorrentTask.get_or_none(
                TorrentTask.hash == task_hash
            )

            if not task:
                print(f"[!] Nie znaleziono zadania o hashu {task_hash} w bazie danych.")
                continue

            if should_delete_files:
                # Odtwarzamy dokładną ścieżkę do folderu odcinka: /downloads/{anime_id}
                anime_folder_path = task.save_path
                # Dokładna ścieżka do pliku wideo: /downloads/{anime_id}/{task.name}
                file_path = os.path.join(anime_folder_path, task.name)

                # Usuwamy fizyczny plik wideo, jeśli istnieje
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"[*] Pomyślnie usunięto plik wideo: {file_path}")

                # [OPCJONALNIE] Jeśli folder anime_id jest teraz pusty, sprzątamy go
                if os.path.exists(anime_folder_path) and not os.listdir(
                    anime_folder_path
                ):
                    os.rmdir(anime_folder_path)
                    print(f"[*] Usunięto pusty katalog serii: {anime_folder_path}")

            # 2. Usuwamy wpis z bazy SQLite za pomocą Peewee
            attempts = 3
            while attempts > 0:
                try:
                    task.delete_instance()
                    print(f"[*] Usunięto wpis zadania {task_hash} z bazy danych.")
                    break
                except OperationalError:
                    attempts -= 1
                    import time

                    time.sleep(0.2)

        except Exception as e:
            print(f"[!] Krytyczny błąd podczas usuwania zadania {task_hash}: {e}")

    # Sonarr oczekuje prostego tekstowego "Ok." lub pustej odpowiedzi z kodem 200
    return Response(
        content="Ok.", media_type="text/plain", status_code=status.HTTP_200_OK
    )


@app.get("/api/v2/app/preferences")
async def get_preferences():
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
        "dht": True,
        "dht_same_port": True,
    }


@app.get("/api/v2/torrents/categories")
async def get_categories():
    return {}


@app.post("/api/v2/torrents/createCategory", response_class=PlainTextResponse)
async def create_category(
    category: str = Form(...),
    savePath: str = Form(""),  # Sonarr może (ale nie musi) to wysłać
):
    print(f"[*] Sonarr utworzył kategorię: {category} ze ścieżką: {savePath}")
    return "Ok."


@app.get("/api/v2/torrents/files")
async def torrents_files(hash: str):
    task: TorrentTask | None = TorrentTask.get_or_none(TorrentTask.hash == hash)

    if not task:
        return Response(content="[]", media_type="application/json", status_code=404)

    files_list = [
        {
            "index": 0,
            "name": task.name,  # Wyjdzie np.: "123/Regardless of My...mp4"
            "size": task.size,
            "progress": task.progress,
            "priority": 1,
            "is_seed": True,
        }
    ]

    return files_list


@app.get("/api/v2/torrents/properties")
async def torrents_properties(hash: str):
    task: TorrentTask | None = TorrentTask.get_or_none(TorrentTask.hash == hash)

    if not task:
        return Response(content="{}", media_type="application/json", status_code=404)

    # qBittorrent zwraca w properties szczegółowe podsumowanie zadania
    properties = {
        "save_path": task.save_path,  # Dokładnie tak jak w /info!
        "creation_time": task.added_on,
        "total_size": task.size,
        "total_downloaded": (
            task.size if task.state == "uploading" else int(task.size * task.progress)
        ),
        "total_uploaded": 0,
        "total_wasted": 0,
        "dl_speed": task.dlspeed,
        "up_speed": 0,
        "seeding_time": 0,
        "time_elapsed": int(time.time()) - task.added_on,
        "share_ratio": 0,
        "addition_date": task.added_on,
        "completion_date": task.added_on if task.state == "uploading" else 0,
        "comment": "Downloaded via yt-dlp Custom Client",
        "pieces_num": 0,
        "pieces_have": 0,
    }

    return properties


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
    t: str = None,
    q: str = None,
    season: Optional[int] = None,
    ep: Optional[int] = None,
    cat: str = None,
):
    if t == "caps" or (season is None and ep is None and q is None and t != "tvsearch"):
        return Response(content=INDEXER_CAPS_XML, media_type="application/xml")

    chosen_cat = "5070"

    # Przygotowanie sezonu/odcinka
    s_str = (
        f"{season:02d}" if season is not None else "01"
    )  # Zmień na 01, Sonarr woli realne numery
    e_str = f"{ep:02d}" if ep is not None else "00"

    print(f"[*] Sonarr szuka: {q} Sezon: {season} Odcinek: {ep} Cat filter: {cat}")

    # Logika scrapowania API Anikoto
    if q is not None:
        try:
            anikoto_id = find_anikoto_id(q)
        except Exception as e:
            print(f"[!] Błąd pobierania danych z API Anikoto: {e}")
            anikoto_id = 0
    else:
        anikoto_id = 0

    if ep is not None:
        clean_name = f"{q} - S{s_str}E{e_str} - 1080p - WEBDL"
    else:
        clean_name = f"{q} - Season {s_str} - 1080p - WEBDL"

    payload = f"{clean_name}|||{anikoto_id}|||{'true' if ep is not None else 'false'}"

    encoded_payload = urllib.parse.quote(payload)

    ep_url = f"magnet:?xt=urn:btih:0000000000000000000000000000000000000000&dn={encoded_payload}&tr=http://a.b"

    now = datetime.datetime.now()
    rfc_date = email.utils.format_datetime(now)

    ET.register_namespace("torznab", "http://torznab.com/schemas/2015/feed")

    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "Aniwatch Torznab"

    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = clean_name
    ET.SubElement(item, "guid").text = f"anikoto_{anikoto_id}"
    ET.SubElement(item, "link").text = ep_url
    ET.SubElement(item, "pubDate").text = rfc_date
    ET.SubElement(item, "description").text = f"Episode S{s_str}E{e_str} - AniWatch"

    ET.SubElement(item, "category").text = chosen_cat
    ET.SubElement(
        item,
        "{http://torznab.com/schemas/2015/feed}attr",
        {"name": "category", "value": chosen_cat},
    )

    ET.SubElement(
        item,
        "enclosure",
        {"url": ep_url, "length": "0", "type": "application/x-bittorrent"},
    )

    xml_data = ET.tostring(root, encoding="utf-8")
    return Response(content=xml_data, media_type="application/xml")


if __name__ == "__main__":
    import uvicorn

    # Uruchom serwer z autoreloadem (debug), używając nazwy modułu aby reloader działał poprawnie
    uvicorn.run("main2:app", host="0.0.0.0", port=8080, reload=True, log_level="debug")
