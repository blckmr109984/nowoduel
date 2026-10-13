import asyncio
import itertools
import logging
import os
import random
import time
from pathlib import Path
from dataclasses import dataclass, field
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BotCommand, CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import db
import game

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_URL = "https://t.me/nowoduel"
CHANNEL_LINE = f'📢 <a href="{CHANNEL_URL}">t.me/nowoduel</a>'

WIN_COINS = 40
LOSE_COINS = 10
DAILY_COINS = 60
DAILY_COOLDOWN = 24 * 3600
DUEL_COOLDOWN = 20   # сек между вызовами одного игрока
PENDING_TIME = 60    # сколько ждём принятия вызова
ROUND_TIME = 20      # сек на выбор действия
MAX_ROUNDS = 3       # после этого побеждает тот, у кого больше HP
DUEL_HP = 40         # здоровье в дуэли (чтобы бой был коротким)

# Картинка для /start: положи файл start.jpg (или start.png) рядом с bot.py
START_IMAGES = [Path(__file__).parent / n for n in ("start.jpg", "start.png", "start.jpeg")]
start_file_id: str | None = None

router = Router()
last_duel: dict[int, float] = {}
duels: dict[int, "Duel"] = {}
busy: set[int] = set()
duel_ids = itertools.count(1)


@dataclass
class Duel:
    id: int
    chat_id: int
    p1: dict
    p2: dict
    message: Message | None = None
    active: bool = False
    resolving: bool = False
    round: int = 0
    choices: dict = field(default_factory=dict)
    pre: list = field(default_factory=list)
    last_text: str = ""


def display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


def channel_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📢 Канал бота", url=CHANNEL_URL)
    return b.as_markup()


def profile_text(p: dict) -> str:
    return (
        f"🧍 <b>{escape(p['name'])}</b>\n"
        f"🔫 В руках: {game.weapon_of(p)['name']}\n"
        f"🪙 Монеты: {p['coins']}\n"
        f"🏆 Победы: {p['wins']} | 💀 Поражения: {p['losses']}"
    )


async def safe_edit(message: Message, text: str, kb=None):
    try:
        await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass  # например, текст не изменился


def cleanup(d: Duel):
    duels.pop(d.id, None)
    busy.discard(d.p1["user_id"])
    busy.discard(d.p2["user_id"])


# ---------- дуэль ----------

def hp_block(d: Duel) -> str:
    return (
        f"❤️ {escape(d.p1['name'])} ({game.weapon_of(d.p1)['name']}): "
        f"{d.p1['cur_hp']}/{DUEL_HP}\n"
        f"❤️ {escape(d.p2['name'])} ({game.weapon_of(d.p2)['name']}): "
        f"{d.p2['cur_hp']}/{DUEL_HP}"
    )


def round_text(d: Duel) -> str:
    parts = [
        f"⚔️ <b>{escape(d.p1['name'])}</b> vs <b>{escape(d.p2['name'])}</b>",
        "",
        hp_block(d),
        "",
    ]
    if d.last_text:
        parts += [d.last_text, ""]
    s1 = "✅" if d.p1["user_id"] in d.choices else "⏳"
    s2 = "✅" if d.p2["user_id"] in d.choices else "⏳"
    parts.append(f"<b>Раунд {d.round}/{MAX_ROUNDS}</b>. Выбирайте действие ({ROUND_TIME} сек)")
    parts.append(f"{s1} {escape(d.p1['name'])}   {s2} {escape(d.p2['name'])}")
    parts.append("<i>Выбор тайный: соперник не видит, что ты нажал.</i>")
    return "\n".join(parts)


def action_kb(d: Duel):
    b = InlineKeyboardBuilder()
    for key, title in game.ACTIONS.items():
        b.button(text=title, callback_data=f"act:{d.id}:{key}")
    b.adjust(1)
    return b.as_markup()


async def start_round(d: Duel):
    d.round += 1
    d.choices.clear()
    await safe_edit(d.message, round_text(d), action_kb(d))
    asyncio.create_task(round_timeout(d, d.round))


async def round_timeout(d: Duel, round_no: int):
    await asyncio.sleep(ROUND_TIME)
    if d.id not in duels or d.round != round_no or d.resolving:
        return
    for p in (d.p1, d.p2):
        if p["user_id"] not in d.choices:
            d.choices[p["user_id"]] = random.choice(list(game.ACTIONS))
            d.pre.append(f"⏰ {escape(p['name'])} не успел, действие выбрано случайно")
    await resolve_round(d)


async def resolve_round(d: Duel):
    if d.resolving:
        return
    d.resolving = True
    try:
        a1 = d.choices[d.p1["user_id"]]
        a2 = d.choices[d.p2["user_id"]]
        dmg_to_2, dmg_to_1, lines = game.resolve(d.p1, d.p2, a1, a2)
        d.p2["cur_hp"] = max(0, d.p2["cur_hp"] - dmg_to_2)
        d.p1["cur_hp"] = max(0, d.p1["cur_hp"] - dmg_to_1)
        d.last_text = "\n".join(d.pre + lines)
        d.pre = []

        over = (
            d.p1["cur_hp"] <= 0
            or d.p2["cur_hp"] <= 0
            or d.round >= MAX_ROUNDS
        )
        if over:
            await finish(d)
        else:
            await start_round(d)
    finally:
        d.resolving = False


async def finish(d: Duel):
    p1, p2 = d.p1, d.p2
    note = ""
    if p1["cur_hp"] == p2["cur_hp"]:
        winner = random.choice([p1, p2])
        note = "\n<i>Ничья по здоровью, победителя выбрал жребий.</i>"
    else:
        winner = p1 if p1["cur_hp"] > p2["cur_hp"] else p2
    loser = p2 if winner is p1 else p1

    db.record_result(d.chat_id, winner["user_id"], loser["user_id"],
                     WIN_COINS, LOSE_COINS)
    cleanup(d)

    text = (
        f"⚔️ <b>{escape(p1['name'])}</b> vs <b>{escape(p2['name'])}</b>\n\n"
        f"{hp_block(d)}\n\n"
        f"{d.last_text}\n\n"
        f"🏆 Победил <b>{escape(winner['name'])}</b> (+{WIN_COINS} 🪙)\n"
        f"Утешительный приз для {escape(loser['name'])}: +{LOSE_COINS} 🪙"
        f"{note}\n\n"
        f"🛒 Оружие в /shop\n{CHANNEL_LINE}"
    )
    await safe_edit(d.message, text, channel_kb())


async def expire_pending(d: Duel):
    await asyncio.sleep(PENDING_TIME)
    if d.id in duels and not d.active:
        cleanup(d)
        await safe_edit(d.message, "⌛ Вызов не принят, бой отменён.")


# ---------- команды ----------

async def send_start_image(message: Message):
    """Шлёт приветственную картинку, если она лежит рядом с ботом."""
    global start_file_id
    try:
        if start_file_id:
            await message.answer_photo(start_file_id)
            return
        for path in START_IMAGES:
            if path.exists():
                sent = await message.answer_photo(FSInputFile(path))
                start_file_id = sent.photo[-1].file_id  # дальше шлём без загрузки
                return
    except TelegramBadRequest:
        logging.exception("Не удалось отправить стартовую картинку")


@router.message(Command("start"))
async def cmd_start(message: Message):
    p = db.register(message.from_user.id, display_name(message.from_user))
    await send_start_image(message)
    await message.answer(
        "Добро пожаловать на арену! Твой персонаж готов.\n\n"
        + profile_text(p)
        + "\n\nВ чате ответь на чьё-то сообщение командой /duel, "
        "чтобы вызвать человека на бой. Бой короткий: 3 раунда, в каждом "
        "тайно выбираешь действие. Быстрый удар бьёт мощный, мощный пробивает "
        "блок, блок отражает быстрый.\n\n"
        "🛒 /shop: оружие за монеты\n"
        "🎁 /daily: ежедневный бонус\n"
        "🏆 /top: рейтинг чата\n\n"
        + CHANNEL_LINE,
        reply_markup=channel_kb(),
    )


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    p = db.register(message.from_user.id, display_name(message.from_user))
    await message.answer(profile_text(p) + "\n\n" + CHANNEL_LINE)


@router.message(Command("daily"))
async def cmd_daily(message: Message):
    user = message.from_user
    db.register(user.id, display_name(user))
    ok, left = db.claim_daily(user.id, DAILY_COINS, DAILY_COOLDOWN)
    if ok:
        text = f"🎁 Ежедневный бонус: +{DAILY_COINS} 🪙. Загляни в /shop!"
    else:
        text = (
            f"Бонус уже получен. Следующий через "
            f"{left // 3600} ч {(left % 3600) // 60} мин."
        )
    await message.answer(text + "\n\n" + CHANNEL_LINE)


@router.message(Command("top"))
async def cmd_top(message: Message):
    rows = db.get_top(message.chat.id)
    if not rows:
        await message.answer(
            "В этом чате ещё никто не дрался. Начни с /duel.\n\n" + CHANNEL_LINE
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {escape(r['name'])}: {r['wins']} поб.")
    await message.answer(
        "🏆 <b>Топ бойцов чата</b>\n\n" + "\n".join(lines) + "\n\n" + CHANNEL_LINE
    )


@router.message(Command("duel"))
async def cmd_duel(message: Message):
    target_msg = message.reply_to_message
    if not target_msg or not target_msg.from_user:
        await message.answer("Ответь командой /duel на сообщение того, кого вызываешь.")
        return

    me, enemy = message.from_user, target_msg.from_user
    if enemy.is_bot:
        await message.answer("С ботами не дерёмся, у них нет чувства юмора.")
        return
    if enemy.id == me.id:
        await message.answer("Драться с самим собой нельзя. Найди оппонента.")
        return
    if me.id in busy or enemy.id in busy:
        await message.answer("Кто-то из вас уже в бою, дождитесь конца.")
        return

    now = time.time()
    wait = DUEL_COOLDOWN - (now - last_duel.get(me.id, 0))
    if wait > 0:
        await message.answer(f"Отдышись ещё {int(wait) + 1} сек.")
        return
    last_duel[me.id] = now

    p1 = db.register(me.id, display_name(me))
    p2 = db.register(enemy.id, display_name(enemy))
    p1["cur_hp"] = p2["cur_hp"] = DUEL_HP

    d = Duel(id=next(duel_ids), chat_id=message.chat.id, p1=p1, p2=p2)
    duels[d.id] = d
    busy.update({me.id, enemy.id})

    kb = InlineKeyboardBuilder()
    kb.button(text="⚔️ Принять вызов", callback_data=f"acc:{d.id}")
    d.message = await message.answer(
        f"🔔 <b>{escape(p1['name'])}</b> вызывает на дуэль "
        f"<b>{escape(p2['name'])}</b>!\n"
        f"{escape(p2['name'])}, прими вызов кнопкой ниже.",
        reply_markup=kb.as_markup(),
    )
    asyncio.create_task(expire_pending(d))


@router.callback_query(F.data.startswith("acc:"))
async def on_accept(cb: CallbackQuery):
    d = duels.get(int(cb.data.split(":")[1]))
    if not d or d.active:
        await cb.answer("Этот вызов уже недействителен")
        return
    if cb.from_user.id != d.p2["user_id"]:
        await cb.answer("Этот вызов адресован не тебе", show_alert=True)
        return
    d.active = True
    await cb.answer()
    await start_round(d)


@router.callback_query(F.data.startswith("act:"))
async def on_action(cb: CallbackQuery):
    _, did, action = cb.data.split(":")
    d = duels.get(int(did))
    if not d or not d.active:
        await cb.answer("Этот бой уже закончен")
        return
    uid = cb.from_user.id
    if uid not in (d.p1["user_id"], d.p2["user_id"]):
        await cb.answer("Ты не участвуешь в этом бою", show_alert=True)
        return
    if d.resolving:
        await cb.answer("Раунд уже считается")
        return
    if uid in d.choices:
        await cb.answer("Ты уже выбрал, жди соперника")
        return

    d.choices[uid] = action
    await cb.answer(f"Ты выбрал: {game.ACTIONS[action]}")
    if len(d.choices) == 2:
        await resolve_round(d)
    else:
        await safe_edit(d.message, round_text(d), action_kb(d))


# ---------- магазин ----------

def shop_view(p: dict, owned: set, uid: int):
    equipped = p["weapon"] if p["weapon"] in game.WEAPONS else "fists"
    lines = [
        "🛒 <b>Магазин оружия</b>",
        f"🪙 Монеты: {p['coins']}",
        f"В руках: {game.WEAPONS[equipped]['name']}",
        "",
    ]
    b = InlineKeyboardBuilder()
    for key, w in game.WEAPONS.items():
        stats = f"+{w['dmg']} урон" if w["dmg"] else "базовый урон"
        if w["crit"]:
            stats += f", +{int(w['crit'] * 100)}% крит"
        price = "" if key == "fists" else f" · {w['price']} 🪙"
        mark = ""
        if key == equipped:
            mark = " (в руках)"
        elif key in owned and key != "fists":
            mark = " ✅"
        lines.append(f"{w['name']}: {stats}{price}{mark}")

        if key == equipped:
            continue
        if key in owned:
            b.button(text=f"Надеть {w['name']}", callback_data=f"shop:eq:{key}:{uid}")
        else:
            b.button(
                text=f"Купить {w['name']} · {w['price']} 🪙",
                callback_data=f"shop:buy:{key}:{uid}",
            )
    b.button(text="📢 Канал бота", url=CHANNEL_URL)
    b.adjust(1)
    lines += ["", "Монеты дают за победы в дуэлях и за /daily.", CHANNEL_LINE]
    return "\n".join(lines), b.as_markup()


@router.message(Command("shop"))
async def cmd_shop(message: Message):
    user = message.from_user
    p = db.register(user.id, display_name(user))
    text, kb = shop_view(p, db.owned_items(user.id), user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("shop:"))
async def on_shop(cb: CallbackQuery):
    _, action, key, uid = cb.data.split(":")
    uid = int(uid)
    if cb.from_user.id != uid:
        await cb.answer("Это не твой магазин. Открой свой: /shop", show_alert=True)
        return
    if key not in game.WEAPONS:
        await cb.answer("Такого товара нет")
        return

    w = game.WEAPONS[key]
    db.register(uid, display_name(cb.from_user))
    if action == "buy":
        res = db.buy_item(uid, key, w["price"])
        if res == "poor":
            await cb.answer("Не хватает монет 🪙", show_alert=True)
            return
        if res == "owned":
            await cb.answer("Уже куплено")
        else:
            db.equip(uid, key)
            await cb.answer(f"Куплено и надето: {w['name']}")
    elif action == "eq":
        if db.equip(uid, key):
            await cb.answer(f"В руках: {w['name']}")
        else:
            await cb.answer("Сначала купи это оружие", show_alert=True)
            return
    else:
        await cb.answer()
        return

    p = db.register(uid, display_name(cb.from_user))
    text, kb = shop_view(p, db.owned_items(uid), uid)
    await safe_edit(cb.message, text, kb)


async def main():
    if not TOKEN:
        raise SystemExit("Не найден BOT_TOKEN. Создай файл .env по образцу .env.example")
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML, link_preview_is_disabled=True
        ),
    )
    await bot.set_my_commands([
        BotCommand(command="duel", description="Вызвать на дуэль (ответом на сообщение)"),
        BotCommand(command="shop", description="Магазин оружия"),
        BotCommand(command="daily", description="Ежедневный бонус"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="top", description="Рейтинг чата"),
    ])
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())