import re
import json
import requests
from bs4 import BeautifulSoup


def find_anikoto_id(q: str):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }
    )

    url = f"https://anikoto.cz/ajax/anime/search?keyword={q.replace(' ', '+')}"

    ru = session.get(url)

    html = json.loads(ru.text)["result"]["html"]
    soup = BeautifulSoup(html, "html.parser")

    items = soup.find_all("a", class_="item")

    animes = []

    for item in items:
        r = session.get(item.get("href"))

        search = re.search(rf"https://anikoto.cz/anime/getinfo/(\d+)", r.text)

        match = re.search(r"<div>Episodes:\s*<span>\s*(\d+)", r.text)

        if match:
            # Wyciągamy zawartość pierwszej grupy i konwertujemy na int
            episodes_count = int(match.group(1))
            print(f"[+] Znaleziono liczbę odcinków: {episodes_count}")
        else:
            episodes_count = (
                12  # Bezpieczny fallback, gdyby struktura HTML się zmieniła
            )
            print("[!] Nie udało się dopasować regexa, używam fallbacku: 12")

        animes.append(
            {
                "id": search.group(1),
                "title": item.find("div", class_="name").text,
                "episodes": episodes_count,
            }
        )

    return animes


def get_anime_by_id(anikoto_id: str):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }
    )

    url = f"https://anikotoapi.site/series/{anikoto_id}"

    response = session.get(url)
    return response.json().get("data", {})


def search_anikoto(q: str):
    url = f"https://anikoto.cz/ajax/anime/search?keyword={q.replace(' ', '+')}"

    headers = {
        "Host": "anikoto.cz",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "pl,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": f"https://anikoto.cz/filter?keyword={q.replace(' ', '+')}",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
        "Alt-Used": "anikoto.cz",
        "Cookie": "country_code=DE; prefered_server_id=e54; prefered_server_type=sub",
    }

    response = requests.get(url, headers=headers)
    html = json.loads(response.text)["result"]["html"]
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("a", class_="item")

    return item.get("href")
