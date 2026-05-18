import os
import time
from peewee import SqliteDatabase, Model, CharField, IntegerField, FloatField

# Ścieżka do bazy danych - upewnij się, że ten katalog jest zmapowany w TrueNAS
DATABASE_PATH = "./downloads.db"

# Tworzymy katalog, jeśli nie istnieje
os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)

# Definicja bazy zoptymalizowanej pod kątem niskiego RAM-u i wysokiej współbieżności (WAL)
db = SqliteDatabase(
    DATABASE_PATH,
    pragmas={
        "journal_mode": "wal",  # Tryb WAL umożliwia jednoczesny odczyt i zapis
        "cache_size": -1024 * 32,  # Ograniczenie cache bazy do zaledwie 32MB RAM
        "synchronous": 1,  # Wyważone bezpieczeństwo zapisu i wydajność
        "timeout": 5000,  # W razie zajętości, poczekaj do 5 sekund przed błędem
    },
)


class TorrentTask(Model):
    # --- Pola wymagane przez Sonarra / qBittorrent ---
    hash = CharField(unique=True, primary_key=True)  # Unikalny identyfikator zadania
    name = CharField()  # Pełna nazwa (np. "Anime - S01E05 - 1080p")
    size = IntegerField(default=0)  # Całkowity rozmiar pliku w bajtach
    amount_left = IntegerField(default=0)  # Ile bajtów zostało do końca
    progress = FloatField(default=0.0)  # Postęp pobierania (0.0 do 1.0)
    dlspeed = IntegerField(default=0)  # Prędkość pobierania w bajtach/s
    eta = IntegerField(default=0)  # Czas do końca w sekundach
    state = CharField(
        default="downloading"
    )  # Status: downloading, completed, error, pausedDL
    save_path = CharField(default="/downloads")  # Gdzie Sonarr ma szukać gotowego pliku
    category = CharField(default="tv-sonarr")  # Kategoria z Sonarra
    added_on = IntegerField(
        default=lambda: int(time.time())
    )  # Znacznik czasu Unix dodania

    # --- Przydatne pola dodatkowe (Rozbudowa) ---
    source_url = CharField()  # Stały link do podstrony odcinka (np. na Anikoto)
    anime_id = IntegerField(
        null=True
    )  # ID anime z Twojego parsera (przydatne do logów)
    episode_num = IntegerField(
        null=True
    )  # Sam numer odcinka (np. 5) jako czysta liczba
    season_num = IntegerField(null=True)  # Numer sezonu (np. 1) jako czysta liczba
    error_message = CharField(null=True)  # Tu wpiszesz powód, jeśli yt-dlp się wyłoży

    class Meta:
        database = db


# Funkcja inicjalizująca bazę przy starcie aplikacji
def init_db():
    db.connect()
    db.create_tables([TorrentTask], safe=True)
    db.close()  # Zamykamy połączenie, Peewee otworzy je automatycznie kiedy trzeba
