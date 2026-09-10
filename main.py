import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, BigInteger, String, Boolean, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from pyrogram import Client
from pyrogram.errors import FloodWait, SessionPasswordNeeded, PasswordHashInvalid, PhoneCodeInvalid, PhoneCodeExpired
from pyrogram.enums import ChatType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./licenses.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
CRYPTO_WEBHOOK_SECRET = os.getenv("CRYPTO_WEBHOOK_SECRET", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DEV_TELEGRAM_ID = os.getenv("DEV_TELEGRAM_ID", "")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "964442694"))
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

PLANS = {
    "week": {"name": "7 дней", "days": 7, "price": "3.00"},
    "month": {"name": "30 дней", "days": 30, "price": "8.00"},
    "quarter": {"name": "90 дней", "days": 90, "price": "20.00"},
    "forever": {"name": "Навсегда", "days": None, "price": "49.00"},
}

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(index=True)
    plan: Mapped[str] = mapped_column(String(30))
    invoice_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    license_key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ManualLicenseKey(Base):
    __tablename__ = "manual_license_keys"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    duration_days: Mapped[int] = mapped_column(Integer)
    used_by: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(index=True)
    message: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptances"
    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    session_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BroadcastTask(Base):
    __tablename__ = "broadcast_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_ids: Mapped[str] = mapped_column(Text, default="[]")
    messages: Mapped[str] = mapped_column(Text, default="[]")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    safe_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_text_index: Mapped[int] = mapped_column(Integer, default=-1)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    groups_count: Mapped[int] = mapped_column(Integer, default=0)
    current_cycle: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)
app = FastAPI(title="License mini app")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# --- Pyrogram clients store (временное хранение между запросами) ---
pending_clients: dict[int, Client] = {}
pending_phone: dict[int, str] = {}
pending_hash: dict[int, str] = {}


class Checkout(BaseModel):
    plan: str


class KeyActivation(BaseModel):
    key: str


class ManualKeyCreate(BaseModel):
    duration_days: int


class SupportMessage(BaseModel):
    message: str


class SupportReply(BaseModel):
    message: str


class PhoneRequest(BaseModel):
    phone: str


class CodeRequest(BaseModel):
    code: str


class PasswordRequest(BaseModel):
    password: str


class BroadcastStart(BaseModel):
    account_ids: list[int]
    messages: list[str]
    interval_minutes: int
    safe_mode: bool


def telegram_user(init_data: str | None) -> int:
    if not init_data:
        if DEV_TELEGRAM_ID:
            return int(DEV_TELEGRAM_ID)
        raise HTTPException(401, "Откройте приложение через Telegram.")
    if not BOT_TOKEN:
        raise HTTPException(503, "BOT_TOKEN не настроен.")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    signature = values.pop("hash", "")
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Недействительные данные Telegram.")
    try:
        return int(json.loads(values["user"])["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "Не найден пользователь Telegram.")


async def crypto(method: str, payload: dict) -> dict:
    if not CRYPTO_PAY_TOKEN:
        raise HTTPException(503, "CRYPTO_PAY_TOKEN не настроен.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://pay.crypt.bot/api/{method}", json=payload,
            headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
        )
    data = response.json()
    if not response.is_success or not data.get("ok"):
        raise HTTPException(502, "Не удалось создать или проверить счёт Crypto Pay.")
    return data["result"]


def key() -> str:
    return "LIC-" + base64.b32encode(secrets.token_bytes(12)).decode().rstrip("=")


async def send_key(telegram_id: int, license_key: str, expires_at: datetime | None):
    if not BOT_TOKEN:
        return
    until = "бессрочно" if expires_at is None else expires_at.strftime("%d.%m.%Y")
    text = f"✅ Оплата получена!\n\nВаш ключ: <code>{license_key}</code>\nДействует: {until}"
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": telegram_id, "text": text, "parse_mode": "HTML"})


def accepted(telegram_id: int) -> bool:
    with Session(engine) as db:
        return db.get(TermsAcceptance, telegram_id) is not None


def require_terms(telegram_id: int):
    if not accepted(telegram_id):
        raise HTTPException(403, "Сначала примите пользовательское соглашение.")


def require_admin(telegram_id: int):
    if telegram_id != ADMIN_TELEGRAM_ID:
        raise HTTPException(403, "Раздел доступен только администратору.")


def has_active_license(telegram_id: int) -> bool:
    if telegram_id == ADMIN_TELEGRAM_ID:
        return True
    with Session(engine) as db:
        order = db.scalar(
            select(Order).where(Order.telegram_id == telegram_id, Order.status == "paid").order_by(Order.created_at.desc())
        )
        if not order:
            return False
        if order.expires_at is None:
            return True
        return order.expires_at > datetime.now(timezone.utc)


def require_license(telegram_id: int):
    if not has_active_license(telegram_id):
        raise HTTPException(403, "Нужен активный лицензионный ключ.")


async def activate(invoice_id: str) -> Order | None:
    invoice = await crypto("getInvoices", {"invoice_ids": invoice_id})
    items = invoice.get("items", [])
    if not items or items[0].get("status") != "paid":
        return None
    with Session(engine) as db:
        order = db.scalar(select(Order).where(Order.invoice_id == invoice_id))
        if not order:
            return None
        if order.status == "paid":
            return order
        plan = PLANS[order.plan]
        order.status = "paid"
        order.license_key = key()
        order.expires_at = None if plan["days"] is None else datetime.now(timezone.utc) + timedelta(days=plan["days"])
        db.commit()
        db.refresh(order)
    await send_key(order.telegram_id, order.license_key, order.expires_at)
    return order


# ============================================================
# БАЗОВЫЕ ЭНДПОИНТЫ (из Бота №1)
# ============================================================

@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/plans")
def plans():
    return [{"id": ident, **plan, "currency": "USDT"} for ident, plan in PLANS.items()]


@app.get("/api/me")
def me(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    with Session(engine) as db:
        orders = db.scalars(select(Order).where(Order.telegram_id == telegram_id, Order.status == "paid").order_by(Order.created_at.desc())).all()
        latest = orders[0] if orders else None
        return {
            "telegram_id": telegram_id,
            "terms_accepted": db.get(TermsAcceptance, telegram_id) is not None,
            "license_key": latest.license_key if latest else None,
            "expires_at": latest.expires_at if latest else None,
            "purchases": len(orders),
            "is_admin": telegram_id == ADMIN_TELEGRAM_ID,
        }


@app.post("/api/terms/accept")
def accept_terms(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    with Session(engine) as db:
        if not db.get(TermsAcceptance, telegram_id):
            db.add(TermsAcceptance(telegram_id=telegram_id))
            db.commit()
    return {"ok": True}


@app.get("/api/admin/summary")
def admin_summary(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_admin(telegram_id)
    with Session(engine) as db:
        orders = db.scalars(select(Order)).all()
        paid = [order for order in orders if order.status == "paid"]
        return {
            "orders_total": len(orders),
            "paid_total": len(paid),
            "users_total": len({order.telegram_id for order in orders}),
            "revenue_usdt": f"{sum(float(PLANS.get(order.plan, {}).get('price', 0)) for order in paid):.2f}",
        }


@app.post("/api/admin/keys")
def create_manual_key(body: ManualKeyCreate, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_admin(telegram_id)
    if body.duration_days != -1 and body.duration_days < 1:
        raise HTTPException(422, "Укажите число дней или -1 для бессрочного ключа.")
    new_key = key()
    with Session(engine) as db:
        db.add(ManualLicenseKey(key=new_key, duration_days=body.duration_days))
        db.commit()
    return {"key": new_key, "duration_days": body.duration_days}


@app.get("/api/admin/users")
def admin_users(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_admin(telegram_id)
    with Session(engine) as db:
        ids = {row[0] for row in db.execute(select(Order.telegram_id)).all()}
        ids.update(row[0] for row in db.execute(select(TermsAcceptance.telegram_id)).all())
        result = []
        for user_id in sorted(ids, reverse=True)[:100]:
            latest = db.scalar(select(Order).where(Order.telegram_id == user_id, Order.status == "paid").order_by(Order.created_at.desc()))
            result.append({"telegram_id": user_id, "license_key": latest.license_key if latest else None, "expires_at": latest.expires_at if latest else None})
    return result


@app.get("/api/admin/tickets")
def admin_tickets(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_admin(telegram_id)
    with Session(engine) as db:
        tickets = db.scalars(select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(30)).all()
        return [{"id": ticket.id, "telegram_id": ticket.telegram_id, "message": ticket.message, "status": ticket.status, "created_at": ticket.created_at} for ticket in tickets]


@app.post("/api/support")
async def create_ticket(body: SupportMessage, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Напишите сообщение для поддержки.")
    with Session(engine) as db:
        ticket = SupportTicket(telegram_id=telegram_id, message=message)
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
    if BOT_TOKEN:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": ADMIN_TELEGRAM_ID, "text": f"🆘 Обращение #{ticket.id} от <code>{telegram_id}</code>\n\n{message}", "parse_mode": "HTML"})
    return {"ticket_id": ticket.id}


@app.post("/api/admin/tickets/{ticket_id}/reply")
async def reply_ticket(ticket_id: int, body: SupportReply, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_admin(telegram_id)
    reply = body.message.strip()
    if not reply:
        raise HTTPException(422, "Введите ответ.")
    with Session(engine) as db:
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(404, "Обращение не найдено.")
        ticket.status = "answered"
        recipient = ticket.telegram_id
        db.commit()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": recipient, "text": f"💬 <b>Ответ поддержки</b>\n\n{reply}", "parse_mode": "HTML"})
        response.raise_for_status()
    return {"ok": True}


@app.post("/api/keys/activate")
def activate_key(body: KeyActivation, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_terms(telegram_id)
    supplied = body.key.strip().upper()
    with Session(engine) as db:
        manual = db.get(ManualLicenseKey, supplied)
        if manual:
            if manual.used_by:
                raise HTTPException(409, "Этот ключ уже активирован.")
            expiry = None if manual.duration_days == -1 else datetime.now(timezone.utc) + timedelta(days=manual.duration_days)
            db.add(Order(telegram_id=telegram_id, plan="manual", invoice_id=f"manual-{supplied}", status="paid", license_key=supplied, expires_at=expiry))
            manual.used_by = telegram_id
            db.commit()
            return {"key": supplied, "expires_at": expiry}
        order = db.scalar(select(Order).where(Order.license_key == supplied, Order.status == "paid"))
        if not order:
            raise HTTPException(404, "Ключ не найден или ещё не оплачен.")
        order.telegram_id = telegram_id
        db.commit()
        return {"key": order.license_key, "expires_at": order.expires_at}


@app.post("/api/checkout")
async def checkout(body: Checkout, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_terms(telegram_id)
    if body.plan not in PLANS:
        raise HTTPException(422, "Неизвестный тариф.")
    plan = PLANS[body.plan]
    invoice = await crypto("createInvoice", {
        "asset": "USDT", "amount": plan["price"], "description": f"Лицензия: {plan['name']}",
        "payload": json.dumps({"telegram_id": telegram_id, "plan": body.plan}),
    })
    with Session(engine) as db:
        db.add(Order(telegram_id=telegram_id, plan=body.plan, invoice_id=str(invoice["invoice_id"])))
        db.commit()
    pay_url = invoice.get("mini_app_invoice_url") or invoice.get("bot_invoice_url") or invoice.get("pay_url")
    if not pay_url:
        raise HTTPException(502, "Crypto Pay не вернул ссылку на счёт.")
    return {"invoice_id": str(invoice["invoice_id"]), "pay_url": pay_url}


@app.get("/api/orders/{invoice_id}")
async def order_status(invoice_id: str, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    with Session(engine) as db:
        order = db.scalar(select(Order).where(Order.invoice_id == invoice_id, Order.telegram_id == telegram_id))
        if not order:
            raise HTTPException(404, "Счёт не найден.")
    activated = await activate(invoice_id)
    if activated:
        return {"status": "paid", "key": activated.license_key, "expires_at": activated.expires_at}
    return {"status": "pending"}


# ============================================================
# АККАУНТЫ (из Бота №2)
# ============================================================

@app.get("/api/accounts")
def list_accounts(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    with Session(engine) as db:
        accounts = db.scalars(select(Account).where(Account.telegram_id == telegram_id).order_by(Account.created_at.desc())).all()
        return [{"id": a.id, "phone": a.phone_number, "created_at": a.created_at} for a in accounts]


@app.post("/api/accounts/send-code")
async def send_code(body: PhoneRequest, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    if not API_ID or not API_HASH:
        raise HTTPException(503, "API_ID/API_HASH не настроены.")
    phone = body.phone.strip()
    if not phone.startswith("+"):
        raise HTTPException(422, "Номер должен начинаться с +.")
    client = Client(f"u_{telegram_id}_{secrets.token_hex(4)}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    try:
        await client.connect()
        sent = await client.send_code(phone)
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise HTTPException(400, f"Ошибка отправки кода: {e}")
    pending_clients[telegram_id] = client
    pending_phone[telegram_id] = phone
    pending_hash[telegram_id] = sent.phone_code_hash
    return {"ok": True}


@app.post("/api/accounts/verify-code")
async def verify_code(body: CodeRequest, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    client = pending_clients.get(telegram_id)
    phone = pending_phone.get(telegram_id)
    phash = pending_hash.get(telegram_id)
    if not client or not phone or not phash:
        raise HTTPException(400, "Сессия истекла. Начните заново.")
    try:
        await client.sign_in(phone, phash, body.code.strip())
    except SessionPasswordNeeded:
        return {"need_password": True}
    except (PhoneCodeInvalid, PhoneCodeExpired) as e:
        raise HTTPException(400, f"Неверный код: {e}")
    except Exception as e:
        raise HTTPException(400, f"Ошибка: {e}")
    session_string = await client.export_session_string()
    try:
        await client.disconnect()
    except Exception:
        pass
    with Session(engine) as db:
        db.add(Account(telegram_id=telegram_id, phone_number=phone, session_string=session_string))
        db.commit()
    pending_clients.pop(telegram_id, None)
    pending_phone.pop(telegram_id, None)
    pending_hash.pop(telegram_id, None)
    return {"ok": True}


@app.post("/api/accounts/verify-password")
async def verify_password(body: PasswordRequest, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    client = pending_clients.get(telegram_id)
    phone = pending_phone.get(telegram_id)
    if not client or not phone:
        raise HTTPException(400, "Сессия истекла. Начните заново.")
    try:
        await client.check_password(body.password)
    except PasswordHashInvalid:
        raise HTTPException(400, "Неверный пароль 2FA.")
    except Exception as e:
        raise HTTPException(400, f"Ошибка: {e}")
    session_string = await client.export_session_string()
    try:
        await client.disconnect()
    except Exception:
        pass
    with Session(engine) as db:
        db.add(Account(telegram_id=telegram_id, phone_number=phone, session_string=session_string))
        db.commit()
    pending_clients.pop(telegram_id, None)
    pending_phone.pop(telegram_id, None)
    pending_hash.pop(telegram_id, None)
    return {"ok": True}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    with Session(engine) as db:
        acc = db.get(Account, account_id)
        if not acc or acc.telegram_id != telegram_id:
            raise HTTPException(404, "Аккаунт не найден.")
        db.delete(acc)
        db.commit()
    return {"ok": True}


# ============================================================
# РАССЫЛКА (из Бота №2 с новой логикой)
# ============================================================

async def get_user_groups(client: Client):
    groups = []
    try:
        async for dialog in client.get_dialogs():
            if dialog.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                groups.append({"id": dialog.chat.id, "title": dialog.chat.title or "Без названия"})
    except Exception as e:
        logger.error(f"get_user_groups error: {e}")
    return groups


def pick_next_text(messages: list[str], last_index: int, safe_mode: bool) -> tuple[str, int]:
    """Обычный режим: всегда messages[0]. Безопасный: случайный, не равный last_index."""
    if not messages:
        return "", -1
    if not safe_mode or len(messages) == 1:
        return messages[0], 0
    available = [i for i in range(len(messages)) if i != last_index]
    chosen = random.choice(available)
    return messages[chosen], chosen


def next_interval_seconds(base_minutes: int, safe_mode: bool) -> int:
    base = base_minutes * 60
    if not safe_mode:
        return base
    variation = int(base * 0.2)
    lo = max(1800, base - variation)
    hi = min(7200, base + variation)
    return random.randint(lo, hi)


async def broadcast_worker(task_id: int):
    with Session(engine) as db:
        task = db.get(BroadcastTask, task_id)
        if not task:
            return
        account_ids = json.loads(task.account_ids or "[]")
        messages = json.loads(task.messages or "[]")
        telegram_id = task.telegram_id
        safe_mode = task.safe_mode
        interval_minutes = task.interval_minutes
        last_index = task.last_text_index

    clients: list[Client] = []
    with Session(engine) as db:
        for acc_id in account_ids:
            acc = db.get(Account, acc_id)
            if acc and acc.session_string:
                client = Client(
                    f"b_{acc_id}_{task_id}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=acc.session_string,
                    in_memory=True,
                )
                clients.append(client)

    if not clients:
        with Session(engine) as db:
            t = db.get(BroadcastTask, task_id)
            if t:
                t.status = "error"
                db.commit()
        return

    try:
        for client in clients:
            try:
                await client.start()
            except Exception as e:
                logger.error(f"client start error: {e}")

        while True:
            with Session(engine) as db:
                current = db.get(BroadcastTask, task_id)
                if not current or current.status != "active":
                    break

            message_text, new_index = pick_next_text(messages, last_index, safe_mode)
            last_index = new_index

            cycle_sent = 0
            groups_total = 0

            for client in clients:
                groups = await get_user_groups(client)
                groups_total = max(groups_total, len(groups))
                for group in groups:
                    with Session(engine) as db:
                        check = db.get(BroadcastTask, task_id)
                        if not check or check.status != "active":
                            break
                    try:
                        await client.send_message(group["id"], message_text)
                        cycle_sent += 1
                        with Session(engine) as db:
                            t = db.get(BroadcastTask, task_id)
                            if t:
                                t.sent_count += 1
                                t.current_cycle += 0 if cycle_sent > 1 else 1
                                t.groups_count = groups_total
                                t.last_text_index = last_index
                                t.last_sent_at = datetime.now(timezone.utc)
                                db.commit()
                        await asyncio.sleep(1)
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except Exception:
                        continue

            with Session(engine) as db:
                t = db.get(BroadcastTask, task_id)
                if not t or t.status != "active":
                    break

            wait_seconds = next_interval_seconds(interval_minutes, safe_mode)
            for _ in range(wait_seconds // 5):
                with Session(engine) as db:
                    t = db.get(BroadcastTask, task_id)
                    if not t or t.status != "active":
                        break
                await asyncio.sleep(5)
            else:
                continue
            break

    except Exception as e:
        logger.error(f"broadcast_worker error: {e}")
    finally:
        for client in clients:
            try:
                await client.stop()
            except Exception:
                pass


@app.get("/api/broadcast/tasks")
def broadcast_tasks(x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    with Session(engine) as db:
        tasks = db.scalars(select(BroadcastTask).where(BroadcastTask.telegram_id == telegram_id).order_by(BroadcastTask.created_at.desc()).limit(20)).all()
        return [{
            "id": t.id, "status": t.status, "safe_mode": t.safe_mode,
            "interval_minutes": t.interval_minutes, "current_cycle": t.current_cycle,
            "sent_count": t.sent_count, "groups_count": t.groups_count,
            "created_at": t.created_at,
        } for t in tasks]


@app.post("/api/broadcast/start")
async def broadcast_start(body: BroadcastStart, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)

    if not body.account_ids:
        raise HTTPException(422, "Выберите хотя бы один аккаунт.")
    texts = [m.strip() for m in body.messages if m.strip()]
    if body.safe_mode:
        if len(texts) < 3:
            raise HTTPException(422, "Безопасный режим требует 3 текста.")
        texts = texts[:3]
    else:
        if len(texts) < 1:
            raise HTTPException(422, "Введите текст сообщения.")
        texts = texts[:1]
    if body.interval_minutes < 30 or body.interval_minutes > 120:
        raise HTTPException(422, "Интервал должен быть 30-120 минут.")

    with Session(engine) as db:
        owned = db.scalars(select(Account).where(Account.telegram_id == telegram_id, Account.id.in_(body.account_ids))).all()
        if len(owned) != len(body.account_ids):
            raise HTTPException(403, "Один или несколько аккаунтов не принадлежат вам.")
        task = BroadcastTask(
            telegram_id=telegram_id,
            account_ids=json.dumps(body.account_ids),
            messages=json.dumps(texts),
            interval_minutes=body.interval_minutes,
            safe_mode=body.safe_mode,
            status="active",
            last_text_index=-1,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id

    asyncio.create_task(broadcast_worker(task_id))
    return {"task_id": task_id}


@app.post("/api/broadcast/stop/{task_id}")
def broadcast_stop(task_id: int, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    with Session(engine) as db:
        task = db.get(BroadcastTask, task_id)
        if not task or task.telegram_id != telegram_id:
            raise HTTPException(404, "Задача не найдена.")
        task.status = "paused"
        db.commit()
    return {"ok": True}


@app.get("/api/broadcast/status/{task_id}")
def broadcast_status(task_id: int, x_telegram_init_data: str | None = Header(default=None)):
    telegram_id = telegram_user(x_telegram_init_data)
    require_license(telegram_id)
    with Session(engine) as db:
        task = db.get(BroadcastTask, task_id)
        if not task or task.telegram_id != telegram_id:
            raise HTTPException(404, "Задача не найдена.")
        return {
            "id": task.id, "status": task.status, "safe_mode": task.safe_mode,
            "current_cycle": task.current_cycle, "sent_count": task.sent_count,
            "groups_count": task.groups_count, "last_text_index": task.last_text_index,
            "last_sent_at": task.last_sent_at,
        }


# ============================================================
# WEBHOOKS
# ============================================================

@app.post("/api/crypto/webhook/{secret}")
async def crypto_webhook(secret: str, request: Request):
    if not CRYPTO_WEBHOOK_SECRET or not hmac.compare_digest(secret, CRYPTO_WEBHOOK_SECRET):
        raise HTTPException(404, "Не найдено")
    data = await request.json()
    if data.get("update_type") == "invoice_paid":
        invoice_id = str(data.get("payload", {}).get("invoice_id", ""))
        if invoice_id:
            await activate(invoice_id)
    return {"ok": True}


@app.post("/api/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if not TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(secret, TELEGRAM_WEBHOOK_SECRET):
        raise HTTPException(404, "Не найдено")
    update = await request.json()
    message = update.get("message", {})
    if not message.get("text", "").startswith("/start") or not BOT_TOKEN:
        return {"ok": True}
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return {"ok": True}
    text = ("👋 <b>Добро пожаловать!</b>\n\n"
            "Здесь можно ознакомиться с сервисом, принять соглашение и выбрать лицензию. "
            "Нажмите кнопку ниже, чтобы открыть приложение.")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "https").split(",")[0].strip()
    web_app_url = f"{forwarded_proto}://{forwarded_host}" if forwarded_host else WEBAPP_URL
    if not web_app_url.startswith("https://"):
        web_app_url = WEBAPP_URL
    if not web_app_url or not web_app_url.startswith("https://"):
        logger.error("No valid public HTTPS WEBAPP_URL is configured.")
        return {"ok": True}
    keyboard = {"inline_keyboard": [[{"text": "🚀 Запустить", "web_app": {"url": web_app_url}}]]}
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})
    return {"ok": True}