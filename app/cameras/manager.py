import asyncio
from app.db.database import SessionLocal
from app.db.models import Camera, AppSettings
from app.cameras.worker import CameraWorker

class CameraManager:
    def __init__(self,telegram):
        self.telegram=telegram; self.workers={}; self.task=None; self.stop_event=asyncio.Event()

    async def start(self): self.task=asyncio.create_task(self._run())
    async def stop(self):
        self.stop_event.set()
        for w in self.workers.values(): w.running=False
        if self.task: await self.task
        for w in self.workers.values():
            try: await w._close()
            except Exception: pass

    async def _run(self):
        while not self.stop_event.is_set():
            with SessionLocal() as db:
                ids=[c.id for c in db.query(Camera).filter(Camera.enabled==True).all()]
                s=db.get(AppSettings,1); delay=max(1,int(s.sync_seconds if s else 3))
            for cid in ids:
                if cid not in self.workers:
                    w=CameraWorker(cid,self.telegram); self.workers[cid]=w; asyncio.create_task(w.run())
            for cid in list(self.workers):
                if cid not in ids:
                    self.workers[cid].running=False; self.workers.pop(cid,None)
            await asyncio.sleep(delay)
