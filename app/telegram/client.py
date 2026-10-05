import httpx
from app.db.database import SessionLocal
from app.db.models import TelegramSettings

class TelegramClient:
    def _settings(self):
        with SessionLocal() as db:
            s = db.get(TelegramSettings, 1)
            if not s or not s.enabled or not s.bot_token or not s.chat_id:
                raise RuntimeError("Telegram is not configured")
            return {"enabled":s.enabled,"token":s.bot_token,"chat_id":s.chat_id,"thread_id":s.thread_id,"proxy_enabled":s.proxy_enabled,"proxy_host":s.proxy_host,"proxy_port":s.proxy_port}

    def _client(self):
        s = self._settings()
        proxy = None
        if s["proxy_enabled"] and s["proxy_host"]:
            proxy = f'socks5://{s["proxy_host"]}:{s["proxy_port"]}'
        return s, httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0), proxy=proxy)

    async def send_message(self, text):
        s, client = self._client()
        try:
            data={"chat_id":s["chat_id"],"text":text}
            if s["thread_id"]: data["message_thread_id"]=s["thread_id"]
            r=await client.post(f'https://api.telegram.org/bot{s["token"]}/sendMessage', data=data)
            r.raise_for_status(); payload=r.json()
            if not payload.get("ok"): raise RuntimeError(str(payload))
        finally:
            await client.aclose()

    async def send_photo(self, path, caption):
        s, client = self._client()
        try:
            data={"chat_id":s["chat_id"],"caption":caption}
            if s["thread_id"]: data["message_thread_id"]=s["thread_id"]
            with open(path,"rb") as f:
                files={"photo":(path.name if hasattr(path,'name') else str(path), f, "image/jpeg")}
                r=await client.post(f'https://api.telegram.org/bot{s["token"]}/sendPhoto', data=data, files=files)
            r.raise_for_status(); payload=r.json()
            if not payload.get("ok"): raise RuntimeError(str(payload))
        finally:
            await client.aclose()
