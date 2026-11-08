"""
Парсер NFT-подарков Телеграма: ищет подарки, выставленные на продажу,
показывает юзернейм владельца и делит по цене в TON.

Установка:  pip install telethon httpx
Переменные окружения: API_ID, API_HASH, BOT_TOKEN, CHAT_ID
Первый запуск попросит номер телефона и код (вход аккаунта-парсера).
"""
import asyncio
import os
import sqlite3

import httpx
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]  # твой id, бот пришлёт сюда
INTERVAL = 300  # пауза между полными обходами, сек
SESSION = os.environ.get("SESSION")  # строка сессии (для облака)
ONCE = os.environ.get("ONCE") == "1"  # один обход и выход (для GitHub Actions)
MAX_PRICE = 100  # выше 100 TON не показываем


def category(p):
    if p < 7:
        return "🟢 Дешёвые (0–6 TON)"
    if p <= 15:
        return "🟡 Средние (7–15 TON)"
    if p <= MAX_PRICE:
        return "🔴 Дорогие (15–100 TON)"
    return None


def ton_price(g):
    """Цена перепродажи в TON (или None, если только в звёздах)."""
    for a in getattr(g, "resell_amount", None) or []:
        if type(a).__name__ == "StarsTonAmount":
            return a.amount / 1e9
    return None


def owner_info(g, users):
    uid = getattr(getattr(g, "owner_id", None), "user_id", None)
    u = users.get(uid)
    if u:
        if getattr(u, "username", None):
            return "@" + u.username
        return f"{u.first_name or 'без имени'} (без юзернейма, id {u.id})"
    return getattr(g, "owner_name", None) or "скрыт"


async def notify(http, text):
    await http.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
    )


async def scan(client, db, http, silent):
    res = await client(functions.payments.GetStarGiftsRequest(hash=0))
    for base in getattr(res, "gifts", []):
        if not getattr(base, "upgrade_stars", None):
            continue  # у подарка нет NFT-версии
        offset = ""
        while True:
            try:
                r = await client(
                    functions.payments.GetResaleStarGiftsRequest(
                        gift_id=base.id, offset=offset, limit=100, sort_by_price=True
                    )
                )
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
                continue
            users = {u.id: u for u in r.users}
            stop = False
            for g in r.gifts:
                p = ton_price(g)
                if p is None:
                    continue
                cat = category(p)
                if cat is None:
                    stop = True  # отсортировано по цене, дальше дороже
                    break
                row = db.execute("SELECT price FROM seen WHERE id=?", (g.id,)).fetchone()
                if row and abs(row[0] - p) < 1e-9:
                    continue
                db.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (g.id, p))
                db.commit()
                if not silent:
                    await notify(
                        http,
                        f"{cat}\n{g.title} #{g.num}\n"
                        f"Цена: {p:.2f} TON\n"
                        f"Владелец: {owner_info(g, users)}\n"
                        f"https://t.me/nft/{g.slug}",
                    )
            if stop or not getattr(r, "next_offset", None):
                break
            offset = r.next_offset
            await asyncio.sleep(1)
        await asyncio.sleep(1)


async def main():
    db = sqlite3.connect("gifts.db")
    db.execute("CREATE TABLE IF NOT EXISTS seen (id INTEGER PRIMARY KEY, price REAL)")
    first = db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    session = StringSession(SESSION) if SESSION else "parser_session"
    async with TelegramClient(session, API_ID, API_HASH) as client:
        async with httpx.AsyncClient(timeout=30) as http:
            while True:
                # первый обход молча заполняет базу, чтобы не засыпать тебя
                await scan(client, db, http, silent=first)
                first = False
                if ONCE:
                    return
                await asyncio.sleep(INTERVAL)


asyncio.run(main())
