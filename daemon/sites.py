"""What's already obvious from a window's title or a notification, with no model to ask.

Runs after your own allow and block lists, and before anything slower. It only covers the clear
cases and stays out when a title points both ways ("Scraping Shopee with Python"): those go on to
what learned from you, then the model.
"""

import re


# After a word, these make it a file or a library and not the site: Anime.js, discord.py. "Detik.com" is still Detik.
NOT_A_FILE = r"(?!\.(?:jsx?|tsx?|py|rs|go|rb|java|php|cs|cpp|c|h|md|json|ya?ml|html?|css|sh|toml|lock|txt)\b)"


def _words(*options, then=""):
    return re.compile(r"(?<![\w.-])(?:" + "|".join(options) + r")(?![\w-])" + then, re.I)


# Words that also mean something ordinary only count in the shape the distraction comes in: "Temu | Explore" and "Tas - Temu"
# but not "Temu Kembali Informasi", "Resep Bubur" but not "Resep obat", "Ramalan Zodiak" but not "Ramalan Penjualan".
DISTRACTIONS = _words(
    # shopping
    "lazada", "shopee", "tokopedia", "blibli", "bukalapak", "zalora", "tiktok shop", "aliexpress", "temu(?=\\.com| ?[|:]| [-–—] )", "(?<=[-|–] )temu$",
    # watching
    "netflix", "disney\\+", "prime video", "vidio", "wetv", "iqiyi", "viu", "twitch", "nonton", "anime", "sub indo",
    "drakor", "episode \\d+", "bioskop",
    # games
    "genshin( impact)?", "mobile legends", "valorant", "roblox", "minecraft", "free fire", "pubg", "steam store",
    # sports and gossip
    "timnas", "liga (inggris|italia|spanyol|champions|1)", "premier league", "hasil pertandingan", "transfer pemain",
    "gosip", "selebriti", "zodiak", "ramalan (?:zodiak|bintang|cinta|jodoh|karier|karir|shio|nasib|tarot|hari ini)", "horoscope", "horoskop",
    # food and leisure
    "resep(?! (?:obat|dokter|kacamata|antibiotik|digital|elektronik|sukses|rahasia|bisnis))",
    "cookpad",
    # feeds
    "instagram", "tiktok", "facebook", "reddit", "pinterest",
    then=NOT_A_FILE,
)
# Learning about a thing is not the same as being distracted by it: "Minecraft modding in Java tutorial".
LEARNING = _words(
    "tutorials?", "lectures?", "courses?", "kursus", "kuliah", "materi", "belajar", "jurnal", "journal", "papers?", "skripsi",
    "khan academy", "coursera", "udemy", "dicoding",
)
TECH = _words(
    "documentation", "docs?", "dokumentasi", "api reference", "api", "sdk", "getting started", "upgrade guide", "changelog",
    "release notes", "stack ?overflow", "github", "gitlab", "mdn", "npm", "pypi", "packagist", "readthedocs", "dev\\.to",
    "laravel", "eloquent", "php", "react", "next\\.?js", "vue", "typescript", "javascript", "python", "django", "rust",
    "golang", "docker", "kubernetes", "postgres(ql)?", "mysql", "sql", "redis", "nginx", "tailwind( css)?", "prisma",
    "node\\.?js", "git", "linux", "frontend", "backend", "debugging", "stack trace", "dicoding", "regex", "ci/cd",
)
CODING_MODE = re.compile(r"\b(cod(e|ing)|ngoding|program|develop|software|debug)", re.I)
EDITORS = re.compile(r"^(code|code-oss|codium|cursor|antigravity(-ide)?|zed|sublime_text|org\.gnome\.Builder|jetbrains-.*)"
                     r"\.desktop$", re.I)

# Notifications.
URGENT = _words(
    "otp", "kode verifikasi", "verification code", "security code", "password reset", "reset password",
    "percobaan login", "login dari perangkat", "new sign-?in", "suspicious", "mencurigakan", "transaksi gagal",
    "peringatan keamanan", "security alert", "akun (kamu|anda) (diblokir|dikunci)",
    "server down", "production down", "build failed", "ci failed", "deploy(ment)? failed", "darurat", "mendesak", "urgent",
)
ADS = _words(
    "promo", "diskon", "voucher", "cashback", "flash sale", "gratis ongkir", "kupon", "\\d+% off", "buruan", "sale",
)
FEED_APPS = _words("instagram", "tiktok", "facebook", "threads", "twitter", "youtube", "pinterest", "duolingo", "snapchat")
PRESSING = _words("deadline", "tenggat", "asap", "errors?", "exceptions?", "outage", "gangguan", "gagal", "failed")
# Someone asking for something by a certain time.
ASKED_BY_WHEN = re.compile(r"\b(tolong|mohon|please|bisa|can you|could you)\b.*\b(besok|hari ini|sekarang|malam ini|nanti (pagi|siang|sore|malam)"
                           r"|jam \d+|today|tomorrow|tonight|by \d+)\b", re.I | re.S)


def _is_coding(mode):
    return bool(CODING_MODE.search(f"{mode.get('label', '')} {mode.get('description', '')}"))


def about_the_work(mode, title):
    """Whether a title is about the tools of the trade, in a mode for coding: "Twitter API v2 documentation"."""
    return _is_coding(mode) and bool(TECH.search(title or ""))


def window_verdict(mode, app_id, title):
    """(on_task or off_task, what it is) when a window's plainly one or the other in a focus mode, else None."""
    coding = _is_coding(mode)
    if coding and app_id and EDITORS.match(app_id):
        return "on_task", ""
    text = title or ""
    tech = TECH.search(text)
    distraction = DISTRACTIONS.search(text)
    if distraction and (tech or LEARNING.search(text)):
        return None  # "Scraping Shopee with Python": a model can read that
    if distraction:
        return "off_task", distraction.group(0).strip().title()
    if tech and coding:
        return "on_task", tech.group(0)
    return None


def notification_verdict(app, title, body):
    """True (let it through) or False (it can wait) when that's plain, else None."""
    text = f"{title or ''} {body or ''}"
    if URGENT.search(text):
        return True
    if ADS.search(text) or FEED_APPS.search(app or ""):
        return False
    # These come after the ads: "cashback gagal? cek promo" is an ad, not a failure.
    if PRESSING.search(text) or ASKED_BY_WHEN.search(text):
        return True
    return None
