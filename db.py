import sqlite3
import time
from contextlib import closing

DB_PATH = "game.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(_conn()) as conn:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS players (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    hp INTEGER NOT NULL DEFAULT 100,
                    dmg_min INTEGER NOT NULL DEFAULT 8,
                    dmg_max INTEGER NOT NULL DEFAULT 16,
                    coins INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    weapon TEXT NOT NULL DEFAULT 'fists',
                    last_daily REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chat_wins (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    wins INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS inventory (
                    user_id INTEGER NOT NULL,
                    item TEXT NOT NULL,
                    PRIMARY KEY (user_id, item)
                );
                """
            )
            # миграция для базы, созданной старой версией бота
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
            if "weapon" not in cols:
                conn.execute(
                    "ALTER TABLE players ADD COLUMN weapon TEXT NOT NULL DEFAULT 'fists'"
                )
            if "last_daily" not in cols:
                conn.execute(
                    "ALTER TABLE players ADD COLUMN last_daily REAL NOT NULL DEFAULT 0"
                )


def register(user_id: int, name: str) -> dict:
    """Создаёт игрока, если его нет, и обновляет имя. Возвращает профиль."""
    with closing(_conn()) as conn:
        with conn:
            conn.execute(
                "INSERT INTO players (user_id, name) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
                (user_id, name),
            )
        row = conn.execute(
            "SELECT * FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row)


def record_result(chat_id: int, winner_id: int, loser_id: int,
                  win_coins: int, lose_coins: int):
    with closing(_conn()) as conn:
        with conn:
            conn.execute(
                "UPDATE players SET coins = coins + ?, wins = wins + 1 "
                "WHERE user_id = ?",
                (win_coins, winner_id),
            )
            conn.execute(
                "UPDATE players SET coins = coins + ?, losses = losses + 1 "
                "WHERE user_id = ?",
                (lose_coins, loser_id),
            )
            conn.execute(
                "INSERT INTO chat_wins (chat_id, user_id, wins) VALUES (?, ?, 1) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET wins = wins + 1",
                (chat_id, winner_id),
            )


def get_top(chat_id: int, limit: int = 10) -> list[dict]:
    with closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT p.name AS name, c.wins AS wins "
            "FROM chat_wins c JOIN players p ON p.user_id = c.user_id "
            "WHERE c.chat_id = ? ORDER BY c.wins DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- магазин ----------

def owned_items(user_id: int) -> set[str]:
    with closing(_conn()) as conn:
        rows = conn.execute(
            "SELECT item FROM inventory WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {r["item"] for r in rows} | {"fists"}


def buy_item(user_id: int, item: str, price: int) -> str:
    """Возвращает 'ok', 'owned' или 'poor'."""
    with closing(_conn()) as conn:
        with conn:
            has = conn.execute(
                "SELECT 1 FROM inventory WHERE user_id = ? AND item = ?",
                (user_id, item),
            ).fetchone()
            if has:
                return "owned"
            coins = conn.execute(
                "SELECT coins FROM players WHERE user_id = ?", (user_id,)
            ).fetchone()["coins"]
            if coins < price:
                return "poor"
            conn.execute(
                "UPDATE players SET coins = coins - ? WHERE user_id = ?",
                (price, user_id),
            )
            conn.execute(
                "INSERT INTO inventory (user_id, item) VALUES (?, ?)",
                (user_id, item),
            )
            return "ok"


def equip(user_id: int, item: str) -> bool:
    if item not in owned_items(user_id):
        return False
    with closing(_conn()) as conn:
        with conn:
            conn.execute(
                "UPDATE players SET weapon = ? WHERE user_id = ?", (item, user_id)
            )
    return True


def claim_daily(user_id: int, amount: int, cooldown: int):
    """Возвращает (получен ли бонус, сколько секунд осталось ждать)."""
    now = time.time()
    with closing(_conn()) as conn:
        with conn:
            last = conn.execute(
                "SELECT last_daily FROM players WHERE user_id = ?", (user_id,)
            ).fetchone()["last_daily"]
            if now - last < cooldown:
                return False, int(cooldown - (now - last))
            conn.execute(
                "UPDATE players SET coins = coins + ?, last_daily = ? "
                "WHERE user_id = ?",
                (amount, now, user_id),
            )
            return True, 0