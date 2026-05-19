import os
import time
from peewee import SqliteDatabase, Model, CharField, IntegerField, FloatField

# Ścieżka do bazy danych - upewnij się, że ten katalog jest zmapowany w TrueNAS
DATABASE_PATH = "/app/db/downloads.db"

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
    hash = CharField(unique=True, primary_key=True)
    title = CharField()
    name = CharField()
    anime_id = IntegerField()
    source_url = CharField()
    episode_embed_id = IntegerField()
    episode_num = IntegerField(default=0)
    season_num = IntegerField(default=0)
    size = IntegerField(default=0)
    amount_left = IntegerField(default=0)
    progress = FloatField(default=0.0)
    dlspeed = IntegerField(default=0)
    eta = IntegerField(default=0)
    state = CharField(default="downloading")
    save_path = CharField(default="/downloads")
    category = CharField(default="tv-sonarr")
    added_on = IntegerField(default=lambda: int(time.time()))

    error_message = CharField(null=True)

    class Meta:
        database = db


# Funkcja inicjalizująca bazę przy starcie aplikacji
def init_db():
    db.connect()
    db.create_tables([TorrentTask], safe=True)
    db.close()  # Zamykamy połączenie, Peewee otworzy je automatycznie kiedy trzeba
