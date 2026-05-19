import asyncio
import datetime
import email
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

    payload = params["dn"][0]
    torrent_name, anikoto_id = payload.split("|||")

    match = re.search(r"^(.*?)\s*-\s*S(\d+)E(\d+)", torrent_name)

    if match:
        name = match.group(1).strip()  # Wyciągnie: "Kaguya-sama - Love is War"
        season = match.group(2)  # Wyciągnie: "01"
        episode = match.group(3)  # Wyciągnie: "05"
    else:
        # Zabezpieczenie awaryjne, gdyby format był zupełnie inny
        name = torrent_name
        season, episode = "0", "0"

    # season, episode = season_episode.replace("S", "").split("E")

    found_anime = get_anime_by_id(anikoto_id)

    anime_data = found_anime.get("anime", {})

    episodes = found_anime.get("episodes", []) or []
    embed_id = next(
        (
            ep.get("episode_embed_id")
            for ep in episodes
            if ep.get("number") == int(episode)
        ),
        None,
    )

    slug = anime_data.get("slug", "unknown")
    episode_url = f"https://anikoto.cz/watch/{slug}/ep-{episode}"

    title = html.unescape(anime_data.get("title", name))

    task, created = TorrentTask.get_or_create(
        hash=str(hash(episode_url)),
        title=title,
        # name=f"{title} S{season}E{episode}.mp4",
        name=f"{torrent_name}.mp4",
        source_url=episode_url,
        anime_id=anikoto_id,
        episode_embed_id=embed_id,
        episode_num=int(episode),
        season_num=int(season),
        save_path=f"/downloads/{anikoto_id}",
    )

    print(type(task))
    print(task)
    print(task.source_url)

    asyncio.create_task(download_episode(task))

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
    # 1. Obsługa zapytania o możliwości (Caps)
    # Podczas testu Sonarr pyta o caps. Czasem przysyła t=caps, czasem t=tvsearch bez parametrów.
    if t == "caps" or (season is None and ep is None and q is None and t != "tvsearch"):
        return Response(content=INDEXER_CAPS_XML, media_type="application/xml")

    # Dla bezpieczeństwa testów w Sonarrze - zawsze zwracamy kategorię Anime (5070)
    # Ponieważ Sonarr dla Anime szuka tylko w kategorii 5070 (lub pokrewnych z sekcji Anime)
    chosen_cat = "5070"

    # Przygotowanie sezonu/odcinka
    s_str = (
        f"{season:02d}" if season is not None else "01"
    )  # Zmień na 01, Sonarr woli realne numery
    e_str = f"{ep:02d}" if ep is not None else "01"
    title_query = q or "Test Anime Episode"

    print(f"[*] Sonarr szuka: {q} Sezon: {season} Odcinek: {ep} Cat filter: {cat}")

    # Logika scrapowania Twojego API Anikoto
    if q is not None:
        try:
            anikoto_id = find_anikoto_id(q)
            # res = requests.get(
            #     f"https://anikotoapi.site/series/{anikoto_id}", timeout=5
            # )
            # anime_data = res.json()
            # slug = anime_data["data"]["anime"]["slug"]
            # real_url = f"https://anikoto.cz/watch/{slug}/ep-{e_str}"
        except Exception as e:
            print(f"[!] Błąd pobierania danych z API Anikoto: {e}")
            # real_url = "https://anikoto.cz/watch/unknown-anime/ep-01"
            anikoto_id = 0
    else:
        # To wykona się podczas TESTU połączenia w Sonarrze
        anikoto_id = 0
        # real_url = "https://anikoto.cz/watch/test-anime/ep-01"

    clean_name = f"{q} - S{s_str}E{e_str} - 1080p - WEBDL"

    # payload = f"{clean_name}|||{real_url}"
    payload = f"{clean_name}|||{anikoto_id}"

    encoded_payload = urllib.parse.quote(payload)

    ep_url = f"magnet:?xt=urn:btih:0000000000000000000000000000000000000000&dn={encoded_payload}&tr=http://pusty-tracker.com/announce"
    # ep_url = f"magnet:?xt=urn:btih:0000000000000000000000000000000000000000&dn={anikoto_id}&tr=http://pusty-tracker.com/announce"

    now = datetime.datetime.now()
    rfc_date = email.utils.format_datetime(now)

    ET.register_namespace("torznab", "http://torznab.com/schemas/2015/feed")

    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "Aniwatch Torznab"

    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = clean_name
    # ET.SubElement(item, "title").text = (
    #     f"{title_query} - S{s_str}E{e_str} - {anikoto_id} - AniWatch"
    # )
    ET.SubElement(item, "guid").text = f"anikoto_{anikoto_id}"
    ET.SubElement(item, "link").text = ep_url
    ET.SubElement(item, "pubDate").text = rfc_date
    ET.SubElement(item, "description").text = f"Episode S{s_str}E{e_str} - AniWatch"

    # Te dwa elementy MUSZĄ mieć wartość "5070", aby Sonarr (skonfigurowany pod Anime) zaakceptował wynik
    ET.SubElement(item, "category").text = chosen_cat
    ET.SubElement(
        item,
        "{http://torznab.com/schemas/2015/feed}attr",
        {"name": "category", "value": chosen_cat},
    )

    ET.SubElement(
        item,
        "enclosure",
        {"url": ep_url, "length": "1500000000", "type": "application/x-bittorrent"},
    )

    xml_data = ET.tostring(root, encoding="utf-8")
    return Response(content=xml_data, media_type="application/xml")


if __name__ == "__main__":
    import uvicorn

    # Uruchom serwer z autoreloadem (debug), używając nazwy modułu aby reloader działał poprawnie
    uvicorn.run("main2:app", host="0.0.0.0", port=8080, reload=True, log_level="debug")
