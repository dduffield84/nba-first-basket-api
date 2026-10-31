import csv
import os
from pathlib import Path

from sqlalchemy import (
    Column, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

DATA_DIR = Path(__file__).parent / "data"
FIRST_BASKETS_CSV = DATA_DIR / "first_baskets.csv"
STARTERS_CSV = DATA_DIR / "game_starters.csv"

metadata = MetaData()

first_baskets = Table(
    "first_baskets", metadata,
    Column("game_id", String(32), primary_key=True),
    Column("game_date", String(10), nullable=False, index=True),
    Column("player_id", String(32), nullable=False, index=True),
    Column("player_name", String(100), nullable=False),
    Column("team", String(3), nullable=False),
    Column("source", String(200), nullable=False),
    Column("verified", Integer, nullable=False, default=0),
    Column("recorded_at", String(40), nullable=False),
    Column("season", String(7)),
    Column("home_team", String(3)),
    Column("away_team", String(3)),
    Column("tip_winner_team", String(3)),
    Column("data_flags", Text),
)

game_starters = Table(
    "game_starters", metadata,
    Column("game_id", String(32), primary_key=True),
    Column("player_id", String(32), primary_key=True),
    Column("player_name", String(100), nullable=False),
    Column("team", String(3), nullable=False, index=True),
    Column("game_date", String(10), nullable=False),
)

prediction_sets = Table(
    "prediction_sets", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", String(32), nullable=False, index=True),
    Column("game_date", String(10), nullable=False),
    Column("model", String(100), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("tip_time", String(40)),
)

prediction_items = Table(
    "prediction_items", metadata,
    Column("set_id", Integer, ForeignKey("prediction_sets.id"),
           primary_key=True),
    Column("player_id", String(32), primary_key=True),
    Column("player_name", String(100), nullable=False),
    Column("team", String(3), nullable=False),
    Column("probability", Float, nullable=False),
)

FIRST_BASKET_FIELDS = [
    c.name for c in first_baskets.columns
]
STARTER_FIELDS = [c.name for c in game_starters.columns]


def database_url():
    url = os.getenv("DATABASE_URL", "")
    if url:
        # Render and most hosts hand out postgres:// or postgresql://;
        # SQLAlchemy needs the driver named explicitly.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url
    path = Path(os.getenv("DB_PATH", "data/nba_api.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def make_engine(url=None):
    return create_engine(url or database_url(), pool_pre_ping=True)


def upsert(conn, table, rows, update=True):
    """Insert rows, replacing (or keeping) existing ones by primary key."""
    if not rows:
        return
    if conn.dialect.name == "postgresql":
        stmt = pg_insert(table)
    else:
        stmt = sqlite_insert(table)
    keys = [c.name for c in table.primary_key.columns]
    if update:
        stmt = stmt.on_conflict_do_update(
            index_elements=keys,
            set_={
                c.name: stmt.excluded[c.name]
                for c in table.columns if c.name not in keys
            },
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)
    conn.execute(stmt, rows)


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [
            {k: (v if v != "" else None) for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def seed_from_csv(conn):
    """Load the committed history so it survives ephemeral disks.

    Existing rows win, so edits made through the API are not overwritten.
    """
    baskets = read_csv(FIRST_BASKETS_CSV)
    for row in baskets:
        row["verified"] = int(row.get("verified") or 0)
    upsert(conn, first_baskets, baskets, update=False)
    upsert(conn, game_starters, read_csv(STARTERS_CSV), update=False)
    return len(baskets)
