"""Clipodify — Streamlit UI.

Pipeline 6 step, sequential. Hanya step 3 (Whisper) yang menyentuh GPU.

Sumber video bisa berupa URL YouTube atau path file lokal. Keduanya dibedakan
di `core/source.py` dan diperlakukan sama persis di seluruh halaman ini —
nilainya beredar dengan nama `src`.
"""

import json
import os

import pandas as pd
import streamlit as st

from core import (
    audio_analyzer,
    channel_advisor,
    clip_report,
    comment_analyzer,
    gemini_analyzer,
    gemini_client,
    meta_analytics,
    narrative_analyzer,
    podcast_scout,
    source,
    text_analyzer,
    upload_schedule,
    youtube_analytics,
)
from core.downloader import cleanup_tmp
from core.fusion import fuse
from core.gemini_analyzer import analyze as analyze_gemini
from core.gemini_analyzer import test_api_key
from core.transcriber import transcribe
from core.validator import validate
from core.windowing import build_windows
from db.database import (
    add_api_key,
    add_watch_channel,
    create_job,
    delete_api_key,
    delete_meta_auth,
    delete_watch_channel,
    delete_yt_auth,
    get_all_jobs,
    get_api_keys,
    get_candidates_by_job,
    get_job_by_url,
    get_meta_auth,
    get_watch_channels,
    get_yt_auth,
    get_yt_videos,
    init_db,
    save_candidates,
    save_meta_auth,
    save_segments,
    save_yt_retention,
    save_yt_videos,
    set_active_api_key,
    set_job_has_comments,
    set_meta_facebook,
    update_candidate_status,
    update_job_status,
)
from utils.constants import (
    CHANNEL_MIN_VIEWS,
    CLIP_CAMPAIGN_HASHTAGS,
    GEMINI_MODEL,
    MAX_CLIP_SECONDS,
    MIN_CLIP_SECONDS,
    OUTPUT_DIR,
    QUALITY_MIN_STANDALONE,
    SOFTSELL_CLEAN_OPENING_SECONDS,
    WHISPER_LANGUAGE,
    WHISPER_LANGUAGE_MIN_PROB,
    SOFTSELL_MAX_PITCH_RATIO,
    TMP_DIR,
)
from utils.helpers import (
    ensure_dirs,
    sanitize_filename,
    seconds_to_timestamp,
    timestamp_to_seconds,
    youtube_embed_url,
    youtube_watch_url,
)

init_db()
ensure_dirs()

st.set_page_config(
    page_title="Clipodify",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

TOTAL_STEPS = 6
GRID_COLUMNS = 2

STYLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "style.css")


def _load_style() -> None:
    """Suntikkan assets/style.css sekali per rerun.

    Ditaruh di berkas terpisah, bukan string di sini, supaya tampilan bisa
    diutak-atik tanpa menyentuh logika. Kalau berkasnya hilang, aplikasinya
    tetap jalan dengan tampilan bawaan Streamlit — bukan error.
    """
    try:
        with open(STYLE_PATH, encoding="utf-8") as handle:
            st.markdown(f"<style>{handle.read()}</style>", unsafe_allow_html=True)
    except OSError:
        pass


_load_style()


def _apply_pending_navigation() -> None:
    """Terapkan perpindahan halaman yang diminta pada run sebelumnya.

    Streamlit melarang mengubah session_state milik sebuah widget setelah widget
    itu dibuat, jadi tombol "Load Result" di History tidak bisa langsung
    memindahkan sidebar. Ia menitipkan tujuannya di `pending_nav`, dan fungsi ini
    menerapkannya di awal run berikutnya — sebelum sidebar dan input URL dibuat.
    """
    pending = st.session_state.pop("pending_nav", None)
    if pending:
        st.session_state["nav_page"] = pending["page"]
        st.session_state["url_input"] = pending["url"]


_apply_pending_navigation()


# ---------------------------------------------------------------- pipeline

def run_pipeline(
    src: str,
    job_id: int,
    video_id: str,
    title: str,
    min_seconds: int = MIN_CLIP_SECONDS,
    max_seconds: int = MAX_CLIP_SECONDS,
    performance_hint: str | None = None,
    mode: str = gemini_analyzer.MODE_REGULAR,
    campaign: dict | None = None,
    language: str | None = WHISPER_LANGUAGE,
) -> list[dict]:
    is_local = source.detect_type(src) == source.LOCAL
    is_campaign = mode in gemini_analyzer.CAMPAIGN_MODES

    progress = st.progress(0)
    status = st.empty()
    detail = st.empty()

    def update(step: int, label: str) -> None:
        progress.progress(step / TOTAL_STEPS)
        status.text(f"[{step}/{TOTAL_STEPS}] {label}")

    update(1, "Membaca info file..." if is_local else "Fetching metadata & comments...")
    meta = source.get_metadata(src, with_comments=True)
    mentions = comment_analyzer.extract_timestamp_mentions(meta["comments"])
    set_job_has_comments(job_id, bool(mentions))
    if is_local:
        detail.caption(
            "File lokal — tanpa komentar, bobot sinyal akan dinormalisasi ulang"
        )
    else:
        detail.caption(
            f"{len(meta['comments'])} komentar · {len(mentions)} mention timestamp · "
            f"{len(meta['chapters'])} chapter"
        )

    update(2, "Mengekstrak audio..." if is_local else "Downloading audio...")
    audio_path = source.prepare_audio(src, video_id)

    update(3, "Transcribing (faster-whisper GPU)...")
    transcript = transcribe(audio_path, language=language)
    if not transcript["segments"]:
        raise Exception(
            "Transcript kosong — tidak ada ucapan terdeteksi. Video musik atau "
            "instrumental tidak cocok untuk tool ini."
        )

    update(4, "Building natural windows...")
    # Batas window diambil dari jeda bicara di AUDIO, bukan dari jeda antar
    # segmen transcript — segmen faster-whisper berdempet sehingga jeda itu
    # hampir selalu nol.
    pauses = audio_analyzer.detect_pauses(audio_path)
    windows = build_windows(transcript["segments"], pauses=pauses)
    detail.caption(
        f"{len(transcript['segments'])} segmen · {len(pauses)} jeda terdeteksi "
        f"→ {len(windows)} window natural · bahasa: {transcript['language']}"
        + ("" if transcript.get("language_forced") else " (deteksi otomatis)")
    )

    # Salah deteksi bahasa tidak melempar error — ia diam-diam menerjemahkan
    # seluruh transcript, dan baru ketahuan saat title/description terlanjur
    # berbahasa asing. Karena itu keraguannya diangkat ke permukaan di sini.
    prob = transcript.get("language_probability") or 1.0
    if not transcript.get("language_forced") and prob < WHISPER_LANGUAGE_MIN_PROB:
        st.warning(
            f"Bahasa dideteksi otomatis sebagai **{transcript['language']}** "
            f"dengan keyakinan hanya {prob:.0%}. Deteksi Whisper cuma membaca 30 "
            f"detik pertama — kalau video ini dibuka intro bermusik, tebakannya "
            f"bisa meleset dan seluruh transcript akan ikut TERJEMAHAN, bukan "
            f"salinan. Kalau hasilnya nanti berbahasa asing, ulangi dengan "
            f"bahasa dikunci."
        )

    update(5, "Scoring signals...")
    windows = comment_analyzer.score_windows(windows, mentions, meta["chapters"])
    windows = audio_analyzer.score_windows(audio_path, windows)
    windows = narrative_analyzer.score_windows(windows)
    # Di mode campaign, pola iklan JUSTRU bahan yang dicari — kalau tetap
    # dibuang di sini, window produknya hilang sebelum Gemini melihatnya.
    windows = text_analyzer.score_windows(windows, allow_ads=is_campaign)
    top_windows, fusion_stats = fuse(windows)
    save_segments(job_id, top_windows)

    message = (
        f"{fusion_stats['total_windows']} window · "
        f"{fusion_stats['disqualified']} dibuang (iklan/boilerplate) · "
        f"{fusion_stats['selected']} dikirim ke AI"
    )
    if not fusion_stats["has_comments"]:
        message += " · tanpa data komentar, bobot dinormalisasi ulang"
    detail.caption(message)

    if not top_windows:
        raise Exception(
            "Semua window tersaring habis. Kemungkinan video terlalu pendek "
            "atau isinya iklan semua."
        )

    update(6, "AI judging (Gemini)...")
    # Rentang yang sama dioper ke dua tempat: Gemini memakainya untuk MEMILIH,
    # validator untuk MENEGAKKAN. Kalau berbeda, kandidat dibuang sia-sia.
    raw = analyze_gemini(
        top_windows,
        transcript["segments"],
        title,
        transcript["language"],
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        performance_hint=performance_hint,
        mode=mode,
        campaign=campaign,
    )
    # Mengirimkan word_data (kata atau segmen) agar validator dapat melakukan word-level snapping
    word_data = transcript.get("words") or transcript.get("segments", [])

    candidates, val_stats = validate(
        raw,
        word_data,
        top_windows,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        campaign_mode=is_campaign,
    )
    save_candidates(job_id, candidates)

    if is_campaign and not candidates:
        raise Exception(
            f"Tidak ada clip yang memuat \"{(campaign or {}).get('product', 'produk')}\" "
            f"di video ini. Ini bukan error — produknya memang tidak cukup "
            f"terlihat atau tidak cukup dibahas untuk jadi bahan clip campaign. "
            f"Coba video campaign yang lain."
        )

    if gemini_client.LAST_MODEL and gemini_client.LAST_MODEL != GEMINI_MODEL:
        st.warning(
            f"Kuota `{GEMINI_MODEL}` habis — analisis ini dikerjakan "
            f"`{gemini_client.LAST_MODEL}`. Hasilnya bisa sedikit berbeda "
            f"kualitasnya. Jatah harian pulih otomatis besok."
        )

    detail.caption(
        f"Gemini kirim {val_stats['received']} → "
        f"snap -{val_stats['dropped_snap']} · "
        f"durasi -{val_stats['dropped_duration']} · "
        f"dedupe -{val_stats['dropped_dedupe']} · "
        f"tidak berdiri sendiri -{val_stats.get('dropped_quality', 0)} · "
        f"variasi -{val_stats['dropped_diversity']} → "
        + (
            f"produk tak terlihat -{val_stats['dropped_campaign']} → "
            if val_stats.get("dropped_campaign") else ""
        )
        + f"{val_stats['final']} final"
        + (
            f" · {val_stats['dropped_hook']} titik hook dibuang (di luar clip)"
            if val_stats.get("dropped_hook") else ""
        )
    )

    # Audio hasil ekstraksi/unduhan dibuang; file sumber lokal tidak pernah
    # disentuh karena memang tidak pernah disalin ke sini.
    cleanup_tmp(video_id)
    update_job_status(job_id, "done")
    progress.progress(1.0)
    status.text("✅ Analysis complete!")

    return get_candidates_by_job(job_id)


def _output_folder(video_title: str) -> str:
    folder = os.path.join(OUTPUT_DIR, sanitize_filename(video_title))
    os.makedirs(folder, exist_ok=True)
    return folder


def _handle_download(
    src: str, parts: list[dict], index: int, video_title: str, candidate: dict,
    campaign: dict | None = None,
) -> None:
    output_folder = _output_folder(video_title)

    start_label = parts[0]["start"].replace(":", "-")
    filename = f"clip_{index:02d}_{start_label}.mp4"
    output_path = os.path.join(output_folder, filename)

    label = f"Memproses clip #{index}..."
    if len(parts) > 1:
        label = f"Memproses clip #{index} ({len(parts)} bagian, lalu disambung)..."

    with st.spinner(label):
        try:
            result_path = source.extract_clip(src, parts, output_path)
        except Exception as e:
            st.error(f"Download gagal: {e}")
            return

    # Laporan analisis ditulis berdampingan dengan videonya. Tanpa ini, file
    # .mp4 di folder output tidak membawa apa pun soal kenapa ia dipilih.
    report_path = None
    try:
        report_path = clip_report.write_report(
            result_path,
            clip_report.build_report(
                candidate, video_title, source.describe(src), index, campaign
            ),
        )
    except Exception as e:
        st.warning(f"Video tersimpan, tapi berkas analisis gagal ditulis: {e}")

    if candidate.get("id"):
        update_candidate_status(candidate["id"], "downloaded", result_path)

    st.success(f"✅ Tersimpan: `{result_path}`")
    if report_path:
        st.caption(f"📄 Analisis: `{os.path.basename(report_path)}`")


# ------------------------------------------------------------ unduh manual

def _handle_full_download(src: str, meta: dict) -> None:
    """Unduh video utuh. Tidak lewat pipeline, tidak menyentuh DB kandidat."""
    if not source.needs_full_download(src):
        st.info(
            f"File lokal tidak perlu diunduh — sudah ada di disk:\n\n`{src}`\n\n"
            f"Clipodify sengaja tidak menyalinnya supaya tidak menggandakan file besar."
        )
        return

    folder = _output_folder(meta["title"])
    output_path = os.path.join(folder, f"full_{sanitize_filename(meta['title'])}.mp4")

    bar = st.progress(0.0)
    label = st.empty()

    def hook(status: dict) -> None:
        if status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes") or 0
        if total:
            bar.progress(min(done / total, 1.0))
            label.caption(f"{done / 1e6:.0f} MB / {total / 1e6:.0f} MB")
        else:
            label.caption(f"{done / 1e6:.0f} MB terunduh")

    try:
        result = source.download_full(src, output_path, progress_hook=hook)
        bar.progress(1.0)
        size_mb = os.path.getsize(result) / 1e6
        st.success(f"✅ Tersimpan ({size_mb:.0f} MB): `{result}`")
    except Exception as e:
        st.error(f"Download video penuh gagal: {e}")


def _handle_manual_clip(src: str, meta: dict, start: str, end: str) -> None:
    """Potong sesuai timestamp yang diketik user, lewat jalur yang sama dengan clip AI."""
    try:
        start_seconds = timestamp_to_seconds(start)
        end_seconds = timestamp_to_seconds(end)
    except ValueError as e:
        st.error(f"{e}. Pakai format HH:MM:SS atau MM:SS.")
        return

    if end_seconds <= start_seconds:
        st.error("Waktu selesai harus setelah waktu mulai.")
        return

    duration = meta.get("duration") or 0
    if duration and start_seconds >= duration:
        st.error(
            f"Waktu mulai ({start}) melewati durasi video "
            f"({seconds_to_timestamp(duration)})."
        )
        return
    if duration and end_seconds > duration:
        st.warning(
            f"Waktu selesai melewati akhir video — dipotong ke "
            f"{seconds_to_timestamp(duration)}."
        )
        end_seconds = duration
        end = seconds_to_timestamp(duration)

    parts = [
        {
            "start": start,
            "end": end,
            "start_seconds": start_seconds,
            "duration_seconds": end_seconds - start_seconds,
        }
    ]

    folder = _output_folder(meta["title"])
    filename = f"manual_{start.replace(':', '-')}_{end.replace(':', '-')}.mp4"
    output_path = os.path.join(folder, filename)

    span = seconds_to_timestamp(end_seconds - start_seconds)
    with st.spinner(f"Memotong {start} → {end} ({span})..."):
        try:
            result = source.extract_clip(src, parts, output_path)
            st.success(f"✅ Tersimpan: `{result}`")
        except Exception as e:
            st.error(f"Potong manual gagal: {e}")


def render_download_page() -> None:
    """Halaman unduh manual — sengaja terpisah dari Analyze.

    Tidak menyentuh pipeline, Gemini, maupun tabel `jobs`. Input sumbernya
    sendiri (`dl_input`), bukan milik halaman Analyze, supaya mengunduh sesuatu
    di sini tidak mengubah video yang sedang dianalisis di sebelah.
    """
    st.title("⬇️ Unduh Manual")
    st.caption("Video utuh atau potongan timestamp sendiri — tanpa analisis AI")

    raw_input = st.text_input(
        "YouTube URL atau path file lokal",
        placeholder="https://www.youtube.com/watch?v=...  atau  /home/kamu/Videos/podcast.mp4",
        key="dl_input",
    )
    if not raw_input:
        st.stop()

    src = source.normalize(raw_input)
    problem = source.validate(src)
    if problem:
        st.error(problem)
        st.stop()

    with st.spinner("Membaca info video..."):
        try:
            meta = source.get_metadata(src)
        except Exception as e:
            st.error(f"Gagal membaca video: {e}")
            st.stop()

    duration_label = seconds_to_timestamp(meta.get("duration") or 0)

    st.subheader(meta["title"])
    bits = [f"Durasi: {duration_label}"]
    if meta.get("resolution"):
        bits.append(f"Resolusi: {meta['resolution']}")
    st.caption(" · ".join(bits) + f" · Sumber: {source.describe(src)}")

    st.divider()

    st.markdown("### Video utuh")
    if source.needs_full_download(src):
        st.caption(
            f"Durasi {duration_label}. Ukurannya bisa ratusan MB sampai beberapa GB."
        )
    else:
        st.caption("Sumbernya file lokal — sudah utuh di disk, tidak perlu diunduh.")

    if st.button("⬇️ Unduh video penuh", key="full_dl"):
        _handle_full_download(src, meta)

    st.divider()

    st.markdown("### Potongan timestamp sendiri")
    st.caption(
        f"Format HH:MM:SS atau MM:SS. Batas video: 00:00:00 – {duration_label}. "
        f"Potongannya presisi ke detik yang kamu tulis — tanpa snap ke transcript "
        f"dan tanpa bantalan ekor, beda dengan clip hasil AI."
    )

    col_start, col_end = st.columns(2)
    start = col_start.text_input("Mulai", value="00:00:00", key="manual_start")
    end = col_end.text_input("Selesai", value="00:01:00", key="manual_end")

    if st.button("✂️ Potong & unduh", key="manual_dl", type="primary"):
        _handle_manual_clip(src, meta, start.strip(), end.strip())


# ------------------------------------------------------------ kartu clip

def _parts_of(cand: dict) -> list[dict]:
    return cand.get("parts") or [
        {
            "start": cand["start"],
            "end": cand["end"],
            "start_seconds": cand["start_seconds"],
            "duration_seconds": cand["duration_seconds"],
        }
    ]


def _render_player(src: str, cand: dict, index: int, video_id: str, parts: list[dict]):
    """Pemutar preview, berbeda jalur untuk YouTube dan file lokal."""
    preview_start = max(0, int(cand["start_seconds"]) - 3)

    if source.detect_type(src) == source.YOUTUBE:
        replay_key = f"replay_{video_id}_{index}"
        nonce = st.session_state.get(replay_key, 0)
        st.iframe(
            youtube_embed_url(
                video_id,
                preview_start,
                # Autoplay hanya setelah user menekan "putar dari awal". Saat
                # halaman pertama dimuat semua player harus diam — belasan
                # iframe autoplay berbunyi berbarengan.
                autoplay=nonce > 0,
                nonce=nonce,
            ),
            height=320,
        )
        return replay_key, nonce

    # Sumber lokal. File aslinya tidak pernah dioper ke Streamlit: st.video
    # memuat seluruh isi file ke memori, jadi video 2 GB akan menghabiskan RAM.
    # Yang diputar adalah preview 360p kecil yang dibuat saat diminta.
    preview_key = f"preview_{video_id}_{index}"
    preview_path = st.session_state.get(preview_key)

    if preview_path and os.path.exists(preview_path):
        st.video(preview_path)
    else:
        if len(parts) > 1:
            st.caption(
                f"Preview belum dibuat — untuk clip {len(parts)} bagian, preview "
                "menampilkan hasil sambungannya yang sebenarnya."
            )
        else:
            st.caption("Preview belum dibuat.")
        if st.button("▶ Buat Preview", key=f"mkprev_{index}", width="stretch"):
            with st.spinner("Menyiapkan preview..."):
                try:
                    st.session_state[preview_key] = source.make_preview(
                        src, parts, video_id, index + 1
                    )
                except Exception as e:
                    st.error(f"Gagal membuat preview: {e}")
                    return None, 0
            st.rerun()

    return None, 0


def _render_card(
    cand: dict, index: int, src: str, video_id: str, video_title: str,
    campaign: dict | None = None,
) -> None:
    parts = _parts_of(cand)
    is_youtube = source.detect_type(src) == source.YOUTUBE

    with st.container(border=True):
        span = f"`{cand['start']} → {cand['end']}`"
        if len(parts) > 1:
            span += f" &nbsp; `✂ {len(parts)} bagian`"
        st.markdown(
            f"**#{index + 1} — {cand['final_score']}/10** &nbsp; "
            f"`{cand.get('clip_type', '?')}` &nbsp; `{int(cand['duration_seconds'])}s`"
        )
        st.caption(span.replace("`", ""))

        # Skor reaksi ditampilkan berdampingan dengan final_score, bukan
        # dilebur ke dalamnya. Clip 9/10 dengan shareability 3 adalah clip yang
        # akan ditonton habis lalu berhenti di situ — dan itu justru pola yang
        # perlu terlihat, bukan tersembunyi di balik satu angka gabungan.
        share_score = cand.get("shareability")
        talk_score = cand.get("discussion_trigger")
        if share_score is not None or talk_score is not None:
            def _mark(value) -> str:
                if value is None:
                    return "–"
                return f"{value}/10" + ("  ⚠️" if value < 5 else "")

            st.markdown(
                f"📤 **Layak dibagikan:** {_mark(share_score)} &nbsp;&nbsp; "
                f"💬 **Memancing komentar:** {_mark(talk_score)}"
            )

            # Subskor mutu ditaruh di expander: ini bahan diagnosis saat sebuah
            # clip terasa aneh, bukan angka yang perlu dibaca tiap kali.
            quality = [
                ("Berdiri sendiri", cand.get("standalone_context")),
                ("Kekuatan hook", cand.get("hook_strength")),
                ("Kepuasan ujung", cand.get("payoff_strength")),
            ]
            if any(value is not None for _, value in quality):
                with st.expander("Rincian skor mutu"):
                    st.markdown("  \n".join(
                        f"**{label}:** {_mark(value)}" for label, value in quality
                    ))
                    st.caption(
                        "Berdiri sendiri adalah gerbang: di bawah "
                        f"{QUALITY_MIN_STANDALONE:.0f} kandidat dibuang sebelum "
                        "diperingkat, berapa pun skor lainnya."
                    )

        st.markdown(f"> {cand['hook']}")

        replay_key, nonce = _render_player(src, cand, index, video_id, parts)

        # Rincian potongan ditampilkan SELALU, termasuk untuk clip satu bagian.
        # Sebelumnya hanya muncul saat multi-bagian, sehingga hampir tidak
        # pernah terlihat — padahal "detik mana sampai detik mana" adalah hal
        # pertama yang dibutuhkan saat memotong sendiri di editor.
        rows = [f"**{len(parts)} potongan** — total {int(cand['duration_seconds'])}s"]
        for n, part in enumerate(parts, 1):
            rows.append(
                f"Potongan {n}: `{part['start']}` → `{part['end']}` "
                f"({int(part['duration_seconds'])}s)"
            )
            if n < len(parts):
                gap = (
                    parts[n]["start_seconds"] - part["start_seconds"]
                    - part["duration_seconds"]
                )
                rows.append(f"&nbsp;&nbsp;&nbsp;✂ dibuang {int(gap)}s")
        st.markdown("  \n".join(rows))

        # Skor kecocokan campaign ditampilkan berdampingan dengan final_score,
        # bukan menggantikannya: keduanya harus tinggi. Clip 9/10 dengan
        # kecocokan 4 adalah clip yang bagus untuk campaign orang lain.
        if cand.get("campaign_fit") is not None:
            fit_note = f"🎯 **Kecocokan produk: {cand['campaign_fit']}/10**"
            if cand.get("product_moment"):
                fit_note += f" — muncul di `{cand['product_moment']}`"
            st.markdown(fit_note)

        if cand.get("hook_start") and cand.get("hook_end"):
            moved = cand.get("hook_placement") == "pindahkan_ke_awal"
            note = (
                "⚡ pindahkan ke paling depan saat edit"
                if moved else "✓ sudah jadi pembuka clip"
            )
            st.markdown(
                f"🎣 **Hook:** `{cand['hook_start']}` → `{cand['hook_end']}` — {note}"
            )

        scores = cand.get("segment_scores") or {}
        readings = []
        for label, key in (
            ("Comment", "score_comment"),
            ("Audio", "score_audio"),
            ("Narrative", "score_narrative"),
            ("Text", "score_text"),
        ):
            value = scores.get(key)
            readings.append(f"{label} {'n/a' if value is None else f'{value:.2f}'}")
        st.caption(" · ".join(readings))

        if cand.get("title"):
            hashtags = clip_report.build_hashtags(cand, campaign)
            tag_line = " ".join(f"#{t}" for t in hashtags)

            with st.expander(f"📝 {cand['title']}", expanded=False):
                # st.code memberi tombol salin bawaan — caption siap tempel
                # ke TikTok/Shorts tanpa perlu blok-dan-copy manual.
                st.caption("Title")
                st.code(cand["title"], language=None, wrap_lines=True)

                st.caption("Description")
                st.code(cand.get("description") or "", language=None, wrap_lines=True)

                if tag_line:
                    st.caption(f"Hashtags ({len(hashtags)})")
                    st.code(tag_line, language=None, wrap_lines=True)

                st.caption("Caption lengkap — siap tempel (sudah ada atribusi)")
                st.code(
                    clip_report.build_caption(cand, video_title, campaign),
                    language=None,
                    wrap_lines=True,
                )

        if cand.get("thumbnail_prompt"):
            with st.expander("🖼️ Bahan thumbnail"):
                st.caption(
                    "Alurnya: screenshot frame pembicara dari clip ini, lampirkan "
                    "ke AI gambar, lalu tempel prompt di bawah."
                )
                if cand.get("thumbnail_text"):
                    st.caption("Teks di gambar")
                    st.code(cand["thumbnail_text"], language=None, wrap_lines=True)
                st.caption("Prompt untuk AI gambar")
                st.code(cand["thumbnail_prompt"], language=None, wrap_lines=True)

        with st.expander("💡 AI Analysis"):
            st.write(cand["reason"])
            for signal, note in (cand.get("signal_notes") or {}).items():
                st.markdown(f"**{signal.capitalize()}:** {note}")

        preview_start = max(0, int(cand["start_seconds"]) - 3)

        if is_youtube:
            # Lebar tidak dibagi rata: di dalam kolom setengah lebar, label yang
            # sedikit lebih panjang akan membungkus dan membuat tombolnya lebih
            # tinggi dari tetangganya.
            c1, c2, c3, c4 = st.columns([1.0, 1.25, 1.1, 0.85])
            if c1.button(
                "↺ Putar",
                key=f"replay_btn_{index}",
                help=f"Kembalikan player ke {cand['start']} dan langsung putar",
                width="stretch",
            ):
                st.session_state[replay_key] = nonce + 1
                st.rerun()
            c2.link_button(
                "↗ YouTube",
                youtube_watch_url(video_id, preview_start),
                help="Buka di tab baru pada timestamp yang sama",
                width="stretch",
            )
            dl_col, skip_col = c3, c4
        else:
            dl_col, skip_col = st.columns(2)

        if dl_col.button("⬇ Unduh", key=f"dl_{index}", width="stretch"):
            _handle_download(src, parts, index + 1, video_title, cand, campaign)

        if skip_col.button("✕ Skip", key=f"skip_{index}", width="stretch"):
            if cand.get("id"):
                update_candidate_status(cand["id"], "skipped")
            st.toast(f"Clip #{index + 1} skipped")


def render_candidates(
    candidates: list[dict], src: str, video_id: str, video_title: str,
    campaign: dict | None = None,
) -> None:
    if not candidates:
        st.warning(
            "Tidak ada kandidat yang lolos validasi. Coba lebarkan rentang durasi "
            "di atas, atau naikkan SNAP_TOLERANCE di .env."
        )
        return

    st.divider()
    st.subheader(f"🎯 {len(candidates)} Clip Recommendations")

    types: dict[str, int] = {}
    for cand in candidates:
        key = cand.get("clip_type") or "?"
        types[key] = types.get(key, 0) + 1
    st.caption("Variasi tipe — " + " · ".join(f"{k}: {v}" for k, v in sorted(types.items())))

    for row_start in range(0, len(candidates), GRID_COLUMNS):
        row = candidates[row_start:row_start + GRID_COLUMNS]
        columns = st.columns(GRID_COLUMNS)
        for offset, cand in enumerate(row):
            with columns[offset]:
                _render_card(
                    cand, row_start + offset, src, video_id, video_title, campaign
                )


# --------------------------------------------------------------- halaman

def _store_session(
    candidates: list[dict], src: str, video_id: str, title: str,
    campaign: dict | None = None,
) -> None:
    st.session_state["candidates"] = candidates
    st.session_state["current_url"] = src
    st.session_state["current_video_id"] = video_id
    st.session_state["current_title"] = title
    # Detail campaign ikut disimpan karena caption memerlukannya kembali saat
    # clip diunduh — termasuk saat hasil lama dimuat ulang dari History, jauh
    # setelah kotak isian campaign-nya kosong lagi.
    st.session_state["current_campaign"] = campaign or {}


def _campaign_of(job: dict) -> dict:
    """Rakit ulang detail campaign dari baris job.

    Dibaca dari database, bukan dari kotak isian, supaya caption clip yang
    diunduh berminggu-minggu kemudian tetap membawa hashtag brand yang sama.
    """
    product = job.get("campaign_product") or ""
    if not product:
        return {}
    try:
        tags = json.loads(job.get("campaign_hashtags") or "[]")
    except (json.JSONDecodeError, TypeError):
        tags = []
    return {"product": product, "hashtags": tags}


MODE_LABELS = {
    "Clip biasa": gemini_analyzer.MODE_REGULAR,
    "Campaign — produk harus terlihat": gemini_analyzer.MODE_CAMPAIGN,
    "Campaign soft-selling — jualan tanpa terasa jualan": gemini_analyzer.MODE_SOFTSELL,
}


def _mode_picker() -> tuple[str, dict]:
    """Pilihan mode analisis, plus detail campaign kalau modenya campaign.

    Mode mengubah tiga hal sekaligus di hilir: pola iklan berhenti dibuang di
    prefilter, Gemini dapat syarat kedua (produk wajib ada), dan blok atribusi
    caption ditukar. Karena itu ia dipilih SEBELUM tombol Analyze, bukan
    sesudahnya — hasil analisis reguler tidak bisa diubah jadi campaign.
    """
    label = st.radio(
        "Jenis clip",
        list(MODE_LABELS.keys()),
        key="clip_mode",
        horizontal=False,
    )
    mode = MODE_LABELS[label]

    if mode == gemini_analyzer.MODE_REGULAR:
        return mode, {}

    with st.container(border=True):
        product = st.text_input(
            "Produk / brand yang harus ada di clip",
            placeholder="contoh: Somethinc Copper Peptide",
            key="campaign_product",
            help=(
                "Ditulis seperti yang disebut di video. Clip yang tidak memuat "
                "ini akan dibuang, sebagus apa pun dia sebagai short."
            ),
        )
        benefit = st.text_input(
            "Nilai jual yang ingin disampaikan (opsional)",
            placeholder="contoh: bikin kulit lebih kenyal dalam 2 minggu",
            key="campaign_benefit",
        )
        cta = st.text_input(
            "Ajakan di description (opsional)",
            placeholder="contoh: cek keranjang kuning",
            key="campaign_cta",
        )
        raw_tags = st.text_input(
            "Hashtag wajib dari brand (opsional)",
            placeholder="#cavemensgrooming #keranjangkuning sabunpria",
            key="campaign_hashtags",
            help=(
                "Tempel apa adanya dari brief brand — pemisahnya boleh koma atau "
                "spasi, tanda pagar boleh ada boleh tidak. Tag ini ditaruh paling "
                "depan, menggantikan hashtag tetap channelmu, karena tag campaign "
                "sering jadi syarat pembayaran."
            ),
        )
        brand_tags = clip_report.parse_hashtags(raw_tags)
        if brand_tags:
            st.caption("Akan dipakai: " + " ".join(f"#{t}" for t in brand_tags))
        else:
            st.caption(
                "Kosong — dipakai cadangan "
                + " ".join(f"#{t}" for t in CLIP_CAMPAIGN_HASHTAGS)
                + " (tanpa #podcast, karena clip campaign belum tentu podcast)."
            )

        if mode == gemini_analyzer.MODE_SOFTSELL:
            st.caption(
                f"Soft-selling: maksimal "
                f"{int(SOFTSELL_MAX_PITCH_RATIO * 100)}% durasi clip boleh berisi "
                f"pitch, dan {SOFTSELL_CLEAN_OPENING_SECONDS} detik pertama harus "
                f"bersih dari jualan. Title/description dilarang berbunyi iklan."
            )
        else:
            st.caption(
                "Hook tetap diambil dari momen terkuat, bukan dari momen produk — "
                "itu sebab paling umum clip campaign gagal viral. Kalau momen kuat "
                "dan momen produk berjauhan, Gemini diminta menyambungnya jadi clip "
                "beberapa bagian."
            )

        if not product:
            st.warning(
                "Isi nama produknya dulu. Tanpa itu tidak ada yang bisa dicari, "
                "dan hasilnya akan sama saja dengan mode biasa."
            )

    return mode, {"product": product.strip(), "benefit": benefit.strip(),
                  "cta": cta.strip(), "hashtags": brand_tags}


def render_analyze_page() -> None:
    # Judul halaman, bukan nama aplikasi: mereknya sudah ada di sidebar, dan
    # mengulangnya di sini cuma memakan baris pertama tanpa memberi tahu apa pun.
    st.title("🎬 Analisis Clip")
    st.caption("Tempel sumbernya, pilih jenis clip, lalu jalankan analisis")

    raw_input = st.text_input(
        "YouTube URL atau path file lokal",
        placeholder="https://www.youtube.com/watch?v=...  atau  /home/kamu/Videos/podcast.mp4",
        key="url_input",
        help=(
            "File lokal tidak disalin ke mana pun — hanya dibaca di tempat. "
            "Yang masuk folder sementara cuma audio hasil ekstraksi."
        ),
    )
    if not raw_input:
        st.stop()

    src = source.normalize(raw_input)
    problem = source.validate(src)
    if problem:
        st.error(problem)
        st.stop()

    is_local = source.detect_type(src) == source.LOCAL

    with st.spinner("Membaca info video..."):
        try:
            meta = source.get_metadata(src)
        except Exception as e:
            st.error(f"Gagal membaca video: {e}")
            st.stop()

    if meta.get("thumbnail"):
        col_thumb, col_info = st.columns([1, 3])
        with col_thumb:
            st.image(meta["thumbnail"], width="stretch")
        info = col_info
    else:
        info = st.container()

    with info:
        st.subheader(meta["title"])
        bits = [f"Durasi: {seconds_to_timestamp(meta['duration'])}"]
        if meta.get("resolution"):
            bits.append(f"Resolusi: {meta['resolution']}")
        if meta.get("uploader"):
            bits.append(("Folder: " if is_local else "Uploader: ") + meta["uploader"])
        st.caption(" · ".join(bits))
        st.caption(f"Sumber: **{source.describe(src)}**")

    if is_local:
        st.info(
            "File lokal tidak punya komentar, jadi sinyal komentar dilewati dan "
            "bobot tiga sinyal lain dinormalisasi ulang secara otomatis."
        )

    existing = get_job_by_url(src)
    # Niat "analisis ulang" WAJIB disimpan di session, bukan dibaca dari nilai
    # tombolnya. Nilai tombol hanya True pada rerun yang dipicu kliknya sendiri;
    # begitu user menyentuh kontrol lain di bawah — pilihan bahasa, jenis clip,
    # kotak campaign — rerun berikutnya membuatnya False lagi dan halaman
    # berhenti sebelum tombol Analyze sempat terbaca.
    wants_reanalyze = st.session_state.get("reanalyze_for") == src
    if existing and existing["status"] == "done" and not wants_reanalyze:
        cached = get_candidates_by_job(existing["id"])
        if cached and st.session_state.get("current_url") != src:
            cached_mode = existing.get("mode") or gemini_analyzer.MODE_REGULAR
            cached_product = existing.get("campaign_product") or ""
            note = f"✅ Hasil cached ditemukan ({len(cached)} kandidat)."
            if cached_mode != gemini_analyzer.MODE_REGULAR:
                note += f" Dianalisis sebagai campaign **{cached_product}**."
            else:
                note += " Dianalisis sebagai clip biasa."
            note += " Ganti jenis clip di bawah butuh **Analisis Ulang**."
            st.info(note)
            col_a, col_b = st.columns(2)
            if col_a.button("Gunakan Hasil Cached", type="primary"):
                _store_session(
                    cached, src, meta["video_id"], meta["title"],
                    _campaign_of(existing),
                )
                st.rerun()
            if col_b.button("Analisis Ulang"):
                st.session_state["reanalyze_for"] = src
                st.rerun()
            st.stop()

    if wants_reanalyze:
        col_note, col_cancel = st.columns([4, 1])
        col_note.info(
            "🔁 Mode analisis ulang — hasil lama untuk video ini akan ditimpa "
            "begitu kamu tekan Analyze."
        )
        if col_cancel.button("Batal", key="cancel_reanalyze"):
            st.session_state.pop("reanalyze_for", None)
            st.rerun()

    mode, campaign = _mode_picker()

    lang_labels = {
        "Indonesia (dikunci)": "id",
        "English (dikunci)": "en",
        "Deteksi otomatis": None,
    }
    default_lang = next(
        (i for i, code in enumerate(lang_labels.values()) if code == WHISPER_LANGUAGE),
        2,
    )
    language = lang_labels[
        st.selectbox(
            "Bahasa transcript",
            list(lang_labels.keys()),
            index=default_lang,
            key="whisper_lang",
            help=(
                "Deteksi otomatis Whisper hanya membaca 30 detik pertama. Kalau "
                "video dibuka narasi bermusik atau intro, tebakannya bisa meleset "
                "— dan Whisper lalu MENERJEMAHKAN seluruh video, bukan menyalinnya. "
                "Kunci bahasanya kalau kamu sudah tahu isinya bahasa apa."
            ),
        )
    ]

    min_seconds, max_seconds = st.slider(
        "Rentang durasi clip (detik)",
        min_value=15,
        max_value=120,
        value=(MIN_CLIP_SECONDS, MAX_CLIP_SECONDS),
        step=5,
        key="clip_range",
        help=(
            "Rentang ini dipakai dua kali: Gemini memilih clip di dalamnya, lalu "
            "validator membuang yang tetap di luar. Rentang lebar memberi bahan "
            "mentah yang longgar untuk diedit sendiri; rentang sempit memberi clip "
            "yang lebih siap upload."
        ),
    )
    span = max_seconds - min_seconds
    if span <= 20:
        st.caption(
            f"⚠️ Rentang {min_seconds}–{max_seconds}s cukup sempit. Clip yang bagus "
            f"tapi sedikit di luar rentang akan dibuang, jadi jumlah hasil bisa turun."
        )
    else:
        st.caption(
            f"Target {min_seconds}–{max_seconds} detik. Kalau dua titik potong "
            f"sama kuatnya, Gemini diminta memilih yang lebih pendek."
        )

    # Umpan balik dari hasil upload sendiri. Hanya muncul kalau memang ada
    # datanya — tanpa itu, checkbox yang tidak melakukan apa-apa cuma menipu.
    performance_hint = channel_advisor.build_performance_hint(get_yt_videos())
    use_hint = False
    if performance_hint:
        use_hint = st.checkbox(
            "Pelajari dari performa uploadku di YouTube",
            value=True,
            key="use_hint",
            help=(
                "Retensi clip yang sudah kamu upload ikut dikirim sebagai konteks, "
                "supaya pemilihan condong ke pola yang terbukti jalan di channelmu."
            ),
        )
        if use_hint:
            with st.expander("Lihat data yang akan dikirim"):
                st.code(performance_hint, language=None)
    else:
        st.caption(
            "💡 Hubungkan YouTube di **Analitik YouTube** supaya pemilihan clip "
            "bisa belajar dari retensi uploadmu sendiri."
        )

    is_campaign = mode in gemini_analyzer.CAMPAIGN_MODES
    if st.button(
        "🔍 Analyze", type="primary", disabled=is_campaign and not campaign["product"]
    ):
        job_id = create_job(
            src,
            meta["video_id"],
            meta["title"],
            meta["duration"],
            meta["thumbnail"],
            source_type=source.detect_type(src),
            mode=mode,
            campaign_product=campaign.get("product", ""),
            campaign_hashtags=campaign.get("hashtags") or [],
        )
        update_job_status(job_id, "processing")
        try:
            candidates = run_pipeline(
                src,
                job_id,
                meta["video_id"],
                meta["title"],
                min_seconds=min_seconds,
                max_seconds=max_seconds,
                performance_hint=performance_hint if use_hint else None,
                mode=mode,
                campaign=campaign,
                language=language,
            )
            _store_session(
                candidates, src, meta["video_id"], meta["title"],
                campaign,
            )
            # Niatnya sudah terpenuhi. Tanpa ini, membuka video yang sama besok
            # akan langsung masuk mode analisis ulang tanpa menawarkan cache.
            st.session_state.pop("reanalyze_for", None)
        except Exception as e:
            update_job_status(job_id, "error")
            st.error(f"Pipeline error: {e}")
            st.stop()

    if st.session_state.get("current_url") == src:
        render_candidates(
            st.session_state["candidates"],
            src,
            st.session_state["current_video_id"],
            st.session_state["current_title"],
            st.session_state.get("current_campaign") or {},
        )


def render_history_page() -> None:
    st.title("📋 History")

    jobs = get_all_jobs()
    if not jobs:
        st.info("Belum ada video yang pernah diproses.")
        return

    for job in jobs:
        is_local = (job.get("source_type") or "youtube") == "local"
        icon = "📁" if is_local else "▶"
        label = f"{icon} {job['title']} — {(job['created_at'] or '')[:10]}"

        with st.expander(label):
            candidates = get_candidates_by_job(job["id"])
            downloaded = [c for c in candidates if c.get("status") == "downloaded"]

            col_a, col_b, col_c = st.columns([2, 1, 1])
            col_a.write(f"**Sumber:** {job['url']}")
            col_b.write(f"**Status:** {job['status']}")
            col_c.write(f"**Downloaded:** {len(downloaded)}/{len(candidates)}")

            if is_local and not os.path.exists(job["url"]):
                st.warning(
                    "File sumbernya sudah tidak ada di path itu. Hasil analisis "
                    "tetap bisa dibaca, tapi Download dan Preview butuh filenya "
                    "kembali di lokasi yang sama."
                )

            if not job["has_comments"] and not is_local:
                st.caption(
                    "⚠️ Diproses tanpa data komentar — bobot dinormalisasi ulang"
                )

            if not candidates:
                st.caption(
                    "Belum ada kandidat tersimpan untuk job ini — "
                    "kemungkinan analisisnya gagal di tengah jalan."
                )
            elif st.button("🔄 Load Result", key=f"load_{job['id']}"):
                _store_session(
                    candidates, job["url"], job["video_id"], job["title"],
                    _campaign_of(job),
                )
                # Pindah ke halaman Analyze sekaligus isikan sumbernya, kalau tidak
                # hasilnya termuat tapi tidak terlihat di mana pun.
                st.session_state["pending_nav"] = {
                    "page": "Analyze",
                    "url": job["url"],
                }
                st.rerun()


def _mask(value: str) -> str:
    """Tampilkan key secukupnya untuk dikenali, tidak cukup untuk dipakai."""
    if len(value) <= 12:
        return "•" * len(value)
    return f"{value[:6]}{'•' * 8}{value[-4:]}"


def _tmp_usage() -> tuple[int, int]:
    total = 0
    count = 0
    for name in os.listdir(TMP_DIR) if os.path.isdir(TMP_DIR) else []:
        path = os.path.join(TMP_DIR, name)
        if os.path.isfile(path):
            total += os.path.getsize(path)
            count += 1
    return count, total


def render_settings_page() -> None:
    st.title("⚙️ Settings")

    st.subheader("API Key Gemini")
    st.caption(
        "Key disimpan di database lokal `db/clipodify.db` dalam bentuk teks biasa — "
        "sama seperti `.env`, jadi jangan bagikan file itu. Key di `.env` tetap "
        "dipakai sebagai cadangan kalau daftar di bawah kosong."
    )

    keys = get_api_keys()

    if keys:
        active_id = next((k["id"] for k in keys if k["is_active"]), keys[0]["id"])
        options = [k["id"] for k in keys]
        labels = {k["id"]: f"{k['name']} — {_mask(k['key_value'])}" for k in keys}

        chosen = st.radio(
            "Key aktif",
            options,
            index=options.index(active_id),
            format_func=lambda kid: labels[kid],
        )
        if chosen != active_id:
            set_active_api_key(chosen)
            st.rerun()

        col_test, col_del = st.columns(2)
        if col_test.button("🔌 Uji Key Aktif", width="stretch"):
            active = next(k for k in keys if k["id"] == chosen)
            with st.spinner("Menghubungi Gemini..."):
                ok, message = test_api_key(active["key_value"])
            (st.success if ok else st.error)(message)

        if col_del.button("🗑 Hapus Key Terpilih", width="stretch"):
            delete_api_key(chosen)
            st.rerun()
    else:
        st.info(
            "Belum ada key tersimpan. Sementara ini Clipodify memakai "
            "GEMINI_API_KEY dari `.env` kalau ada."
        )

    with st.form("add_key", clear_on_submit=True):
        st.markdown("**Tambah key baru**")
        name = st.text_input(
            "Nama", placeholder="akun utama", help="Nama yang sama akan menimpa key lama"
        )
        value = st.text_input("API key", type="password", placeholder="AIza...")
        make_active = st.checkbox("Langsung jadikan key aktif", value=True)

        if st.form_submit_button("💾 Simpan", type="primary"):
            try:
                add_api_key(name, value, make_active=make_active)
                st.success(f"Key '{name}' tersimpan.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    st.caption(
        "Buat key gratis di https://aistudio.google.com/apikey — beberapa key "
        "berguna saat kuota harian salah satunya habis."
    )

    st.divider()
    st.subheader("File sementara")
    count, total = _tmp_usage()
    st.caption(
        f"`{TMP_DIR}` berisi {count} file · {total / 1_048_576:.1f} MB. "
        "Isinya audio hasil ekstraksi dan preview clip — semuanya bisa dibuat "
        "ulang, jadi aman dihapus."
    )
    if count and st.button("🧹 Bersihkan file sementara"):
        removed = 0
        for name in os.listdir(TMP_DIR):
            path = os.path.join(TMP_DIR, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
        # Preview yang path-nya tersimpan di session sudah tidak ada lagi.
        for key in [k for k in st.session_state if k.startswith("preview_")]:
            del st.session_state[key]
        st.success(f"{removed} file dihapus.")
        st.rerun()


def _connect_form() -> None:
    """Layar sebelum terhubung. Tidak meminta secret apa pun lewat form ini —
    hanya PATH ke file kredensial, supaya isinya tidak pernah lewat browser."""
    st.info(
        "Belum terhubung ke YouTube. Yang dibutuhkan cuma sekali setup di Google "
        "Cloud Console."
    )
    with st.expander("Langkah setup", expanded=True):
        st.markdown(
            "1. Buka Google Cloud Console, buat project baru.\n"
            "2. Aktifkan **YouTube Analytics API** dan **YouTube Data API v3**.\n"
            "3. Buat **OAuth client ID** bertipe **Desktop app**, lalu unduh JSON-nya.\n"
            "4. Simpan file itu di luar folder project ini, dan tempel path-nya di bawah."
        )

    path = st.text_input(
        "Path file OAuth client JSON",
        placeholder="/home/kamu/rahasia/client_secret_xxx.json",
        key="yt_secrets_path",
        help=(
            "Yang diminta path-nya, bukan isinya. File itu tidak disalin ke folder "
            "project — hanya dibaca saat menghubungkan."
        ),
    )

    if not st.button("🔗 Hubungkan ke YouTube", type="primary"):
        return

    target = os.path.expanduser((path or "").strip().strip("'\""))
    if not target or not os.path.isfile(target):
        st.error("File tidak ditemukan. Cek lagi path-nya.")
        return

    st.warning(
        "Browser akan terbuka untuk meminta izin. Halaman ini akan tampak "
        "berputar sampai kamu selesai menyetujui di sana — itu normal."
    )
    try:
        with st.spinner("Menunggu persetujuan di browser..."):
            channel = youtube_analytics.connect(target)
        st.success(f"✅ Terhubung sebagai **{channel['channel_title']}**")
        st.rerun()
    except Exception as e:
        st.error(f"Gagal menghubungkan: {e}")


def _refresh_metrics(limit: int) -> None:
    """Tarik ulang daftar video dan metriknya dari YouTube."""
    try:
        creds = youtube_analytics.load_credentials()
        channel = youtube_analytics.get_channel(creds)

        with st.spinner("Mengambil daftar video..."):
            videos = youtube_analytics.list_videos(
                creds, channel["uploads_playlist"], limit=limit
            )
        if not videos:
            st.warning("Channel ini belum punya video.")
            return

        with st.spinner(f"Mengambil metrik {len(videos)} video..."):
            metrics = youtube_analytics.fetch_video_metrics(
                creds, [v["video_id"] for v in videos]
            )
        for video in videos:
            video.update(metrics.get(video["video_id"], {}))

        save_yt_videos(videos)
        st.success(f"✅ {len(videos)} video diperbarui.")
    except Exception as e:
        st.error(f"Gagal mengambil data: {e}")


def _duration_bucket(seconds: float) -> str:
    if not seconds:
        return "?"
    edges = [(30, "<30s"), (45, "30-45s"), (60, "45-60s"), (90, "60-90s")]
    for limit, label in edges:
        if seconds < limit:
            return label
    return ">90s"


def _render_retention_chart(video: dict) -> None:
    """Kurva retensi satu video, sumbu X dalam DETIK bukan rasio.

    Rasio 0-1 dari API tidak bisa dibandingkan antar video dengan panjang
    berbeda — dan justru perbandingan itulah yang dicari: di detik ke berapa
    penonton pergi.
    """
    curve = video.get("retention_curve")
    if not curve:
        st.caption("Kurva retensi belum ditarik untuk video ini.")
        return

    duration = video.get("duration_seconds") or 0
    frame = pd.DataFrame({
        "detik": [point["ratio"] * duration for point in curve],
        "penonton bertahan (%)": [point["watch_ratio"] * 100 for point in curve],
    }).set_index("detik")
    st.line_chart(frame, height=220)

    half = [p for p in curve if p["watch_ratio"] <= 0.5]
    if half:
        drop_at = half[0]["ratio"] * duration
        st.caption(f"Separuh penonton pergi di detik **{drop_at:.0f}**.")


def render_youtube_page() -> None:
    """Analitik channel sendiri — read-only, tidak menulis apa pun ke YouTube."""
    st.title("📊 Analitik YouTube")
    st.caption("Retensi asli dari channelmu, untuk menguji tebakan tool ini")

    if not youtube_analytics.is_connected():
        _connect_form()
        st.stop()

    auth = get_yt_auth()
    col_info, col_action = st.columns([3, 1])
    col_info.subheader(auth["channel_title"])
    col_info.caption(f"Terhubung sejak {auth['connected_at']}")
    if col_action.button("Putuskan koneksi", help="Metrik yang sudah ditarik tetap disimpan"):
        delete_yt_auth()
        st.rerun()

    limit = st.slider("Jumlah video terbaru yang ditarik", 10, 200, 50, step=10)
    if st.button("🔄 Tarik / perbarui data", type="primary"):
        _refresh_metrics(limit)

    videos = get_yt_videos()
    if not videos:
        st.info("Belum ada data. Klik **Tarik / perbarui data** di atas.")
        st.stop()

    st.divider()

    scored = [v for v in videos if v.get("avg_view_percentage") is not None]
    if scored:
        best = max(scored, key=lambda v: v["avg_view_percentage"])
        cols = st.columns(3)
        cols[0].metric("Video terdata", len(videos))
        cols[1].metric(
            "Rata-rata retensi",
            f"{sum(v['avg_view_percentage'] for v in scored) / len(scored):.0f}%",
        )
        cols[2].metric("Retensi tertinggi", f"{best['avg_view_percentage']:.0f}%")

    # Inilah tabel yang menjawab pertanyaan "clip-ku kepanjangan atau tidak":
    # retensi dikelompokkan per rentang durasi, dari data sendiri.
    st.subheader("Retensi per rentang durasi")
    buckets: dict[str, list[float]] = {}
    for video in scored:
        buckets.setdefault(_duration_bucket(video["duration_seconds"]), []).append(
            video["avg_view_percentage"]
        )

    if buckets:
        order = ["<30s", "30-45s", "45-60s", "60-90s", ">90s", "?"]
        summary = pd.DataFrame([
            {
                "durasi": label,
                "jumlah video": len(buckets[label]),
                "retensi rata-rata (%)": round(sum(buckets[label]) / len(buckets[label]), 1),
            }
            for label in order if label in buckets
        ])
        st.dataframe(summary, width="stretch", hide_index=True)
        st.caption(
            "Kalau baris yang lebih pendek konsisten lebih tinggi, itu bukti dari "
            "datamu sendiri bahwa rentang durasi di halaman Analyze perlu diturunkan."
        )

    st.subheader("Per video")
    table = pd.DataFrame([
        {
            "judul": v["title"],
            "durasi": seconds_to_timestamp(v["duration_seconds"] or 0),
            "views": v.get("views"),
            "retensi (%)": (
                round(v["avg_view_percentage"], 1)
                if v.get("avg_view_percentage") is not None else None
            ),
            "rata-rata tonton (detik)": (
                round(v["avg_view_seconds"]) if v.get("avg_view_seconds") else None
            ),
            "like": v.get("likes"),
            "komentar": v.get("comments"),
            "share": v.get("shares"),
            "sub baru": v.get("subs_gained"),
        }
        for v in videos
    ])
    st.dataframe(table, width="stretch", hide_index=True)

    with st.expander("👥 Siapa yang menonton"):
        try:
            audience = youtube_analytics.fetch_audience(
                youtube_analytics.load_credentials()
            )
        except Exception as e:
            st.caption(f"Gagal mengambil data audiens: {e}")
            audience = {}

        if audience.get("demographics"):
            demo = pd.DataFrame(audience["demographics"])
            demo = demo.sort_values("percentage", ascending=False)
            demo.columns = ["umur", "gender", "% penonton"]
            st.dataframe(demo, width="stretch", hide_index=True)

        cols = st.columns(2)
        if audience.get("devices"):
            total = sum(r["views"] for r in audience["devices"]) or 1
            cols[0].caption("**Perangkat**")
            for row in audience["devices"]:
                cols[0].caption(
                    f"{row['device']}: {row['views']} ({row['views'] / total * 100:.0f}%)"
                )
        if audience.get("countries"):
            cols[1].caption("**Negara**")
            for row in audience["countries"][:5]:
                cols[1].caption(
                    f"{row['country']}: {row['views']} view · "
                    f"retensi {row['retention']:.0f}%"
                )

    st.subheader("Kurva retensi")
    # video_id ikut masuk label karena judul yang dipotong bisa kembar — tanpa
    # itu, dua video berjudul mirip akan bertabrakan sebagai kunci dict dan
    # salah satunya hilang dari daftar tanpa pesan apa pun.
    labels = {
        f"{v['title'][:60]} · {seconds_to_timestamp(v['duration_seconds'] or 0)}"
        f" · {v['video_id']}": v
        for v in videos
    }
    choice = st.selectbox("Pilih video", list(labels.keys()))
    video = labels[choice]

    if st.button("📈 Tarik kurva retensi video ini"):
        try:
            with st.spinner("Mengambil kurva..."):
                creds = youtube_analytics.load_credentials()
                curve = youtube_analytics.fetch_retention_curve(creds, video["video_id"])
            if curve:
                save_yt_retention(video["video_id"], curve)
                st.rerun()
            else:
                st.warning(
                    "YouTube tidak mengembalikan data retensi untuk video ini — "
                    "biasanya karena jumlah penontonnya masih terlalu sedikit."
                )
        except Exception as e:
            st.error(f"Gagal mengambil kurva: {e}")

    _render_retention_chart(video)

    st.divider()
    st.subheader("🤖 Saran dari Gemini")
    eligible = sum(
        1 for v in videos
        if v.get("avg_view_percentage") is not None
        and (v.get("views") or 0) >= CHANNEL_MIN_VIEWS
    )
    st.caption(
        f"{eligible} dari {len(videos)} video punya penonton cukup untuk jadi dasar "
        f"kesimpulan. Judul, durasi, views, dan retensi dikirim ke Gemini — "
        f"tidak ada isi video maupun kredensial yang ikut."
    )
    if eligible < 8:
        st.warning(
            f"Baru {eligible} video yang layak disimpulkan. Saran yang keluar akan "
            f"berupa hipotesis, bukan temuan — dan memang seharusnya begitu."
        )

    st.markdown("**Saran clip** — momen mana yang ditonton, dari metrik retensi.")
    if st.button("💡 Minta saran clip", key="advise"):
        try:
            with st.spinner("Gemini membaca data channel..."):
                st.session_state["yt_advice"] = channel_advisor.advise(
                    videos, {"channel_title": auth["channel_title"]}
                )
        except Exception as e:
            st.error(f"Gagal meminta saran: {e}")

    if st.session_state.get("yt_advice"):
        st.markdown(st.session_state["yt_advice"])

    st.divider()
    st.markdown(
        "**Saran setup channel** — identitas, deskripsi, tag, dan konsistensi, "
        "ditimbang terhadap sumber trafik yang sebenarnya."
    )
    st.caption(
        "Tidak memakai data retensi, jadi ini tetap berguna walau video yang "
        "layak disimpulkan masih sedikit. CTR thumbnail tidak diikutkan karena "
        "YouTube tidak menyediakannya lewat API."
    )
    if st.button("🛠️ Periksa setup channel", key="advise_setup"):
        try:
            with st.spinner("Mengambil metadata channel..."):
                creds = youtube_analytics.load_credentials()
                snapshot = youtube_analytics.fetch_setup_snapshot(creds, limit=limit)
            with st.spinner("Gemini memeriksa setup..."):
                st.session_state["yt_setup_advice"] = channel_advisor.advise_setup(
                    snapshot
                )
        except Exception as e:
            st.error(f"Gagal memeriksa setup: {e}")

    if st.session_state.get("yt_setup_advice"):
        st.markdown(st.session_state["yt_setup_advice"])

    st.divider()
    st.markdown(
        "**Saran jadwal upload** — jam, jarak antar upload, dan berapa video per hari."
    )

    schedule = upload_schedule.analyze(videos)
    if schedule.get("close_avg_views") is not None:
        cols = st.columns(2)
        cols[0].metric(
            f"Berdempet (<{upload_schedule.CLOSE_UPLOAD_MINUTES} menit)",
            f"{schedule['close_avg_views']} view",
            help=f"{len(schedule['close'])} video, rata-rata view per video",
        )
        cols[1].metric(
            "Berjarak",
            f"{schedule['spaced_avg_views']} view",
            help=f"{len(schedule['spaced'])} video, rata-rata view per video",
        )
    st.caption(
        f"Waktu dihitung di UTC+{schedule.get('offset_hours')} (zona penonton). "
        "Ini riwayat uploadmu, BUKAN data kapan penonton online — YouTube tidak "
        "menyediakan itu lewat API, hanya di Studio."
    )

    if st.button("🕒 Minta saran jadwal", key="advise_schedule"):
        try:
            with st.spinner("Gemini membaca riwayat uploadmu..."):
                st.session_state["yt_schedule_advice"] = upload_schedule.advise(videos)
        except Exception as e:
            st.error(f"Gagal meminta saran jadwal: {e}")

    if st.session_state.get("yt_schedule_advice"):
        st.markdown(st.session_state["yt_schedule_advice"])


def render_scout_page() -> None:
    """Cari episode podcast yang layak diklip, dari channel yang dipantau."""
    st.title("🎯 Cari Bahan Clip")
    st.caption("Episode podcast terbaru yang sedang naik, disaring ke selera audiensmu")

    if not youtube_analytics.is_connected():
        st.info(
            "Halaman ini memakai koneksi YouTube yang sama. Hubungkan dulu di "
            "**Analitik YouTube**."
        )
        st.stop()

    channels = get_watch_channels()

    with st.expander(f"📺 Channel dipantau ({len(channels)})", expanded=not channels):
        st.caption(
            "Daftar ini dikurasi sendiri, bukan hasil pencarian. Pencarian YouTube "
            "mengembalikan channel kecil yang tidak relevan dan biayanya 50x lipat "
            "kuota — memantau daftar sendiri jauh lebih tajam."
        )
        new_channel = st.text_input(
            "Tambah channel",
            placeholder="@corbuzier  atau  https://www.youtube.com/@corbuzier",
            key="scout_add",
        )
        if st.button("➕ Tambah", key="scout_add_btn") and new_channel.strip():
            try:
                creds = youtube_analytics.load_credentials()
                found = podcast_scout.resolve_channel(creds, new_channel)
                add_watch_channel(
                    found["channel_id"], found["handle"],
                    found["title"], found["subscribers"],
                )
                st.success(
                    f"✅ {found['title']} ({found['subscribers']:,} subscriber)"
                )
                st.rerun()
            except Exception as e:
                st.error(str(e))

        for channel in channels:
            col_name, col_del = st.columns([5, 1])
            col_name.caption(
                f"**{channel['title']}** · {channel['subscribers']:,} subscriber"
            )
            if col_del.button("✕", key=f"del_ch_{channel['channel_id']}"):
                delete_watch_channel(channel["channel_id"])
                st.rerun()

    if not channels:
        st.warning("Belum ada channel dipantau. Tambahkan minimal satu di atas.")
        st.stop()

    col_age, col_dur = st.columns(2)
    max_age = col_age.slider("Umur tayang maksimum (jam)", 12, 168, 72, step=12)
    min_minutes = col_dur.slider("Durasi minimum episode (menit)", 5, 60, 15, step=5)

    wide = st.checkbox(
        "Sertakan pencarian luas ke seluruh YouTube",
        help=(
            "Menemukan channel yang belum kamu pantau. Diperingkat murni oleh "
            "view per jam — jumlah subscriber tidak ikut menentukan."
        ),
    )
    queries: list[str] = []
    if wide:
        raw_queries = st.text_input(
            "Kata kunci pencarian (pisahkan dengan koma)",
            value="podcast, podcast ngobrol, podcast indonesia",
            key="scout_queries",
        )
        queries = [q.strip() for q in raw_queries.split(",") if q.strip()]
        st.caption(
            f"⚠️ {len(queries)} query × ~{podcast_scout.SEARCH_COST_UNITS} unit = "
            f"~{len(queries) * podcast_scout.SEARCH_COST_UNITS} unit kuota, dari "
            f"10.000 per hari. Memantau satu channel cuma ~2 unit — pakai ini "
            f"untuk MENEMUKAN channel, lalu tambahkan yang bagus ke daftar pantauan."
        )
        st.caption(
            "Catatan terukur: pencarian luas TIDAK menemukan episode dari channel "
            "besar yang judulnya tidak memuat kata 'podcast'. Karena itu daftar "
            "pantauan tetap dipindai bersamaan, bukan digantikan."
        )

    if st.button("🔍 Pindai sekarang", type="primary"):
        try:
            creds = youtube_analytics.load_credentials()
            with st.spinner(f"Memindai {len(channels)} channel dipantau..."):
                episodes = podcast_scout.fetch_recent_episodes(
                    creds, channels,
                    max_age_hours=max_age, min_minutes=min_minutes,
                )
            for episode in episodes:
                episode["source"] = "dipantau"

            if queries:
                with st.spinner(f"Mencari {len(queries)} kata kunci di YouTube..."):
                    discovered = podcast_scout.discover_episodes(
                        creds, queries,
                        max_age_hours=max_age, min_minutes=min_minutes,
                    )
                seen = {e["video_id"] for e in episodes}
                for episode in discovered:
                    if episode["video_id"] not in seen:
                        episode["source"] = "temuan"
                        episodes.append(episode)

            # Peringkat akhir MURNI view per jam, tanpa memandang sumber maupun
            # ukuran channel — inilah inti halaman ini.
            episodes.sort(key=lambda e: -e["velocity"])
            st.session_state["scout_episodes"] = episodes
            st.session_state.pop("scout_advice", None)
        except Exception as e:
            st.error(f"Pemindaian gagal: {e}")

    episodes = st.session_state.get("scout_episodes")
    if episodes is None:
        st.info("Klik **Pindai sekarang** untuk mengambil episode terbaru.")
        st.stop()

    if not episodes:
        st.warning(
            "Tidak ada episode yang memenuhi syarat. Longgarkan umur tayang atau "
            "turunkan durasi minimum."
        )
        st.stop()

    st.subheader(f"{len(episodes)} episode ditemukan")

    sort_mode = st.selectbox(
        "Urutkan berdasarkan",
        list(podcast_scout.SORT_MODES.keys()),
        index=0,
        help=(
            "view/jam menjawab 'masih segar atau sudah dipanen clipper lain'. "
            "comment rate menjawab hal yang berbeda: apakah isinya bikin "
            "penonton bergerak — bahan mentah untuk clip yang memancing komentar."
        ),
    )
    episodes = podcast_scout.sort_episodes(episodes, sort_mode)

    st.caption(
        "**view/jam** = seberapa segar bahannya. **comment rate** = seberapa "
        "memancing isinya. Dua sumbu berbeda, sengaja tidak dilebur jadi satu "
        "skor. Episode terbaik unggul di keduanya."
    )

    def _rate(value) -> str:
        """Persentase, atau '—' kalau datanya memang tidak ada."""
        return "—" if value is None else f"{value * 100:.3f}%"

    st.dataframe(
        pd.DataFrame([
            {
                "episode": e["title"][:60],
                "channel": e["channel"],
                "sumber": e.get("source", "dipantau"),
                "view/jam": round(e["velocity"]),
                "views": e["views"],
                "comment rate": (
                    "dimatikan" if e.get("comments_disabled")
                    else _rate(e.get("comment_rate"))
                ),
                "komentar": "—" if e.get("comments") is None else e["comments"],
                "like rate": _rate(e.get("like_rate")),
                "umur (jam)": round(e["hours_since"]),
                "durasi (menit)": round(e["duration_seconds"] / 60),
                "url": e["url"],
            }
            for e in episodes
        ]),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Catatan membaca comment rate: channel besar cenderung lebih rendah "
        "karena audiensnya terdilusi, jadi perbandingan lintas ukuran channel "
        "tidak sepenuhnya adil. \"dimatikan\" berarti kolom komentarnya ditutup "
        "— datanya tidak ada, bukan episodenya sepi."
    )

    # Channel hasil temuan yang velocity-nya bagus layak dipindahkan ke daftar
    # pantauan: sekali dipantau, biayanya turun dari ~100 unit jadi ~2 unit.
    known = {c["channel_id"] for c in channels}
    new_channels: dict[str, dict] = {}
    for episode in episodes:
        channel_id = episode.get("channel_id")
        if (
            episode.get("source") == "temuan"
            and channel_id
            and channel_id not in known
            and channel_id not in new_channels
        ):
            new_channels[channel_id] = episode

    if new_channels:
        with st.expander(f"➕ {len(new_channels)} channel baru dari pencarian"):
            st.caption(
                "Tambahkan yang bagus ke daftar pantauan supaya episode "
                "berikutnya terpantau murah, tanpa perlu pencarian lagi."
            )
            for channel_id, episode in new_channels.items():
                col_name, col_add = st.columns([5, 1])
                col_name.caption(
                    f"**{episode['channel']}** — episode terbaiknya "
                    f"{episode['velocity']:,.0f} view/jam"
                )
                if col_add.button("➕", key=f"add_found_{channel_id}"):
                    try:
                        creds = youtube_analytics.load_credentials()
                        found = podcast_scout.resolve_channel(creds, channel_id)
                        add_watch_channel(
                            found["channel_id"], found["handle"],
                            found["title"], found["subscribers"],
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    st.divider()
    if st.button("🤖 Minta rekomendasi Gemini", key="scout_advise"):
        try:
            with st.spinner("Gemini menimbang episode terhadap performa channelmu..."):
                audience = None
                try:
                    audience = youtube_analytics.fetch_audience(
                        youtube_analytics.load_credentials()
                    )
                except Exception:
                    pass
                st.session_state["scout_advice"] = podcast_scout.recommend(
                    episodes, get_yt_videos(), audience
                )
        except Exception as e:
            st.error(f"Gagal meminta rekomendasi: {e}")

    if st.session_state.get("scout_advice"):
        st.markdown(st.session_state["scout_advice"])
        st.caption(
            "Salin URL episode pilihanmu ke halaman **Analyze** untuk mencari "
            "potongannya."
        )


def _render_meta_connect() -> None:
    """Layar sebelum terhubung ke Meta."""
    st.info("Belum terhubung ke Instagram / Facebook Page.")
    with st.expander("Cara mendapatkan token", expanded=True):
        st.markdown(
            "Akun profesional (**Business** maupun **Creator**) dua-duanya "
            "didukung. Ada dua jalur, dan Clipodify mendeteksi sendiri yang mana:\n\n"
            "**A. Kalau akun IG-mu TIDAK tertaut Facebook Page** (paling umum "
            "untuk Digital Creator):\n"
            "1. Buat app di **developers.facebook.com**, tambahkan produk "
            "**Instagram** → *API setup with Instagram login*.\n"
            "2. Tautkan akun Instagram-mu di sana, lalu klik **Generate token**.\n"
            "3. Salin tokennya, tempel di bawah. Isi **App Secret** saja supaya "
            "ditukar jadi token ~60 hari (App ID tidak perlu untuk jalur ini).\n\n"
            "**B. Kalau akun IG-mu tertaut ke Facebook Page:**\n"
            "1. Buat app di **developers.facebook.com** (tipe Business).\n"
            "2. Buka **Graph API Explorer**, minta permission `pages_show_list`, "
            "`pages_read_engagement`, `instagram_basic`, "
            "`instagram_manage_insights`.\n"
            "3. **Generate Access Token**, salin, tempel di bawah. Isi App ID "
            "dan App Secret untuk token panjang."
        )
        st.caption(
            "Token dari Explorer biasanya cuma berumur 1 jam. Isi App ID dan App "
            "Secret di bawah supaya Clipodify menukarnya jadi token ~60 hari."
        )

    token = st.text_input(
        "Access token",
        type="password",
        key="meta_token",
        help=(
            "Untuk jalur A tempel token Instagram Login (diawali IGAA). Token "
            "Facebook (EAA) hanya dipakai kalau kamu memang menempuh jalur B."
        ),
    )
    col_id, col_secret = st.columns(2)
    app_id = col_id.text_input("App ID (opsional)", key="meta_app_id")
    app_secret = col_secret.text_input(
        "App Secret (opsional)", type="password", key="meta_app_secret"
    )

    if not st.button("🔗 Hubungkan", type="primary"):
        return
    if not token.strip():
        st.error("Token belum diisi.")
        return

    working = token.strip()

    # Jalur Instagram Login dicoba DULUAN. Token jenis itu tidak bisa memanggil
    # me/accounts sama sekali, jadi mencoba jalur Page lebih dulu cuma
    # menghasilkan error yang menyesatkan buat akun Creator tanpa Page.
    with st.spinner("Mengenali jenis token..."):
        ig_account = meta_analytics.probe_instagram_login(working)

    if ig_account:
        try:
            if app_secret.strip():
                with st.spinner("Menukar jadi token panjang..."):
                    exchanged = meta_analytics.exchange_long_lived_ig(
                        working, app_secret.strip()
                    )
                if exchanged.get("token"):
                    working = exchanged["token"]
                    days = (exchanged.get("expires_in") or 0) / 86400
                    st.success(f"Token panjang didapat (berlaku ~{days:.0f} hari).")
            else:
                st.warning(
                    "App Secret kosong, jadi token dipakai apa adanya — biasanya "
                    "cuma berumur 1 jam."
                )
        except Exception as e:
            st.warning(f"Penukaran token dilewati: {e}")

        save_meta_auth(
            working, None, None,
            ig_account["ig_id"], ig_account.get("ig_username"),
            kind=meta_analytics.KIND_IG_LOGIN,
        )
        st.success(
            f"✅ Terhubung ke Instagram @{ig_account.get('ig_username')} "
            f"({ig_account.get('account_type')}) — tanpa Facebook Page."
        )
        st.rerun()
        return

    try:
        if app_id.strip() and app_secret.strip():
            with st.spinner("Menukar jadi token panjang..."):
                exchanged = meta_analytics.exchange_long_lived(
                    working, app_id.strip(), app_secret.strip()
                )
            if exchanged.get("token"):
                working = exchanged["token"]
                days = (exchanged.get("expires_in") or 0) / 86400
                st.success(f"Token panjang didapat (berlaku ~{days:.0f} hari).")

        with st.spinner("Membaca Page yang kamu kelola..."):
            pages = meta_analytics.list_pages(working)
    except Exception as e:
        st.error(str(e))
        return

    if not pages:
        # Diagnosis dari awalan token. Tanpa ini, orang yang menempel token
        # Facebook ke kolom Instagram cuma diberi tahu "tidak ada Page" —
        # benar secara teknis, tapi sama sekali tidak menunjuk kesalahannya.
        if working.startswith("EAA"):
            st.error(
                "Ini token **Facebook**, dan kolom ini menunggu token "
                "**Instagram Login** (diawali `IGAA`).\n\n"
                "Token Facebook tidak ditempel di sini. Sambungkan Instagram "
                "dulu lewat jalur A, lalu tempel token Facebook di expander "
                "**📘 Tambah Facebook Page** yang muncul setelahnya.\n\n"
                "Kalau kamu memang mau memakai jalur B (lewat Page), token ini "
                "tidak mengelola Page mana pun — cek permission "
                "`pages_show_list` saat men-generate-nya."
            )
        elif working.startswith("IGAA"):
            st.error(
                "Token Instagram Login ini ditolak Meta — kemungkinan besar "
                "sudah kedaluwarsa. Generate ulang di dashboard app: "
                "**Instagram → API setup with Instagram login → Generate token**."
            )
        else:
            st.error(
                "Token ini bukan token Instagram Login, dan juga tidak mengelola "
                "Facebook Page mana pun. Untuk akun Creator tanpa Page, pakai "
                "jalur A di atas."
            )
        return

    st.session_state["meta_pages"] = pages
    st.session_state["meta_working_token"] = working
    st.rerun()


def _render_meta_page_picker() -> None:
    pages = st.session_state["meta_pages"]
    st.success(f"{len(pages)} Page ditemukan. Pilih yang mau dianalisis.")

    labels = {
        f"{p['page_name']}" + (f" · IG @{p['ig_username']}" if p.get("ig_username")
                               else " · (tanpa Instagram tertaut)"): p
        for p in pages
    }
    choice = st.selectbox("Page", list(labels.keys()), key="meta_page_choice")
    page = labels[choice]

    if not page.get("ig_id"):
        st.warning(
            "Page ini belum menautkan akun Instagram Business. Data yang bisa "
            "diambil hanya video Facebook-nya."
        )

    if st.button("💾 Simpan koneksi", type="primary"):
        save_meta_auth(
            st.session_state["meta_working_token"],
            page["page_id"], page["page_name"],
            page.get("ig_id"), page.get("ig_username"),
            kind=meta_analytics.KIND_PAGE,
        )
        st.session_state.pop("meta_pages", None)
        st.session_state.pop("meta_working_token", None)
        st.rerun()


def _render_facebook_addon(auth: dict) -> None:
    """Tambahkan Facebook Page di atas koneksi Instagram Login.

    Dua koneksi terpisah karena tokennya memang beda sistem: token Instagram
    Login (diawali IGAA) bicara ke graph.instagram.com dan tidak bisa menyentuh
    endpoint Page sama sekali.
    """
    if auth.get("fb_page_id"):
        col_name, col_del = st.columns([5, 1])
        col_name.caption(
            f"📘 Facebook Page tersambung: **{auth.get('fb_page_name')}**"
        )
        if col_del.button("✕", key="fb_disconnect"):
            set_meta_facebook(None, None, None)
            st.session_state.pop("meta_snapshot", None)
            st.rerun()
        return

    with st.expander("📘 Tambah Facebook Page (opsional)"):
        st.caption(
            "Koneksi Instagram-mu memakai token Instagram Login, yang tidak bisa "
            "membaca Facebook Page. Page butuh token **pengguna** terpisah dari "
            "Graph API Explorer dengan permission `pages_show_list`, "
            "`pages_read_engagement`, dan `read_insights` — yang terakhir wajib "
            "untuk metrik video, dan paling sering terlewat.\n\n"
            "Semua izin ini masuk **Standard Access**, yang otomatis disetujui "
            "untuk app-mu sendiri. Verifikasi bisnis TIDAK diperlukan — itu "
            "hanya dituntut kalau app meminta izin dari sembarang pengguna."
        )
        fb_token = st.text_input(
            "Token Facebook", type="password", key="fb_token_input"
        )

        if st.button("Cari Page", key="fb_find"):
            if not fb_token.strip():
                st.error("Token belum diisi.")
            else:
                try:
                    with st.spinner("Membaca Page yang kamu kelola..."):
                        pages = meta_analytics.list_pages(fb_token.strip())
                    if not pages:
                        st.error(
                            "Token ini tidak mengelola Facebook Page mana pun.\n\n"
                            "Dua kemungkinan:\n"
                            "1. Saat men-generate token, dialog **'Pilih Page yang "
                            "boleh diakses'** dilewati tanpa mencentang Page apa "
                            "pun — tokennya sah tapi buta terhadap semua Page. "
                            "Generate ulang dan pastikan Page-mu tercentang.\n"
                            "2. Akun Facebook-mu memang belum punya Page. Kalau "
                            "begitu, memang tidak ada data Page untuk diambil — "
                            "Instagram saja sudah cukup."
                        )
                    else:
                        st.session_state["fb_pages"] = pages
                        st.session_state["fb_working_token"] = fb_token.strip()
                except Exception as e:
                    st.error(str(e))

        if st.button("🔎 Diagnosa token ini", key="fb_diag"):
            if not fb_token.strip():
                st.error("Token belum diisi.")
            else:
                with st.spinner("Menanyakan langsung ke Meta..."):
                    report = meta_analytics.diagnose_token(fb_token.strip())

                identity = report.get("identity") or {}
                st.markdown(
                    f"**Akun token ini:** {identity.get('name') or '—'} "
                    f"(`{identity.get('id') or '—'}`)"
                )

                needed = ["pages_show_list", "pages_read_engagement", "read_insights"]
                granted = report.get("granted") or []
                st.markdown("**Izin yang benar-benar diberikan:**")
                for permission in needed:
                    mark = "✅" if permission in granted else "❌"
                    st.markdown(f"{mark} `{permission}`")
                extra = [p for p in granted if p not in needed]
                if extra:
                    st.caption("Izin lain: " + ", ".join(f"`{p}`" for p in extra))
                if report.get("declined"):
                    st.caption(
                        "Ditolak saat consent: "
                        + ", ".join(f"`{p}`" for p in report["declined"])
                    )

                pages = report.get("pages") or []
                st.markdown(f"**Page yang terlihat token ini:** {len(pages)}")
                for item in pages:
                    st.caption(f"- {item.get('name')} (`{item.get('id')}`)")

                if report.get("errors"):
                    for where, message in report["errors"].items():
                        st.warning(f"{where}: {message}")

                # Kesimpulan hanya boleh ditarik kalau SEMUA probe berhasil.
                # Kalau ada yang gagal, daftar izin ikut kosong — dan menyimpulkan
                # "izin tidak diberikan" dari situ adalah tuduhan palsu yang
                # menyuruh user memperbaiki hal yang tidak rusak.
                if report.get("errors"):
                    st.error(
                        "**Belum bisa disimpulkan.** Sebagian pertanyaan ke Meta "
                        "gagal (lihat peringatan di atas), jadi daftar izin dan "
                        "daftar Page di sini tidak mencerminkan keadaan sebenarnya. "
                        "Kalau penyebabnya kuota, tunggu beberapa jam lalu ulangi "
                        "diagnosa ini — jangan men-generate ulang token dulu."
                    )
                elif "pages_show_list" not in granted:
                    st.error(
                        "**Penyebabnya ketemu:** izin `pages_show_list` tidak "
                        "diberikan. Generate ulang token dan centang izin itu."
                    )
                elif not pages:
                    st.error(
                        "**Izinnya lengkap, tapi cakupannya nol Page.**\n\n"
                        "Kalau kamu yakin punya Page, penyebab tersering adalah "
                        "Facebook MENGINGAT pilihan Page dari consent sebelumnya. "
                        "Sekali kamu generate token tanpa mencentang Page, dialog "
                        "pemilihan itu tidak muncul lagi — izin tercatat diberikan, "
                        "tapi tidak menjangkau Page mana pun.\n\n"
                        "Paksa dialognya muncul lagi: buka **facebook.com/settings"
                        "?tab=applications**, hapus app ini dari daftar, lalu "
                        "generate token baru. Kali ini pilih **Opt in to all "
                        "Pages**.\n\n"
                        "Kalau Page-mu dimiliki sebuah Business Portfolio, "
                        "tambahkan juga izin `business_management` — di diagnosa "
                        "ini statusnya tercatat DITOLAK."
                    )
                else:
                    st.success(
                        "Token ini sehat. Klik **Cari Page** di atas untuk memilih."
                    )

        pages = st.session_state.get("fb_pages")
        if pages:
            labels = {p["page_name"]: p for p in pages}
            choice = st.selectbox("Page", list(labels.keys()), key="fb_page_choice")
            if st.button("💾 Simpan Page", key="fb_save", type="primary"):
                picked = labels[choice]
                # Token PAGE yang disimpan, bukan token user: metrik video Page
                # hanya terbuka lewat token milik Page itu sendiri.
                set_meta_facebook(
                    picked.get("page_token") or st.session_state["fb_working_token"],
                    picked["page_id"],
                    picked["page_name"],
                )
                st.session_state.pop("fb_pages", None)
                st.session_state.pop("fb_working_token", None)
                st.session_state.pop("meta_snapshot", None)
                st.rerun()


def render_meta_page() -> None:
    """Analitik Instagram Reels dan Facebook Page."""
    st.title("📱 Analitik Instagram & Facebook")
    st.caption("Performa reel dan video Page — read-only, tidak memposting apa pun")

    if st.session_state.get("meta_pages"):
        _render_meta_page_picker()
        st.stop()

    auth = get_meta_auth()
    if not auth:
        _render_meta_connect()
        st.stop()

    col_info, col_action = st.columns([3, 1])
    col_info.subheader(
        auth.get("page_name") or f"Instagram @{auth.get('ig_username')}"
    )
    col_info.caption(
        (f"Instagram @{auth['ig_username']} · " if auth.get("ig_username") else "")
        + f"terhubung {auth['connected_at']}"
    )
    if col_action.button("Putuskan koneksi"):
        delete_meta_auth()
        st.rerun()

    st.warning(
        "Meta tidak menyediakan kurva retensi per detik seperti YouTube — hanya "
        "angka agregat. Pertanyaan \"penonton pergi di detik ke berapa\" hanya "
        "bisa dijawab di halaman Analitik YouTube."
    )

    if auth.get("kind") == meta_analytics.KIND_IG_LOGIN:
        _render_facebook_addon(auth)

    limit = st.slider("Jumlah media terbaru", 10, 100, 30, step=10, key="meta_limit")
    if st.button("🔄 Tarik data", type="primary"):
        try:
            page = {
                "page_id": auth.get("page_id"), "page_name": auth.get("page_name"),
                "ig_id": auth.get("ig_id"), "ig_username": auth.get("ig_username"),
                "page_token": None,
                "kind": auth.get("kind") or meta_analytics.KIND_PAGE,
                "fb_token": auth.get("fb_token"),
                "fb_page_id": auth.get("fb_page_id"),
            }
            with st.spinner("Mengambil data dari Meta..."):
                st.session_state["meta_snapshot"] = meta_analytics.fetch_snapshot(
                    auth["access_token"], page, limit=limit
                )
        except Exception as e:
            st.error(str(e))

    snapshot = st.session_state.get("meta_snapshot")
    if not snapshot:
        st.info("Klik **Tarik data** untuk mengambil performa terbaru.")
        st.stop()

    reels = snapshot.get("instagram") or []
    if reels:
        # Kalau metrik kosong, sebutkan alasannya. Kolom None tanpa penjelasan
        # membuat user mengira datanya memang tidak ada.
        failed = [r for r in reels if r.get("insight_error")]
        if failed:
            st.warning(
                f"{len(failed)} dari {len(reels)} media gagal diambil metriknya — "
                f"kolom angkanya akan kosong. Alasan pertama: "
                f"{failed[0]['insight_error']}"
            )

        st.subheader(f"Instagram — {len(reels)} media")
        st.dataframe(
            pd.DataFrame([
                {
                    "caption": (r.get("caption") or "")[:60],
                    "tayang": (r.get("timestamp") or "")[:10],
                    "views": r.get("views"),
                    "reach": r.get("reach"),
                    "like": r.get("likes"),
                    "komentar": r.get("comments"),
                    "share": r.get("shares"),
                    "saved": r.get("saved"),
                    "link": r.get("permalink"),
                }
                for r in reels
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("Tidak ada media Instagram yang terbaca.")

    if snapshot.get("facebook_error"):
        st.warning(f"Video Facebook Page gagal diambil: {snapshot['facebook_error']}")

    videos = snapshot.get("facebook") or []
    if videos:
        st.subheader(f"Facebook Page — {len(videos)} video")
        st.dataframe(
            pd.DataFrame([
                {
                    "judul": (v.get("title") or "")[:60],
                    "tayang": (v.get("created_time") or "")[:10],
                    "durasi (detik)": (
                        round(v["length_seconds"]) if v.get("length_seconds") else None
                    ),
                    "views": v.get("views"),
                    "impresi": v.get("impressions"),
                    "rata-rata tonton (detik)": (
                        round(v["avg_watch_seconds"], 1)
                        if v.get("avg_watch_seconds") else None
                    ),
                }
                for v in videos
            ]),
            width="stretch",
            hide_index=True,
        )

    audience = snapshot.get("ig_audience") or {}
    if audience:
        with st.expander("👥 Demografi pengikut Instagram"):
            st.dataframe(
                pd.DataFrame(
                    sorted(audience.items(), key=lambda kv: -(kv[1] or 0)),
                    columns=["kelompok", "pengikut"],
                ),
                width="stretch",
                hide_index=True,
            )


NAV_ICONS = {
    "Analyze": "🎬",
    "Cari Bahan Clip": "🔎",
    "Unduh Manual": "⬇️",
    "Analitik YouTube": "📊",
    "Analitik IG & FB": "📱",
    "History": "🗂️",
    "Settings": "⚙️",
}

st.sidebar.title("🎬 Clipodify")
st.sidebar.caption("Pencari clip untuk podcast & interview")
st.sidebar.markdown("")

# Ikon ditempel lewat format_func, bukan dimasukkan ke nilainya: nilai radio
# tetap string polos, sehingga percabangan halaman dan navigasi tertunda di
# `_apply_pending_navigation` tidak perlu tahu soal ikon sama sekali.
page = st.sidebar.radio(
    "Menu",
    list(NAV_ICONS.keys()),
    format_func=lambda name: f"{NAV_ICONS[name]}  {name}",
    key="nav_page",
)

with st.sidebar:
    st.markdown("")
    st.divider()
    st.caption(f"Model: `{GEMINI_MODEL}`")
    st.caption(f"Output: `{OUTPUT_DIR}`")

if page == "Analyze":
    render_analyze_page()
elif page == "Cari Bahan Clip":
    render_scout_page()
elif page == "Unduh Manual":
    render_download_page()
elif page == "Analitik YouTube":
    render_youtube_page()
elif page == "Analitik IG & FB":
    render_meta_page()
elif page == "History":
    render_history_page()
elif page == "Settings":
    render_settings_page()
