"""Pydantic models untuk request/response FastAPI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- shared

class PartSchema(BaseModel):
    start: str
    end: str
    start_seconds: float
    duration_seconds: float


# ---------------------------------------------------------------- jobs

class ProcessRequest(BaseModel):
    src: str
    min_seconds: int = 30
    max_seconds: int = 90
    language: str | None = "id"
    mode: str = "regular"
    campaign_product: str = ""
    campaign_benefit: str = ""
    campaign_cta: str = ""
    campaign_hashtags: list[str] = Field(default_factory=list)
    use_performance_hint: bool = False


class JobResponse(BaseModel):
    id: int
    url: str
    video_id: str
    title: str
    status: str
    source_type: str | None
    mode: str | None
    has_comments: bool | None
    campaign_product: str | None
    created_at: str | None
    thumbnail: str | None
    duration: float | None
    candidates_count: int = 0


class MetadataResponse(BaseModel):
    video_id: str
    title: str
    duration: float
    thumbnail: str | None
    resolution: str | None
    uploader: str | None
    source_type: str


# ---------------------------------------------------------------- candidates

class CandidateResponse(BaseModel):
    id: int | None
    start: str
    end: str
    start_seconds: float
    duration_seconds: float
    final_score: float
    clip_type: str | None
    hook: str | None
    reason: str | None
    title: str | None
    description: str | None
    shareability: float | None
    discussion_trigger: float | None
    standalone_context: float | None
    hook_strength: float | None
    payoff_strength: float | None
    hook_start: str | None
    hook_end: str | None
    hook_placement: str | None
    campaign_fit: float | None
    product_moment: str | None
    thumbnail_prompt: str | None
    thumbnail_text: str | None
    segment_scores: dict[str, Any] | None
    signal_notes: dict[str, str] | None
    status: str | None
    output_path: str | None
    parts: list[PartSchema] | None


class UpdateCandidateStatusRequest(BaseModel):
    status: str


class DownloadClipRequest(BaseModel):
    job_id: int
    candidate_id: int


# ---------------------------------------------------------------- download

class ManualClipRequest(BaseModel):
    src: str
    start: str
    end: str
    title: str


class FullDownloadRequest(BaseModel):
    src: str
    title: str


# ---------------------------------------------------------------- settings

class AddApiKeyRequest(BaseModel):
    name: str
    key_value: str
    make_active: bool = True


class ApiKeyResponse(BaseModel):
    id: int
    name: str
    key_masked: str
    is_active: bool


class TmpUsageResponse(BaseModel):
    count: int
    total_bytes: int
    total_mb: float


# ---------------------------------------------------------------- youtube

class RefreshVideosRequest(BaseModel):
    limit: int = 50


class FetchRetentionRequest(BaseModel):
    video_id: str


class AdviceRequest(BaseModel):
    kind: str = "clip"


# ---------------------------------------------------------------- scout

class AddChannelRequest(BaseModel):
    handle: str


class ScanRequest(BaseModel):
    max_age_hours: int = 72
    min_minutes: int = 15
    wide: bool = False
    queries: list[str] = Field(default_factory=list)
    sort_mode: str = "view/jam"


class RecommendRequest(BaseModel):
    episodes: list[dict[str, Any]]


# ---------------------------------------------------------------- meta

class MetaConnectRequest(BaseModel):
    token: str
    app_id: str = ""
    app_secret: str = ""


class MetaPageSelectRequest(BaseModel):
    page_id: str
    page_name: str
    ig_id: str | None = None
    ig_username: str | None = None
    page_token: str | None = None
    working_token: str


class MetaFetchRequest(BaseModel):
    limit: int = 30


class FacebookAddonRequest(BaseModel):
    token: str


class DiagnoseTokenRequest(BaseModel):
    token: str


# ---------------------------------------------------------------- editor

class SfxItem(BaseModel):
    sfx_id: str
    timestamp: float
    volume: float = 1.0


class ExpressionClip(BaseModel):
    start: float          # detik dari awal clip
    end: float
    type: str             # "angry" | "crying" | "shocked" | "laughing" | "dreamy" | "dramatic"


class TextOverlay(BaseModel):
    kind: str = "custom"
    text: str
    start: float
    duration: float = 3.0
    position: str = "top"
    color: str = "#FFFFFF"
    font: str = "Anton"


class GradeRecipe(BaseModel):
    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 0.0
    gamma: float = 1.0
    warmth: float = 0.0
    tint: float = 0.0


class EditorConfig(BaseModel):
    subtitle_style_id: str | None = None      # gaya dari Studio Subtitle; None = tanpa subtitle
    grade: GradeRecipe = Field(default_factory=GradeRecipe)
    sfx: list[SfxItem] = Field(default_factory=list)
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    expressions: list[ExpressionClip] = Field(default_factory=list)


class RenderRequest(BaseModel):
    job_id: int
    candidate_id: int
    config: EditorConfig


# ---------------------------------------------------------------- websocket

class ProgressEvent(BaseModel):
    step: int
    total: int
    label: str
    detail: str = ""
    done: bool = False
    error: str = ""
