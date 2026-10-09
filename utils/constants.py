import os

from dotenv import load_dotenv

load_dotenv()

# Paths
OUTPUT_DIR = os.path.expanduser(os.getenv("OUTPUT_DIR", "~/clipodify_output"))
TMP_DIR = os.getenv("TMP_DIR", "./tmp")

# Whisper
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v2")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "id")
WHISPER_LANGUAGE_MIN_PROB = float(os.getenv("WHISPER_LANGUAGE_MIN_PROB", 0.85))

# Windowing
PAUSE_GAP_THRESHOLD = float(os.getenv("PAUSE_GAP_THRESHOLD", 1.2))
PAUSE_SILENCE_DB = float(os.getenv("PAUSE_SILENCE_DB", -30))
PAUSE_MIN_SECONDS = float(os.getenv("PAUSE_MIN_SECONDS", 0.4))
PAUSE_SNAP_TOLERANCE = float(os.getenv("PAUSE_SNAP_TOLERANCE", 1.0))
MAX_WINDOW_SECONDS = int(os.getenv("MAX_WINDOW_SECONDS", 90))
MIN_WINDOW_SECONDS = int(os.getenv("MIN_WINDOW_SECONDS", 20))

# Processing
TOP_SEGMENTS = int(os.getenv("TOP_SEGMENTS", 30))
FINAL_CANDIDATES = int(os.getenv("FINAL_CANDIDATES", 3))
CONTEXT_SECONDS = int(os.getenv("CONTEXT_SECONDS", 60))

# Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Menggunakan penomoran versi resmi SDK Google GenAI
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-2.0-flash,gemini-1.5-flash,gemini-1.5-flash-8b",
    ).split(",")
    if m.strip()
]
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", 32768))

# yt-dlp
YTDLP_COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
YTDLP_COOKIE_FILE = os.path.expanduser(os.getenv("YTDLP_COOKIE_FILE", "").strip())

# Comment mining
MAX_COMMENTS = int(os.getenv("MAX_COMMENTS", 250))
COMMENT_PAD_SECONDS = int(os.getenv("COMMENT_PAD_SECONDS", 15))
MIN_MENTIONS_FOR_SIGNAL = int(os.getenv("MIN_MENTIONS_FOR_SIGNAL", 5))

# Atribusi
CLIP_ATTRIBUTION = os.getenv(
    "CLIP_ATTRIBUTION",
    "Potongan momen dari Youtube : {channel} - {source}.\n\n"
    "Hak cipta sepenuhnya milik pemilik konten asli.\n"
    "Hubungi via DM/Email untuk permintaan penghapusan konten.",
).replace("\\n", "\n")

CLIP_CAMPAIGN_ATTRIBUTION = os.getenv(
    "CLIP_CAMPAIGN_ATTRIBUTION",
    "Potongan momen dari {source}.\n\n"
    "Konten ini bagian dari campaign {product}.\n"
    "Hak cipta materi asli milik pemilik kontennya.",
).replace("\\n", "\n")

# Campaign / Soft-sell
SOFTSELL_MAX_PITCH_RATIO = float(os.getenv("SOFTSELL_MAX_PITCH_RATIO", 0.2))
SOFTSELL_CLEAN_OPENING_SECONDS = int(os.getenv("SOFTSELL_CLEAN_OPENING_SECONDS", 3))
CAMPAIGN_MIN_FIT = float(os.getenv("CAMPAIGN_MIN_FIT", 4.0))

# Hashtags
CLIP_FIXED_HASHTAGS = [
    tag.strip().lstrip("#")
    for tag in os.getenv("CLIP_FIXED_HASHTAGS", "clips,podcast,shorts").split(",")
    if tag.strip()
]

CLIP_CAMPAIGN_HASHTAGS = [
    tag.strip().lstrip("#")
    for tag in os.getenv("CLIP_CAMPAIGN_HASHTAGS", "clips,shorts").split(",")
    if tag.strip()
]

# Analitik channel
CHANNEL_MIN_VIEWS = int(os.getenv("CHANNEL_MIN_VIEWS", 30))
CHANNEL_TZ_OFFSET = int(os.getenv("CHANNEL_TZ_OFFSET", 7))

# Signal weights
WEIGHT_COMMENT = float(os.getenv("WEIGHT_COMMENT", 0.30))
WEIGHT_AUDIO = float(os.getenv("WEIGHT_AUDIO", 0.25))
WEIGHT_NARRATIVE = float(os.getenv("WEIGHT_NARRATIVE", 0.25))
WEIGHT_TEXT = float(os.getenv("WEIGHT_TEXT", 0.20))

# Validasi pasca-Gemini
# DIUBAH: Toleransi snap disesuaikan ke float detik (bukan 30 detik int)
SNAP_TOLERANCE = float(os.getenv("SNAP_TOLERANCE", 2.0))
CLIP_TAIL_PAD_SECONDS = float(os.getenv("CLIP_TAIL_PAD_SECONDS", 2.5))
MIN_CLIP_SECONDS = int(os.getenv("MIN_CLIP_SECONDS", 30))
MAX_CLIP_SECONDS = int(os.getenv("MAX_CLIP_SECONDS", 90))
DEDUPE_MIN_GAP = int(os.getenv("DEDUPE_MIN_GAP", 20))
MAX_TYPE_RATIO = float(os.getenv("MAX_TYPE_RATIO", 0.4))

# Multi-part clip
MAX_CLIP_PARTS = int(os.getenv("MAX_CLIP_PARTS", 3))
MIN_PART_SECONDS = int(os.getenv("MIN_PART_SECONDS", 8))

# Audio analysis
LAUGHTER_FREQ_LOW = 1000   # Hz
LAUGHTER_FREQ_HIGH = 4000  # Hz
ENERGY_HOP_SECONDS = 1.0

# Clip Types
CLIP_TYPES = [
    "rant", "story", "reveal", "funny", "quotable", "debate", "insight",
]

# Quality Gate
QUALITY_MIN_STANDALONE = float(os.getenv("QUALITY_MIN_STANDALONE", 5.0))

# Disqualifiers (Mendukung override via .env)
_default_ad_patterns = (
    "brought to you by,sponsored by,our sponsor,use code,promo code,discount code,"
    "link in the description,check out the link,free trial,sign up at,percent off,"
    "kode promo,kode referral,link di deskripsi,link di bio,cek deskripsi,klik link,"
    "gratis ongkir,disponsori,dapatkan diskon,promo spesial,daftar sekarang,pakai kode,"
    "potongan harga,keranjang kuning,beli sekarang"
)
AD_PATTERNS = [
    p.strip().lower()
    for p in os.getenv("AD_PATTERNS", _default_ad_patterns).split(",")
    if p.strip()
]

_default_boilerplate_patterns = (
    "welcome back to,welcome to the show,don't forget to subscribe,hit that like button,"
    "smash that like,see you next week,thanks for watching,before we get started,"
    "jangan lupa like,jangan lupa subscribe,like dan subscribe,tombol subscribe,"
    "subscribe channel,nyalakan lonceng,tekan lonceng,balik lagi di channel,"
    "kembali lagi di channel,selamat datang di channel,selamat datang di podcast,"
    "terima kasih sudah menonton,makasih udah nonton,sampai jumpa di episode,sampai ketemu di episode"
)
BOILERPLATE_PATTERNS = [
    p.strip().lower()
    for p in os.getenv("BOILERPLATE_PATTERNS", _default_boilerplate_patterns).split(",")
    if p.strip()
]