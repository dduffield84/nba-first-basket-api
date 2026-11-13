"""Edge Intelligence shared prediction ledger: tables for NFL, NBA and CFB.

Three append-only tables:

- ledger_entries: one row per model prediction, paper bet or real bet.
  The three kinds are stored side by side and never mixed in reports.
- ledger_prices: price snapshots for an entry (open, bet, current, close).
- ledger_results: settlements for an entry. A correction is a new row; the
  latest row counts and every earlier row stays.

Database triggers refuse UPDATE and DELETE on all three tables, on SQLite
and on Postgres, so a losing pick can never be edited away.
"""
from sqlalchemy import (
    Column, DDL, Float, ForeignKey, Integer, String, Table, Text,
    UniqueConstraint, event,
)

from db import metadata

SPORTS = ("nfl", "nba", "cfb")
KINDS = ("prediction", "paper_bet", "real_bet")
PRICE_TYPES = ("open", "bet", "current", "close")
OUTCOMES = ("win", "loss", "push", "void")

# Market vocabulary. A market's settlement rule is part of its name, so two
# markets that books settle differently can never share prices or grades.
MARKETS = {
    "nba": {
        "nba_first_fg": "First made field goal (free throws do not count)",
        "nba_first_point": "First point of the game (free throws count)",
        "nba_team_first_score": "Team to score first",
        "nba_tip": "Opening tip winner",
        "nba_player_prop": "Player stat over/under",
        "nba_three_pointers": "Player three-pointers made over/under",
    },
    "nfl": {
        "nfl_anytime_td": "Player scores a touchdown at any time",
        "nfl_first_td": "Player scores the first touchdown",
        "nfl_2plus_td": "Player scores 2 or more touchdowns",
        "nfl_3plus_td": "Player scores 3 or more touchdowns",
        "nfl_player_prop": "Player stat over/under",
        "nfl_spread": "Game spread",
        "nfl_total": "Game total",
        "nfl_moneyline": "Game moneyline",
    },
    "cfb": {
        "cfb_spread": "Game spread",
        "cfb_total": "Game total",
        "cfb_moneyline": "Game moneyline",
    },
}

# Which NBA first-scorer market each book's "first basket" price settles on,
# from the Stage 1 audit. Confirm against each book's current house rules.
# A price for the other market from these books is refused. Books not
# listed are accepted but flagged as an unconfirmed settlement rule.
NBA_FIRST_SCORER_BOOK_MARKET = {
    "fanduel": "nba_first_point",
    "draftkings": "nba_first_fg",
    "betmgm": "nba_first_fg",
}

ledger_entries = Table(
    "ledger_entries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Idempotency key from the caller: re-sending the same record is a no-op.
    Column("entry_key", String(200), nullable=False, unique=True),
    Column("kind", String(12), nullable=False, index=True),
    Column("sport", String(3), nullable=False, index=True),
    Column("market", String(40), nullable=False, index=True),
    Column("event_id", String(64)),
    Column("event_date", String(10), nullable=False, index=True),
    Column("event_label", String(40), nullable=False),
    Column("home", String(40)),
    Column("away", String(40)),
    Column("event_start_utc", String(40)),
    Column("selection", String(120), nullable=False),
    Column("selection_id", String(64)),
    Column("side", String(12)),
    Column("line", Float),
    Column("model_prob", Float),
    Column("market_prob", Float),
    Column("model_version", String(60), nullable=False, index=True),
    Column("decision", String(4)),
    Column("odds_american", Float),
    Column("book", String(40)),
    Column("price_at", String(40)),
    Column("stake_units", Float, nullable=False, default=0.0),
    Column("predicted_at", String(40), nullable=False),
    Column("recorded_at", String(40), nullable=False),
    # 1 when predicted_at is before event_start_utc, 0 when not, NULL when
    # the start time is unknown. Only 1 counts toward performance.
    Column("pregame_verified", Integer),
    Column("source", String(200), nullable=False),
    Column("source_ref", String(200)),
    Column("linked_entry_id", Integer, ForeignKey("ledger_entries.id")),
    Column("data_flags", Text),
)

ledger_prices = Table(
    "ledger_prices", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entry_id", Integer, ForeignKey("ledger_entries.id"),
           nullable=False, index=True),
    Column("price_type", String(8), nullable=False),
    Column("odds_american", Float),
    Column("line", Float),
    Column("book", String(40)),
    Column("observed_at", String(40), nullable=False),
    Column("recorded_at", String(40), nullable=False),
    Column("source", String(200), nullable=False),
    Column("data_flags", Text),
    UniqueConstraint("entry_id", "price_type", "book", "observed_at",
                     name="uq_ledger_price_snapshot"),
)

ledger_results = Table(
    "ledger_results", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entry_id", Integer, ForeignKey("ledger_entries.id"),
           nullable=False, index=True),
    Column("outcome", String(5), nullable=False),
    Column("actual_value", Float),
    Column("settled_at", String(40), nullable=False),
    Column("recorded_at", String(40), nullable=False),
    Column("source", String(200), nullable=False),
    Column("note", Text),
)

LEDGER_TABLES = (ledger_entries, ledger_prices, ledger_results)


def _append_only(table):
    name = table.name
    sqlite = [
        DDL(f"CREATE TRIGGER IF NOT EXISTS {name}_no_update BEFORE UPDATE "
            f"ON {name} BEGIN SELECT RAISE(ABORT, "
            f"'{name} is append-only'); END").execute_if(dialect="sqlite"),
        DDL(f"CREATE TRIGGER IF NOT EXISTS {name}_no_delete BEFORE DELETE "
            f"ON {name} BEGIN SELECT RAISE(ABORT, "
            f"'{name} is append-only'); END").execute_if(dialect="sqlite"),
    ]
    postgres = [
        DDL("CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger "
            "AS $$ BEGIN RAISE EXCEPTION '%% is append-only', TG_TABLE_NAME; "
            "END; $$ LANGUAGE plpgsql").execute_if(dialect="postgresql"),
        DDL(f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE "
            f"ON {name} FOR EACH ROW EXECUTE FUNCTION ledger_append_only()"
            ).execute_if(dialect="postgresql"),
    ]
    for ddl in sqlite + postgres:
        event.listen(table, "after_create", ddl)


for _t in LEDGER_TABLES:
    _append_only(_t)


def market_sport(market):
    for sport, markets in MARKETS.items():
        if market in markets:
            return sport
    return None


def book_key(book):
    return "".join(ch for ch in (book or "").lower() if ch.isalnum())


def nba_book_check(market, book):
    """None if the price may be stored, else the reason it is refused.

    Also returns a flag for books whose settlement rule is unconfirmed."""
    if market not in ("nba_first_fg", "nba_first_point") or not book:
        return None, None
    rule = NBA_FIRST_SCORER_BOOK_MARKET.get(book_key(book))
    if rule is None:
        return None, "settlement_rule_unconfirmed"
    if rule != market:
        return (f"{book} settles first-scorer bets on {rule}, "
                f"not {market}"), None
    return None, None
