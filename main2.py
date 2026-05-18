import datetime
import email
from typing import Optional
from fastapi import FastAPI, Form, Response
from fastapi.responses import PlainTextResponse
import urllib
from anikoto import find_anikoto_id, get_anime_by_id
from orm import TorrentTask, init_db

import xml.etree.ElementTree as ET

app = FastAPI()
init_db()

GB = 1024 * 1024 * 1024


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

    name, season_episode, _, _ = torrent_name.split(" - ")

    season, episode = season_episode.replace("S", "").split("E")

    anime_data = get_anime_by_id(anikoto_id).get("anime", {})

    slug = anime_data.get("slug", "unknown")
    episode_url = f"https://anikoto.cz/watch/{slug}/ep-{episode}"

    task = TorrentTask.create(
        hash=str(hash(episode_url)),
        name=torrent_name,
        source_url=episode_url,
        anime_id=anikoto_id,
        episode_num=int(episode),
        season_num=int(season),
    )

    return Response(content="Ok.", media_type="text/plain")


@app.get("/api/v2/torrents/info")
async def torrents_info(category: Optional[str] = None):
    return list(TorrentTask.select().where(TorrentTask.state == "downloading").dicts())


@app.post("/api/v2/torrents/delete")
async def delete_torrent(hashes: str = Form(...)):
    hash_list = hashes.split("|")

    TorrentTask.delete().where(TorrentTask.hash.in_(hash_list)).execute()
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
    ET.SubElement(item, "title").text = (
        f"{title_query} - S{s_str}E{e_str} - {anikoto_id} - AniWatch"
    )
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
