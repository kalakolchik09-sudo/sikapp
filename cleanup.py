"""
Система очистки памяти и управления Pyrogram-клиентами.
Автоматически удаляет устаревшие pending-клиенты и логи.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class ClientManager:
    """Менеджер Pyrogram-клиентов с автоматической очисткой."""
    
    def __init__(self, ttl_minutes: int = 10):
        self.clients: dict[int, dict] = {}
        self.ttl = timedelta(minutes=ttl_minutes)
        self._lock = asyncio.Lock()
    
    async def add(self, telegram_id: int, client, phone: str, phone_hash: str):
        """Добавляет клиент с меткой времени."""
        async with self._lock:
            # Если старый клиент есть — отключаем
            if telegram_id in self.clients:
                old = self.clients[telegram_id].get("client")
                if old:
                    try:
                        await old.disconnect()
                    except Exception:
                        pass
            self.clients[telegram_id] = {
                "client": client,
                "phone": phone,
                "phone_hash": phone_hash,
                "created_at": datetime.now(timezone.utc),
            }
    
    async def get(self, telegram_id: int):
        """Возвращает клиент если он не истёк."""
        async with self._lock:
            data = self.clients.get(telegram_id)
            if not data:
                return None
            if datetime.now(timezone.utc) - data["created_at"] > self.ttl:
                await self._remove_unlocked(telegram_id)
                return None
            return data
    
    async def remove(self, telegram_id: int):
        """Удаляет клиент и отключает его."""
        async with self._lock:
            await self._remove_unlocked(telegram_id)
    
    async def _remove_unlocked(self, telegram_id: int):
        data = self.clients.pop(telegram_id, None)
        if data and data.get("client"):
            try:
                await data["client"].disconnect()
            except Exception:
                pass
    
    async def cleanup_loop(self, interval_seconds: int = 120):
        """Фоновая задача: чистит устаревшие клиенты каждые N секунд."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                now = datetime.now(timezone.utc)
                to_remove = []
                async with self._lock:
                    for telegram_id, data in list(self.clients.items()):
                        if now - data["created_at"] > self.ttl:
                            to_remove.append(telegram_id)
                    for telegram_id in to_remove:
                        await self._remove_unlocked(telegram_id)
                if to_remove:
                    logger.info(f"[CLEANUP] Удалено {len(to_remove)} устаревших клиентов")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CLEANUP] Ошибка: {e}")


client_manager = ClientManager(ttl_minutes=10)


async def periodic_db_cleanup(engine, session_factory):
    """Периодическая очистка старых записей в БД."""
    while True:
        try:
            await asyncio.sleep(3600)  # раз в час
            from sqlalchemy import delete, select
            from sqlalchemy.orm import Session
            
            # Импорт моделей внутри функции, чтобы избежать циклов
            import main as m
            
            with Session(engine) as db:
                # Удаляем завершённые задачи старше 7 дней
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                db.execute(
                    delete(m.BroadcastTask).where(
                        m.BroadcastTask.status.in_(["paused", "error"]),
                        m.BroadcastTask.created_at < cutoff,
                    )
                )
                # Удаляем старые сообщения поддержки (старше 30 дней)
                msg_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                db.execute(
                    delete(m.SupportMessage).where(m.SupportMessage.created_at < msg_cutoff)
                )
                db.commit()
                logger.info("[DB CLEANUP] Старые записи удалены")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[DB CLEANUP] Ошибка: {e}")
