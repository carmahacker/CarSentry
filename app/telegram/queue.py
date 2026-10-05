import asyncio
from dataclasses import dataclass
from pathlib import Path
from app.db.database import SessionLocal
from app.db.models import Event
from .client import TelegramClient

@dataclass
class Job:
    event_id: int
    path: str
    caption: str

class TelegramQueue:
    def __init__(self):
        self.q=asyncio.Queue(maxsize=100)
        self.task=None
        self.stop_event=asyncio.Event()

    async def start(self):
        self.task=asyncio.create_task(self._run())

    async def stop(self):
        self.stop_event.set()
        if self.task: await self.task

    async def put(self, job):
        try: self.q.put_nowait(job)
        except asyncio.QueueFull: pass

    async def _run(self):
        while not self.stop_event.is_set() or not self.q.empty():
            try: job=await asyncio.wait_for(self.q.get(), timeout=0.5)
            except asyncio.TimeoutError: continue
            error=""
            ok=False
            for delay in (0,1,2):
                if delay: await asyncio.sleep(delay)
                try:
                    await TelegramClient().send_photo(Path(job.path), job.caption)
                    ok=True; break
                except Exception as e: error=str(e)
            with SessionLocal() as db:
                ev=db.get(Event, job.event_id)
                if ev:
                    ev.telegram_sent=ok
                    ev.telegram_error="" if ok else error
                    db.commit()
            self.q.task_done()
