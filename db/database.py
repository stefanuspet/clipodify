import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clipodify.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL UNIQUE,
    video_id     TEXT NOT NULL,
    title        TEXT,
    duration     INTEGER,
    thumbnail    TEXT,
    has_comments INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'pending',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER REFERENCES jobs(id),
    start_seconds       REAL NOT NULL,
    end_seconds         REAL NOT NULL,
    duration_seconds    REAL,
    transcript_text     TEXT,
    score_comment       REAL,
    score_audio         REAL,
    score_narrative     REAL,
    score_text          REAL,
    score_final         REAL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER REFERENCES jobs(id),
    segment_id      INTEGER REFERENCES segments(id),
    rank            INTEGER,
    start_seconds   REAL,
    end_seconds     REAL,
    hook            TEXT,
    clip_type       TEXT,
    reason          TEXT,
    signal_notes    TEXT,
    segment_scores  TEXT,
    title           TEXT,
    description     TEXT,
    hashtags        TEXT,
    parts           TEXT,
    final_score     REAL,
    status          TEXT DEFAULT 'pending',
    output_path     TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    key_value   TEXT NOT NULL,
    is_active   INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Kredensial OAuth YouTube. Hanya satu baris yang dipakai (id = 1): tool ini
-- lokal dan single-user, jadi tidak ada gunanya mendukung banyak akun sekaligus.
-- Token disimpan di sini, bukan di file terpisah, karena db/clipodify.db sudah
-- masuk .gitignore sementara file baru belum tentu.
CREATE TABLE IF NOT EXISTS yt_auth (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    client_secrets  TEXT NOT NULL,
    token_json      TEXT NOT NULL,
    channel_id      TEXT,
    channel_title   TEXT,
    connected_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Cache metrik per video. Ditulis ulang tiap kali di-refresh, bukan ditumpuk:
-- yang menarik adalah keadaan terkini, dan menyimpan riwayat menaikkan biaya
-- tanpa menjawab pertanyaan yang sedang kita kejar.
CREATE TABLE IF NOT EXISTS yt_videos (
    video_id            TEXT PRIMARY KEY,
    title               TEXT,
    published_at        TEXT,
    duration_seconds    REAL,
    views               INTEGER,
    avg_view_seconds    REAL,
    avg_view_percentage REAL,
    retention_curve     TEXT,
    fetched_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Channel podcast yang dipantau untuk mencari bahan clip. Daftar yang dikurasi
-- sendiri, bukan hasil pencarian: pencarian buta di YouTube mengembalikan
-- channel kecil yang tidak relevan, dan biayanya 100 unit kuota sekali jalan
-- sementara memantau satu channel cuma ~2 unit.
CREATE TABLE IF NOT EXISTS watch_channels (
    channel_id   TEXT PRIMARY KEY,
    handle       TEXT,
    title        TEXT,
    subscribers  INTEGER,
    added_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Koneksi Meta (Instagram + Facebook Page). Satu baris, seperti yt_auth.
-- Token ditempel user, bukan hasil OAuth: alur OAuth Meta menuntut redirect URI
-- HTTPS publik yang tidak dimiliki aplikasi localhost.
CREATE TABLE IF NOT EXISTS meta_auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    access_token  TEXT NOT NULL,
    page_id       TEXT,
    page_name     TEXT,
    ig_id         TEXT,
    ig_username   TEXT,
    kind          TEXT DEFAULT 'page',
    connected_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_segments_job ON segments(job_id);
CREATE INDEX IF NOT EXISTS idx_candidates_job ON candidates(job_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Kolom yang ditambahkan setelah skema awal dipakai. ALTER TABLE dijalankan
# hanya kalau kolomnya belum ada, jadi data lama tetap utuh.
MIGRATIONS = [
    ("jobs", "source_type", "TEXT DEFAULT 'youtube'"),
    ("candidates", "segment_scores", "TEXT"),
    ("candidates", "title", "TEXT"),
    ("candidates", "description", "TEXT"),
    ("candidates", "hashtags", "TEXT"),
    ("candidates", "parts", "TEXT"),
    # Titik hook di dalam clip, dan bahan thumbnail. Kolom terpisah dari `hook`
    # yang lama: itu kutipan teks, ini posisi waktunya.
    ("candidates", "hook_start", "TEXT"),
    ("candidates", "hook_end", "TEXT"),
    ("candidates", "hook_placement", "TEXT"),
    ("candidates", "thumbnail_text", "TEXT"),
    ("candidates", "thumbnail_prompt", "TEXT"),
    # Nama tokoh di clip, untuk hashtag nama di caption.
    ("candidates", "people", "TEXT"),
    # Hasil analisis campaign. NULL pada clip mode reguler — dan itu memang
    # pembedanya: kolom terisi berarti clip ini pernah dinilai kecocokan produk.
    ("candidates", "campaign_fit", "REAL"),
    ("candidates", "product_moment", "TEXT"),
    # Mode dan produk disimpan di level job, bukan candidate: satu analisis
    # seluruhnya berjalan di satu mode, dan caption butuh nama produknya kembali
    # saat clip diunduh berhari-hari kemudian dari halaman History.
    ("jobs", "mode", "TEXT DEFAULT 'regular'"),
    ("jobs", "campaign_product", "TEXT"),
    # Hashtag wajib dari brand, disimpan sebagai JSON list. Ikut di level job
    # karena syaratnya berlaku untuk seluruh clip dari campaign itu.
    ("jobs", "campaign_hashtags", "TEXT"),
    # Skor reaksi. Terpisah dari `final_score` karena mengukur hal yang berbeda:
    # final_score menjawab "sebagus apa clip ini", dua kolom ini menjawab "apakah
    # orang akan BERGERAK setelah menontonnya". Data channel 14 Agustus 2026
    # menunjukkan keduanya bisa berlawanan arah — retensi 88,5% dengan 1 share.
    ("candidates", "shareability", "REAL"),
    ("candidates", "discussion_trigger", "REAL"),
    # Subskor mutu. `standalone_context` dipakai sebagai GERBANG di validator
    # (di bawah QUALITY_MIN_STANDALONE = dibuang), dua lainnya untuk peringkat
    # dan diagnosis. Sengaja tidak dirata-ratakan jadi final_score — subskor LLM
    # menggerombol di 7-8 dan rata-rata memampatkan daya pisahnya.
    ("candidates", "standalone_context", "REAL"),
    ("candidates", "hook_strength", "REAL"),
    ("candidates", "payoff_strength", "REAL"),
    # Metrik keterlibatan per video. Retensi menjawab "ditonton sampai mana";
    # kolom-kolom ini menjawab "apakah penontonnya peduli" — dua hal berbeda
    # yang bisa berlawanan arah pada video yang sama.
    ("yt_videos", "likes", "INTEGER"),
    ("yt_videos", "comments", "INTEGER"),
    ("yt_videos", "shares", "INTEGER"),
    ("yt_videos", "subs_gained", "INTEGER"),
    ("yt_videos", "minutes_watched", "REAL"),
    # Jalur koneksi Meta: lewat Facebook Page, atau langsung Instagram Login.
    ("meta_auth", "kind", "TEXT DEFAULT 'page'"),
    # Koneksi Facebook Page TERPISAH dari Instagram. Token Instagram Login
    # (diawali IGAA) bicara ke graph.instagram.com dan tidak bisa menyentuh
    # endpoint Page sama sekali — jadi Page butuh tokennya sendiri.
    ("meta_auth", "fb_token", "TEXT"),
    ("meta_auth", "fb_page_id", "TEXT"),
    ("meta_auth", "fb_page_name", "TEXT"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, coltype in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db() -> None:
    """Buat semua tabel kalau belum ada, lalu terapkan migrasi kolom."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


# ---------------------------------------------------------------- jobs

def create_job(
    url: str,
    video_id: str,
    title: str,
    duration: int,
    thumbnail: str,
    source_type: str = "youtube",
    mode: str = "regular",
    campaign_product: str = "",
    campaign_hashtags: list[str] | None = None,
) -> int:
    """Insert job baru. Kalau URL sudah ada, return job_id yang existing.

    `url` juga menampung path absolut untuk sumber file lokal — kolom itu adalah
    identitas sumber, bukan khusus alamat web.
    """
    tags_json = json.dumps(campaign_hashtags or [], ensure_ascii=False)

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE url = ?", (url,)
        ).fetchone()
        if existing:
            # Video yang sama bisa dianalisis ulang dengan mode berbeda. Kalau
            # mode lama dibiarkan, caption clip campaign akan memakai blok
            # atribusi reguler yang isinya keliru.
            conn.execute(
                "UPDATE jobs SET mode = ?, campaign_product = ?, "
                "campaign_hashtags = ? WHERE id = ?",
                (mode, campaign_product, tags_json, existing["id"]),
            )
            return existing["id"]

        cursor = conn.execute(
            """
            INSERT INTO jobs (
                url, video_id, title, duration, thumbnail, source_type,
                mode, campaign_product, campaign_hashtags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, video_id, title, duration, thumbnail, source_type,
             mode, campaign_product, tags_json),
        )
        return cursor.lastrowid


def get_job_by_url(url: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None


def get_job_by_id(job_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def update_job_status(job_id: int, status: str) -> None:
    """status: pending | processing | done | error"""
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, job_id),
        )


def set_job_has_comments(job_id: int, has_comments: bool) -> None:
    """Catat apakah video ini punya mention timestamp di komentar.

    Menentukan apakah bobot fusion dinormalisasi ulang, dan ditampilkan di
    halaman History supaya hasil lama bisa dibaca dengan konteks yang benar.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET has_comments = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if has_comments else 0, job_id),
        )


def get_all_jobs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


# ------------------------------------------------------------ segments

def save_segments(job_id: int, segments: list[dict]) -> None:
    """Ganti semua segment untuk job ini dengan yang baru."""
    with _connect() as conn:
        conn.execute("DELETE FROM segments WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO segments (
                job_id, start_seconds, end_seconds, duration_seconds,
                transcript_text, score_comment, score_audio, score_narrative,
                score_text, score_final
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    job_id,
                    seg.get("start_seconds"),
                    seg.get("end_seconds"),
                    seg.get("duration_seconds"),
                    seg.get("transcript_text"),
                    seg.get("score_comment"),
                    seg.get("score_audio"),
                    seg.get("score_narrative"),
                    seg.get("score_text"),
                    seg.get("score_final"),
                )
                for seg in segments
            ],
        )


def get_segments_by_job(job_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM segments WHERE job_id = ? ORDER BY score_final DESC",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------------------------------------------------------- candidates

def save_candidates(job_id: int, candidates: list[dict]) -> None:
    """Ganti semua candidate untuk job ini dengan yang baru.

    `rank` diambil dari urutan list kalau tidak ada di dict.
    `signal_notes` disimpan sebagai JSON string.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM candidates WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO candidates (
                job_id, segment_id, rank, start_seconds, end_seconds,
                hook, clip_type, reason, signal_notes, segment_scores,
                title, description, hashtags, parts,
                hook_start, hook_end, hook_placement,
                thumbnail_text, thumbnail_prompt, people,
                campaign_fit, product_moment,
                shareability, discussion_trigger,
                standalone_context, hook_strength, payoff_strength,
                final_score, status, output_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    job_id,
                    cand.get("segment_id"),
                    cand.get("rank", index + 1),
                    cand.get("start_seconds"),
                    cand.get("end_seconds"),
                    cand.get("hook"),
                    cand.get("clip_type"),
                    cand.get("reason"),
                    json.dumps(cand.get("signal_notes", {}), ensure_ascii=False),
                    json.dumps(cand.get("segment_scores", {}), ensure_ascii=False),
                    cand.get("title"),
                    cand.get("description"),
                    json.dumps(cand.get("hashtags", []), ensure_ascii=False),
                    json.dumps(cand.get("parts", []), ensure_ascii=False),
                    cand.get("hook_start"),
                    cand.get("hook_end"),
                    cand.get("hook_placement"),
                    cand.get("thumbnail_text"),
                    cand.get("thumbnail_prompt"),
                    json.dumps(cand.get("people", []), ensure_ascii=False),
                    cand.get("campaign_fit"),
                    cand.get("product_moment"),
                    cand.get("shareability"),
                    cand.get("discussion_trigger"),
                    cand.get("standalone_context"),
                    cand.get("hook_strength"),
                    cand.get("payoff_strength"),
                    cand.get("final_score"),
                    cand.get("status", "pending"),
                    cand.get("output_path"),
                )
                for index, cand in enumerate(candidates)
            ],
        )


def get_candidates_by_job(job_id: int) -> list[dict]:
    """Return candidates urut rank ASC.

    `signal_notes` sudah di-parse jadi dict, dan `start`/`end` string
    direkonstruksi dari kolom float supaya UI tidak perlu konversi sendiri.
    """
    from utils.helpers import seconds_to_timestamp

    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE job_id = ? ORDER BY rank ASC",
            (job_id,),
        ).fetchall()

    candidates = []
    for row in rows:
        cand = dict(row)
        for field, empty in (
            ("signal_notes", {}),
            ("segment_scores", {}),
            ("hashtags", []),
            ("people", []),
            ("parts", []),
        ):
            try:
                cand[field] = json.loads(cand.get(field) or json.dumps(empty))
            except (json.JSONDecodeError, TypeError):
                cand[field] = empty

        start = cand.get("start_seconds")
        end = cand.get("end_seconds")
        if start is not None and end is not None:
            cand["start"] = seconds_to_timestamp(start)
            cand["end"] = seconds_to_timestamp(end)
            cand["duration_seconds"] = end - start

        candidates.append(cand)
    return candidates


def get_api_keys() -> list[dict]:
    """Semua key tersimpan, yang aktif di urutan pertama."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM api_keys ORDER BY is_active DESC, name ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def add_api_key(name: str, key_value: str, make_active: bool = True) -> int:
    """Simpan key baru. Nama yang sudah ada akan ditimpa nilainya."""
    name = (name or "").strip()
    key_value = (key_value or "").strip()
    if not name or not key_value:
        raise ValueError("Nama dan API key tidak boleh kosong")

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO api_keys (name, key_value) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET key_value = excluded.key_value
            """,
            (name, key_value),
        )
        row = conn.execute(
            "SELECT id FROM api_keys WHERE name = ?", (name,)
        ).fetchone()
        key_id = row["id"]

        # Key pertama otomatis jadi aktif — kalau tidak, menyimpan key lalu
        # langsung Analyze akan gagal karena belum ada yang dipilih.
        total = conn.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"]
        if make_active or total == 1:
            conn.execute("UPDATE api_keys SET is_active = 0")
            conn.execute("UPDATE api_keys SET is_active = 1 WHERE id = ?", (key_id,))

    return key_id


def set_active_api_key(key_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE api_keys SET is_active = 0")
        conn.execute("UPDATE api_keys SET is_active = 1 WHERE id = ?", (key_id,))


def delete_api_key(key_id: int) -> None:
    """Hapus key. Kalau yang dihapus sedang aktif, key lain menggantikannya."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT is_active FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()
        conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))

        if row and row["is_active"]:
            successor = conn.execute(
                "SELECT id FROM api_keys ORDER BY name ASC LIMIT 1"
            ).fetchone()
            if successor:
                conn.execute(
                    "UPDATE api_keys SET is_active = 1 WHERE id = ?",
                    (successor["id"],),
                )


def get_active_api_key() -> str | None:
    """Nilai key yang sedang aktif, atau None kalau belum ada satu pun."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT key_value FROM api_keys WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return row["key_value"] if row else None


# ------------------------------------------------------- YouTube analytics

def save_yt_auth(
    client_secrets: str, token_json: str, channel_id: str, channel_title: str
) -> None:
    """Simpan/ganti koneksi YouTube. Selalu baris id = 1."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO yt_auth (id, client_secrets, token_json, channel_id,
                                    channel_title, connected_at)
               VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   client_secrets = excluded.client_secrets,
                   token_json     = excluded.token_json,
                   channel_id     = excluded.channel_id,
                   channel_title  = excluded.channel_title,
                   connected_at   = CURRENT_TIMESTAMP""",
            (client_secrets, token_json, channel_id, channel_title),
        )


def update_yt_token(token_json: str) -> None:
    """Simpan token yang baru saja di-refresh, tanpa menyentuh field lain."""
    with _connect() as conn:
        conn.execute("UPDATE yt_auth SET token_json = ? WHERE id = 1", (token_json,))


def get_yt_auth() -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM yt_auth WHERE id = 1").fetchone()
        return dict(row) if row else None


def delete_yt_auth() -> None:
    """Putuskan koneksi. Metrik yang sudah ditarik SENGAJA dipertahankan.

    Memutuskan koneksi adalah tindakan yang sering dilakukan — ganti akun, ganti
    scope, memulihkan izin yang dicabut Google tiap 7 hari di mode Testing.
    Menghapus metrik setiap kali itu terjadi berarti kehilangan kurva retensi
    yang sudah ditarik satu per satu, dan membayar kuota API lagi untuk data
    yang sebenarnya tidak berubah.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM yt_auth WHERE id = 1")


def save_meta_auth(
    access_token: str, page_id: str | None, page_name: str | None,
    ig_id: str | None, ig_username: str | None, kind: str = "page",
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO meta_auth (id, access_token, page_id, page_name,
                                      ig_id, ig_username, kind, connected_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   access_token = excluded.access_token,
                   page_id      = excluded.page_id,
                   page_name    = excluded.page_name,
                   ig_id        = excluded.ig_id,
                   ig_username  = excluded.ig_username,
                   kind         = excluded.kind,
                   connected_at = CURRENT_TIMESTAMP""",
            (access_token, page_id, page_name, ig_id, ig_username, kind),
        )


def get_meta_auth() -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM meta_auth WHERE id = 1").fetchone()
        return dict(row) if row else None


def set_meta_facebook(
    fb_token: str | None, page_id: str | None, page_name: str | None
) -> None:
    """Pasang atau lepas koneksi Facebook Page, tanpa menyentuh koneksi Instagram."""
    with _connect() as conn:
        conn.execute(
            """UPDATE meta_auth
               SET fb_token = ?, fb_page_id = ?, fb_page_name = ?
               WHERE id = 1""",
            (fb_token, page_id, page_name),
        )


def delete_meta_auth() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM meta_auth WHERE id = 1")


def delete_yt_videos() -> None:
    """Hapus cache metrik. Terpisah dari memutus koneksi, dan disengaja."""
    with _connect() as conn:
        conn.execute("DELETE FROM yt_videos")


def save_yt_videos(videos: list[dict]) -> None:
    """Tulis ulang metrik per video. Baris lama ditimpa, bukan ditumpuk."""
    with _connect() as conn:
        for v in videos:
            conn.execute(
                """INSERT INTO yt_videos (video_id, title, published_at,
                       duration_seconds, views, avg_view_seconds,
                       avg_view_percentage, retention_curve, likes, comments,
                       shares, subs_gained, minutes_watched, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(video_id) DO UPDATE SET
                       title               = excluded.title,
                       published_at        = excluded.published_at,
                       duration_seconds    = excluded.duration_seconds,
                       views               = excluded.views,
                       avg_view_seconds    = excluded.avg_view_seconds,
                       avg_view_percentage = excluded.avg_view_percentage,
                       retention_curve     = COALESCE(excluded.retention_curve,
                                                      yt_videos.retention_curve),
                       likes               = excluded.likes,
                       comments            = excluded.comments,
                       shares              = excluded.shares,
                       subs_gained         = excluded.subs_gained,
                       minutes_watched     = excluded.minutes_watched,
                       fetched_at          = CURRENT_TIMESTAMP""",
                (
                    v["video_id"], v.get("title"), v.get("published_at"),
                    v.get("duration_seconds"), v.get("views"),
                    v.get("avg_view_seconds"), v.get("avg_view_percentage"),
                    json.dumps(v["retention_curve"]) if v.get("retention_curve") else None,
                    v.get("likes"), v.get("comments"), v.get("shares"),
                    v.get("subs_gained"), v.get("minutes_watched"),
                ),
            )


def get_yt_videos() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM yt_videos ORDER BY published_at DESC"
        ).fetchall()

    videos = []
    for row in rows:
        video = dict(row)
        video["retention_curve"] = (
            json.loads(video["retention_curve"]) if video["retention_curve"] else None
        )
        videos.append(video)
    return videos


def save_yt_retention(video_id: str, curve: list[dict]) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE yt_videos SET retention_curve = ? WHERE video_id = ?",
            (json.dumps(curve), video_id),
        )


# ------------------------------------------------- channel podcast dipantau

def add_watch_channel(
    channel_id: str, handle: str, title: str, subscribers: int
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO watch_channels (channel_id, handle, title, subscribers)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(channel_id) DO UPDATE SET
                   handle      = excluded.handle,
                   title       = excluded.title,
                   subscribers = excluded.subscribers""",
            (channel_id, handle, title, subscribers),
        )


def get_watch_channels() -> list[dict]:
    with _connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM watch_channels ORDER BY subscribers DESC"
            )
        ]


def delete_watch_channel(channel_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM watch_channels WHERE channel_id = ?", (channel_id,)
        )


def update_candidate_status(candidate_id: int, status: str, output_path: str = None) -> None:
    """status: pending | downloaded | skipped"""
    with _connect() as conn:
        conn.execute(
            "UPDATE candidates SET status = ?, output_path = ? WHERE id = ?",
            (status, output_path, candidate_id),
        )


init_db()
