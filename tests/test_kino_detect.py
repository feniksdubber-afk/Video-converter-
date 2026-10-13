import sys, types, asyncio, logging
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

# ---- pyrogram / telegram / project stubs ----
class FloodWait(Exception): pass
pyro = types.ModuleType("pyrogram")
enums = types.ModuleType("pyrogram.enums")
class MF: VIDEO="VIDEO"; DOCUMENT="DOCUMENT"
enums.MessagesFilter = MF; enums.ParseMode = MagicMock()
errs = types.ModuleType("pyrogram.errors"); errs.FloodWait = FloodWait
filters = types.ModuleType("pyrogram.filters")
class _F:
    def __and__(s,o): return s
filters.incoming = _F(); filters.private = _F()
pyro.filters = filters; pyro.enums = enums
handlers_m = types.ModuleType("pyrogram.handlers")
handlers_m.MessageHandler = lambda *a, **k: ("h", a)
handlers_m.EditedMessageHandler = lambda *a, **k: ("e", a)
for n, m in {"pyrogram": pyro, "pyrogram.enums": enums, "pyrogram.errors": errs,
             "pyrogram.filters": filters, "pyrogram.handlers": handlers_m,
             "pyrogram.raw": MagicMock(), "pyrogram.raw.functions": MagicMock()}.items():
    sys.modules[n] = m
for n in ["telegram", "telegram.constants", "telegram.error", "telegram.ext",
          "config", "handlers.save_restricted", "utils", "utils.auth"]:
    sys.modules[n] = MagicMock()
sys.modules["telegram.error"].BadRequest = type("BadRequest", (Exception,), {})
sys.modules["telegram.error"].RetryAfter = type("RetryAfter", (Exception,), {})
sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})
sys.path.insert(0, ".")
import handlers.kino_sender as ks
ks.POLL_INTERVAL = 0.2

def msg(id, video=False, buttons=0, outgoing=False, chat=7):
    rows = [[NS(text=f"b{i}", callback_data=b"x", url=None) for i in range(buttons)]] if buttons else []
    return NS(id=id, video=NS(file_id="v") if video else None, document=None, audio=None,
              animation=None, video_note=None, photo=None, text=None, caption="cap" if video else None,
              outgoing=outgoing, empty=False, chat=NS(id=chat), media="VIDEO" if video else None,
              reply_markup=NS(inline_keyboard=rows) if rows else None)

class FakeClient:
    """Event'lar UMUMAN kelmaydi, chat tarixi bo'sh — faqat server qidiruvi video ko'rsatadi."""
    def __init__(self, history, videos_before, videos_after, flip_after=0.5):
        self.history=history; self.vb=videos_before; self.va=videos_after; self.flip=flip_after
        self.pressed_at=None; self.loop=None
    def add_handler(self, h, group=0): return (h, group)
    def remove_handler(self, h, group=0): pass
    async def get_chat_history(self, cid, limit=1):
        for m in self.history: yield m
    async def get_messages(self, cid, mid): return msg(mid, buttons=3)
    async def search_messages(self, cid, filter=None, limit=1):
        if filter != "VIDEO": return
        now = asyncio.get_event_loop().time()
        pool = self.va if (self.pressed_at and now - self.pressed_at > self.flip) else self.vb
        for m in pool[:limit]: yield m

async def run(client, timeout=3):
    btn = msg(100, buttons=3)
    async def trigger():
        client.pressed_at = asyncio.get_event_loop().time()
    status = MagicMock()
    async def _noop(*a, **k): return True
    ks._edit = _noop
    return await ks._wait_for_response(client, 7, btn, timeout, trigger, status, {})

def test_video_found_only_via_search():
    old = msg(50, video=True)          # oldingi urinishdan qolgan video (snapshot'da bor)
    new = msg(101, video=True)
    c = FakeClient(history=[], videos_before=[old], videos_after=[new, old])
    kind, m, _ = asyncio.run(run(c))
    assert kind == "media" and m.id == 101, (kind, m)

def test_old_video_not_mistaken_as_new():
    old = msg(50, video=True)
    c = FakeClient(history=[], videos_before=[old], videos_after=[old])
    kind, m, _ = asyncio.run(run(c, timeout=1.5))
    assert kind is None, (kind, m)

def test_video_found_via_history_when_no_events():
    new = msg(101, video=True, buttons=2)
    c = FakeClient(history=[new], videos_before=[], videos_after=[])
    kind, m, _ = asyncio.run(run(c))
    assert kind == "media" and m.id == 101

def test_timeout_fills_diag():
    c = FakeClient(history=[], videos_before=[], videos_after=[])
    ans = {}
    async def go():
        btn = msg(100, buttons=3)
        async def trig(): pass
        async def _noop(*a, **k): return True
        ks._edit = _noop
        return await ks._wait_for_response(c, 7, btn, 1.2, trig, MagicMock(), ans)
    kind, m, _ = asyncio.run(go())
    assert kind is None and "diag" in ans

if __name__ == "__main__":
    import traceback
    ok = True
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                ok = False; print("FAIL", name); traceback.print_exc()
    sys.exit(0 if ok else 1)
