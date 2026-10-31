"""What's already obvious from a window's title or a notification, with no model to ask.

Runs after your own allow and block lists, and before anything slower. It only covers the clear
cases and stays out when a title points both ways ("Scraping Shopee with Python"): those go on to
what learned from you, then the model.
"""

import re


def _words(*options):
    return re.compile(r"(?<![\w.-])(?:" + "|".join(options) + r")(?![\w-])", re.I)


DISTRACTIONS = _words(
    # shopping
    "lazada", "shopee", "tokopedia", "blibli", "bukalapak", "zalora", "tiktok shop", "aliexpress", "temu",
    # watching
    "netflix", "disney\\+", "prime video", "vidio", "wetv", "iqiyi", "viu", "twitch", "nonton", "anime", "sub indo",
    "drakor", "episode \\d+", "bioskop",
    # games
    "genshin( impact)?", "mobile legends", "valorant", "roblox", "minecraft", "free fire", "pubg", "steam store",
    # sports and gossip
    "timnas", "liga (inggris|italia|spanyol|champions|1)", "premier league", "hasil pertandingan", "transfer pemain",
    "gosip", "selebriti", "zodiak", "ramalan", "horoscope", "horoskop",
    # food and leisure
    "resep", "cookpad",
    # feeds
    "instagram", "tiktok", "facebook", "reddit", "pinterest",
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


def window_verdict(mode, app_id, title):
    """(on_task or off_task, what it is) when a window's plainly one or the other in a focus mode, else None."""
    coding = bool(CODING_MODE.search(f"{mode.get('label', '')} {mode.get('description', '')}"))
    if coding and app_id and EDITORS.match(app_id):
        return "on_task", ""
    text = title or ""
    tech = TECH.search(text)
    distraction = DISTRACTIONS.search(text)
    if tech and distraction:
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
    return None
