"""kino_sender: javobni aniqlash va xom (parse qilinmagan) xabarlarni yetkazish testlari.
Pyrogram/PTB o'rnatilmagan muhitda ham ishlaydi (stub'lar bilan):  python tests/test_kino_detect.py"""
import sys, types, asyncio
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

# ---------------- stub'lar ----------------
class FloodWait(Exception):
    def __init__(self, value=1): self.value = value

def _mod(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m

class MF: VIDEO = "VIDEO"; DOCUMENT = "DOCUMENT"
class _F:
    def __and__(s, o): return s
class _Req:
    def __init__(self, **kw): self.__dict__.update(kw)
class GetMessages(_Req): pass
class ForwardMessages(_Req): pass
class InputMessageID(_Req): pass
class FileType: VIDEO = "V"; AUDIO = "A"; DOCUMENT = "D"
class FileId:
    def __init__(self, **kw): self.kw = kw
    def encode(self): return "FID:" + str(self.kw["media_id"]) + ":" + self.kw["file_type"]

functions = NS(messages=NS(GetMessages=GetMessages, ForwardMessages=ForwardMessages))
_mod("pyrogram", filters=NS(incoming=_F(), private=_F()), enums=NS(MessagesFilter=MF, ParseMode=MagicMock()))
_mod("pyrogram.enums", MessagesFilter=MF, ParseMode=MagicMock())
_mod("pyrogram.errors", FloodWait=FloodWait)
_mod("pyrogram.filters", incoming=_F(), private=_F())
_mod("pyrogram.handlers", MessageHandler=lambda *a, **k: ("h", a), EditedMessageHandler=lambda *a, **k: ("e", a))
_mod("pyrogram.raw", functions=functions, types=NS(InputMessageID=InputMessageID))
_mod("pyrogram.raw.functions", messages=functions.messages)
_mod("pyrogram.file_id", FileId=FileId, FileType=FileType)
for n in ["telegram", "telegram.constants", "telegram.ext", "config", "handlers.save_restricted", "utils", "utils.auth"]:
    sys.modules[n] = MagicMock()
_mod("telegram.error", BadRequest=type("BadRequest", (Exception,), {}),
     RetryAfter=type("RetryAfter", (Exception,), {}), TelegramError=type("TelegramError", (Exception,), {}))
sys.path.insert(0, ".")
import handlers.kino_sender as ks
ks.POLL_INTERVAL = 0.2
ks.OPAQUE_SETTLE = 0.3

async def _noop(*a, **k): return True
ks._edit = _noop

# ---------------- yordamchilar ----------------
def msg(id, video=False, buttons=0, outgoing=False, chat=7, text=None):
    rows = [[NS(text=f"b{i}", callback_data=b"x", url=None) for i in range(buttons)]] if buttons else []
    return NS(id=id, video=NS(file_id="v") if video else None, document=None, audio=None,
              animation=None, video_note=None, photo=None, text=text, caption="cap" if video else None,
              outgoing=outgoing, empty=False, chat=NS(id=chat), media="VIDEO" if video else None,
              reply_markup=NS(inline_keyboard=rows) if rows else None)

def _named(cls_name, **kw):
    """Sinf nomi MTProto turiga mos obyekt (kod type(x).__name__ ga qaraydi)."""
    return type(cls_name, (), {})() if not kw else _fill(type(cls_name, (), {})(), kw)

def _fill(o, kw):
    o.__dict__.update(kw); return o

def raw_doc(attrs=("DocumentAttributeVideo", "DocumentAttributeFilename")):
    mk = {"DocumentAttributeVideo": dict(duration=61, w=1280, h=720, supports_streaming=True),
          "DocumentAttributeFilename": dict(file_name="rumiy.mp4")}
    return NS(id=999, access_hash=5, file_reference=b"r", dc_id=2, mime_type="video/mp4", size=12345,
              attributes=[_named(a, **mk[a]) for a in attrs])

def raw_msg(id, media=None, text="🍿 Kino haqida", noforwards=False):
    return NS(id=id, media=media, message=text, noforwards=noforwards, reply_markup=None)

def media_doc(doc):
    return _named("MessageMediaDocument", document=doc)

class FakeClient:
    def __init__(self, history=(), videos_before=(), videos_after=(), flip_after=0.5, raws=None,
                 search_error=None, forward_error=None, history_error=None):
        self.history = list(history); self.vb = list(videos_before); self.va = list(videos_after)
        self.flip = flip_after; self.pressed_at = None; self.raws = raws or {}
        self.search_error = search_error; self.forward_error = forward_error
        self.history_error = history_error; self.forwarded = None; self.search_calls = 0
    def add_handler(self, h, group=0): return (h, group)
    def remove_handler(self, h, group=0): pass
    async def get_chat_history(self, cid, limit=1):
        if self.history_error:
            e, self.history_error = self.history_error, None; raise e
        for m in self.history: yield m
    async def get_messages(self, cid, mid): return msg(mid, buttons=3)
    async def search_messages(self, cid, filter=None, limit=1):
        self.search_calls += 1
        if self.search_error: raise self.search_error
        if filter != "VIDEO": return
        now = asyncio.get_event_loop().time()
        pool = self.va if (self.pressed_at and now - self.pressed_at > self.flip) else self.vb
        for m in pool[:limit]: yield m
    async def invoke(self, req):
        if isinstance(req, GetMessages):
            ids = [i.id for i in req.id]
            return NS(messages=[self.raws[i] for i in ids if i in self.raws])
        if isinstance(req, ForwardMessages):
            if self.forward_error: raise self.forward_error
            self.forwarded = req; return NS()
    async def resolve_peer(self, x): return ("peer", x)
    async def get_chat(self, x): return NS()
    def rnd_id(self): return 1

async def _wait(client, timeout=3, answer=None):
    client.pressed_at = None
    async def trigger(): client.pressed_at = asyncio.get_event_loop().time()
    return await ks._wait_for_response(client, 7, msg(100, buttons=3), timeout, trigger, MagicMock(),
                                       answer if answer is not None else {})
def run(client, **kw): return asyncio.run(_wait(client, **kw))

# ---------------- aniqlash testlari ----------------
def test_video_found_only_via_search():
    old, new = msg(50, video=True), msg(101, video=True)
    kind, m, _ = run(FakeClient(videos_before=[old], videos_after=[new, old]))
    assert kind == "media" and m.id == 101

def test_old_video_not_mistaken_as_new():
    old = msg(50, video=True)
    kind, m, _ = run(FakeClient(videos_before=[old], videos_after=[old]), timeout=1.5)
    assert kind is None

def test_video_found_via_history_when_no_events():
    kind, m, _ = run(FakeClient(history=[msg(101, video=True, buttons=2)]))
    assert kind == "media" and m.id == 101

def test_timeout_fills_diag():
    ans = {}
    kind, _, _ = run(FakeClient(), timeout=1.2, answer=ans)
    assert kind is None and "diag" in ans

def test_opaque_message_returns_raw_list():
    kind, found, _ = run(FakeClient(history=[msg(101)]), timeout=6)
    assert kind == "raw" and [m.id for m in found] == [101]

def test_multiple_opaque_messages_all_returned():
    kind, found, _ = run(FakeClient(history=[msg(102), msg(101)]), timeout=6)
    assert kind == "raw" and sorted(m.id for m in found) == [101, 102]

def test_photo_only_new_message_is_not_raw():
    p = msg(101); p.photo = NS(file_id="p"); p.media = "PHOTO"
    kind, _, _ = run(FakeClient(history=[p]), timeout=1.5)
    assert kind is None

def test_sticker_voice_and_service_messages_are_not_raw():
    for kindname in ("STICKER", "VOICE", "POLL"):
        m = msg(101); m.media = kindname
        kind, _, _ = run(FakeClient(history=[m]), timeout=1.2)
        assert kind is None, kindname
    svc = msg(102); svc.service = "PINNED_MESSAGE"
    kind, _, _ = run(FakeClient(history=[svc]), timeout=1.2)
    assert kind is None

def test_text_only_message_is_not_raw():
    kind, _, texts = run(FakeClient(history=[msg(101, text="Obuna bo'ling")]), timeout=1.5)
    assert kind is None and texts == ["Obuna bo'ling"]

def test_menu_with_buttons_is_menu():
    kind, m, _ = run(FakeClient(history=[msg(101, buttons=4)]))
    assert kind == "menu" and m.id == 101

def test_snapshot_failure_disables_search_no_false_positive():
    """Snapshot olinmasa, eski videoni yangi deb yubormaymiz."""
    old = msg(50, video=True)
    c = FakeClient(videos_before=[old], videos_after=[old], search_error=RuntimeError("boom"))
    kind, m, _ = run(c, timeout=1.5)
    assert kind is None

def test_flood_wait_in_polling_does_not_kill_flow():
    async def fast(_e): pass
    ks._flood_pause = fast
    c = FakeClient(history=[msg(101, video=True)], history_error=FloodWait(1))
    kind, m, _ = run(c)
    assert kind == "media" and m.id == 101

# ---------------- xom yetkazish testlari ----------------
def test_synth_from_raw_builds_downloadable_video():
    synth = ks._synth_from_raw(raw_msg(101, media_doc(raw_doc())), 7)
    assert synth.video and synth.video.file_size == 12345 and synth.video.duration == 61
    assert synth.video.width == 1280 and synth.is_synthetic and synth.dl_target.file_id.startswith("FID:999")
    assert ks._media_of(synth) is synth.video and ks._guess_ext(synth) == "mp4"

def test_synth_none_when_no_document():
    assert ks._synth_from_raw(raw_msg(101, None), 7) is None

def test_deliver_raw_uses_server_forward_first():
    c = FakeClient(raws={101: raw_msg(101, media_doc(raw_doc()))})
    called = []
    async def fake_dm(*a, **k): called.append(a)
    ks._deliver_media = fake_dm
    asyncio.run(ks._deliver_raw(c, 7, [msg(101)], -100123, "1-qism", MagicMock()))
    assert c.forwarded is not None and c.forwarded.id == [101] and c.forwarded.drop_author is True
    assert not called

def test_deliver_raw_protected_falls_back_to_download():
    c = FakeClient(raws={101: raw_msg(101, media_doc(raw_doc()), noforwards=True)},
                   forward_error=RuntimeError("CHAT_FORWARDS_RESTRICTED"))
    got = []
    async def fake_dm(client, synth, dest, title, status): got.append(synth)
    ks._deliver_media = fake_dm
    asyncio.run(ks._deliver_raw(c, 7, [msg(101)], -100123, "1-qism", MagicMock()))
    assert len(got) == 1 and got[0].is_synthetic and got[0].video.file_size == 12345

def test_deliver_raw_unsupported_media_explains_pyrogram_upgrade():
    unsupported = _named("MessageMediaUnsupported")
    c = FakeClient(raws={101: raw_msg(101, unsupported)}, forward_error=RuntimeError("x"))
    try:
        asyncio.run(ks._deliver_raw(c, 7, [msg(101)], -100123, "1-qism", MagicMock()))
        assert False, "KinoError kutilgan edi"
    except ks.KinoError as e:
        assert "pyrofork" in str(e) or "kurigram" in str(e)

def test_deliver_raw_message_deleted_reports_clearly():
    c = FakeClient(raws={}, forward_error=RuntimeError("x"))
    try:
        asyncio.run(ks._deliver_raw(c, 7, [msg(101)], -100123, "1-qism", MagicMock()))
        assert False
    except ks.KinoError as e:
        assert "topilmadi" in str(e)

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
