"""post_builder.build_caption testlari. Ishga tushirish:  python tests/test_post_builder.py
(telegram/pyrogram o'rnatilmagan bo'lsa ham ishlaydi; pytest ham topadi)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from handlers.post_builder import build_caption, _visible_len, CAPTION_LIMIT

FULL = {"title": "Sendan O'zga Muhabbat", "country": "Janubiy Koreya", "language": "O'zbek",
        "genres": ["Drama", "Romantika"], "quality": "1080p", "year": 2026,
        "code": 271, "kind": "serial", "views": 134, "rating": 6.6, "votes": 182}


def test_full_caption_lines():
    c = build_caption(FULL)
    for part in ("📺 <b>Sendan O'zga Muhabbat</b>", "Davlat: Janubiy Koreya", "Til: O'zbek",
                 "#Drama #Romantika", "Sifat: 1080p", "Yil: 2026", "Kod: 271 (serial)",
                 "Ko'rishlar: 134", "Reyting: 6.6/10 (182)", "@SaraFilmHD"):
        assert part in c, part


def test_html_is_escaped_apostrophe_kept():
    c = build_caption({"title": "A & B <x>"})
    assert "A &amp; B &lt;x&gt;" in c
    assert "O'zbek" in build_caption({"language": "O'zbek"})


def test_missing_fields_dropped():
    c = build_caption({"title": "Test"})
    assert "Davlat" not in c and "Reyting" not in c and "\n\n\n" not in c


def test_limit_drops_optional_first():
    c = build_caption({"title": "x" * 190, "genres": ["Drama"] * 200, "code": 1, "views": 2, "rating": 7})
    assert _visible_len(c) <= CAPTION_LIMIT or "Reyting" not in c


def test_long_title_capped():
    c = build_caption({"title": "x" * 900})
    assert _visible_len(c) <= CAPTION_LIMIT


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1; print("OK", name)
    print(f"{n} ta test o'tdi")
