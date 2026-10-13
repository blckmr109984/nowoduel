import random
from html import escape

ACTIONS = {
    "strike": "⚡ Быстрый удар",
    "heavy": "🔨 Мощный удар",
    "block": "🛡 Блок",
}
# Камень-ножницы-бумага: быстрый бьёт мощный, мощный пробивает блок, блок отражает быстрый
BEATS = {"strike": "heavy", "heavy": "block", "block": "strike"}
MULT = {"strike": 1.0, "heavy": 1.6, "block": 0.6}  # у блока это контрудар
CRIT_CHANCE = 0.2

# dmg: прибавка к урону, crit: прибавка к шансу крита
WEAPONS = {
    "fists":   {"name": "👊 Кулаки",    "price": 0,    "dmg": 0, "crit": 0.0},
    "knife":   {"name": "🔪 Нож",       "price": 80,   "dmg": 2, "crit": 0.0},
    "pistol":  {"name": "🔫 Пистолет",  "price": 200,  "dmg": 4, "crit": 0.0},
    "shotgun": {"name": "🧨 Обрез",     "price": 400,  "dmg": 6, "crit": 0.0},
    "rifle":   {"name": "🪖 Автомат",   "price": 700,  "dmg": 5, "crit": 0.05},
    "sniper":  {"name": "🎯 Снайперка", "price": 1200, "dmg": 8, "crit": 0.10},
}

WIN_PHRASES = {
    "strike": "{w} успевает ударить раньше и срывает замах {l}",
    "heavy": "{w} пробивает защиту {l}",
    "block": "{w} отбивает удар и контратакует {l}",
}


def weapon_of(p: dict) -> dict:
    return WEAPONS.get(p.get("weapon", "fists"), WEAPONS["fists"])


def _hit(p: dict, action: str):
    w = weapon_of(p)
    base = random.randint(p["dmg_min"], p["dmg_max"]) + w["dmg"]
    dmg = int(base * MULT[action])
    crit = random.random() < CRIT_CHANCE + w["crit"]
    if crit:
        dmg *= 2
    return max(1, dmg), crit


def _crit(c: bool) -> str:
    return " 💥КРИТ" if c else ""


def resolve(p1: dict, p2: dict, a1: str, a2: str):
    """Один раунд. Возвращает (урон от p1 по p2, урон от p2 по p1, строки лога)."""
    n1, n2 = escape(p1["name"]), escape(p2["name"])
    d1 = d2 = 0
    lines = [f"{n1}: {ACTIONS[a1]}  |  {n2}: {ACTIONS[a2]}"]

    if a1 == a2 == "block":
        lines.append("🤝 Оба ушли в защиту, никто не пострадал")
    elif a1 == a2:
        d1, c1 = _hit(p1, a1)
        d2, c2 = _hit(p2, a2)
        lines.append(
            f"💢 Удары сошлись! {n1} наносит {d1}{_crit(c1)}, "
            f"{n2} наносит {d2}{_crit(c2)}"
        )
    elif BEATS[a1] == a2:
        d1, c = _hit(p1, a1)
        phrase = WIN_PHRASES[a1].format(w=n1, l=n2)
        lines.append(f"{phrase} (-{d1}{_crit(c)})")
    else:
        d2, c = _hit(p2, a2)
        phrase = WIN_PHRASES[a2].format(w=n2, l=n1)
        lines.append(f"{phrase} (-{d2}{_crit(c)})")

    return d1, d2, lines