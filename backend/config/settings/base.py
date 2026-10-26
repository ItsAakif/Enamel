"""Base Django settings shared by every environment.

Configuration is environment-driven. PostgreSQL is the only supported database
(there is deliberately no SQLite fallback); Redis is required for readiness.
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from config.logging import build_logging_config

# backend/ (manage.py lives here)
BASE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BASE_DIR.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


# --- Core ------------------------------------------------------------------

DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    # Development-only placeholder. config.settings.prod refuses to start
    # without an explicit SECRET_KEY.
    "django-insecure-enamel-dev-only-never-use-in-production",
)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")

# --- Database and cache ----------------------------------------------------


def build_databases(database_url: str) -> dict[str, dict]:
    """Parse and validate ``DATABASE_URL``; PostgreSQL is the only backend."""

    parsed = dj_database_url.parse(
        database_url,
        conn_max_age=600,
        conn_health_checks=True,
    )
    if parsed.get("ENGINE") != "django.db.backends.postgresql":
        raise ImproperlyConfigured(
            "DATABASE_URL must use PostgreSQL; SQLite and other engines are not supported."
        )
    parsed.setdefault("OPTIONS", {})["connect_timeout"] = 5
    # Managed Postgres poolers (Supabase Supavisor, Neon -pooler, PgBouncer in
    # transaction mode) do not support server-side cursors. Set
    # DB_DISABLE_SERVER_SIDE_CURSORS=1 when the deployment uses a pooler.
    if os.environ.get("DB_DISABLE_SERVER_SIDE_CURSORS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        parsed["DISABLE_SERVER_SIDE_CURSORS"] = True
    if os.environ.get("DB_CONN_MAX_AGE", "").strip():
        parsed["CONN_MAX_AGE"] = int(os.environ["DB_CONN_MAX_AGE"])
    return {"default": parsed}


DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ImproperlyConfigured(
        "DATABASE_URL is required (PostgreSQL only; SQLite is not supported)."
    )

DATABASES = build_databases(DATABASE_URL)

REDIS_URL = os.environ.get("REDIS_URL")
if not REDIS_URL:
    raise ImproperlyConfigured("REDIS_URL is required (Redis is used for the broker).")

# --- Applications ----------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "corsheaders",
    "rest_framework",
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.clinics.apps.ClinicsConfig",
    "apps.patients.apps.PatientsConfig",
    "apps.charting.apps.ChartingConfig",
    "apps.scheduling.apps.SchedulingConfig",
    "apps.messaging.apps.MessagingConfig",
    "apps.booking.apps.BookingConfig",
    "apps.plans.apps.PlansConfig",
    "apps.billing.apps.BillingConfig",
    "apps.ai.apps.AiConfig",
    "apps.ml.apps.MlConfig",
    "apps.imaging.apps.ImagingConfig",
    "apps.analytics.apps.AnalyticsConfig",
    "apps.interop.apps.InteropConfig",
    "apps.marketing.apps.MarketingConfig",
    "apps.rcm.apps.RcmConfig",
    "apps.portal.apps.PortalConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # RequestIdMiddleware must precede CorsMiddleware: CORS preflight requests
    # are answered inside CorsMiddleware and never reach downstream middleware.
    "apps.core.middleware.RequestIdMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.SessionIdleMiddleware",
    "apps.core.middleware.ClinicScopeMiddleware",
    "apps.core.portal_context.PortalContextMiddleware",
    "apps.core.middleware.UnknownQueryParamsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Authentication --------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Session cookie contract (ADR-007). The absolute age is enforced by Django.
# Idle expiry (30 minutes) is implemented in T101 via session rotation/tracking.
SESSION_COOKIE_NAME = "enamel_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = False  # enabled in prod
SESSION_COOKIE_AGE = 43200  # 12 hours absolute
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_IDLE_TIMEOUT = 1800  # 30 minutes, enforced by SessionIdleMiddleware
SESSION_ABSOLUTE_TIMEOUT = 43200  # 12 hours, enforced by SessionIdleMiddleware

# Optional TOTP MFA (pyotp). Secrets are Fernet-encrypted at rest.
MFA_ISSUER = os.environ.get("MFA_ISSUER", "Enamel")
MFA_TOTP_VALID_WINDOW = 1

# Token lifetimes and links.
PASSWORD_RESET_TIMEOUT_MINUTES = 30
INVITATION_TTL_DAYS = 7
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173")

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@enamel.local")

# Django's default cookie name, as documented in ARCHITECTURE.md section 6.
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_HTTPONLY = False  # the SPA reads it to send X-CSRFToken
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = False  # enabled in prod
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
)

# --- CORS ------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
CORS_ALLOW_CREDENTIALS = True

# --- API -------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "apps.core.schema.EnamelAutoSchema",
    "EXCEPTION_HANDLER": "apps.core.errors.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "auth": "5/min",
        "public_booking": "10/min",
        "public_booking_phone": "3/min",
        "ai": "30/min",
        "export": "5/hour",
    },
}

# --- OpenAPI ---------------------------------------------------------------
# drf-spectacular is the source of truth for the generated frontend client.
# OAS 3.1 is required by the contract. The error envelope is published by
# ``apps.core.schema``: every operation documents the standard error statuses
# and they all reference the ``ErrorEnvelope`` component, whose ``CodeEnum``
# is derived from ``apps.core.errors.ERROR_CODES``.

SPECTACULAR_SETTINGS = {
    "TITLE": "Enamel API",
    "DESCRIPTION": "Enamel dental platform API (version 1).",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Deterministic component names for shared choice sets. Without this,
    # drf-spectacular invents hash-suffixed names (StatusE75Enum) that make
    # generated client diffs noisy.
    "ENUM_NAME_OVERRIDES": {
        "ClinicStatusEnum": "apps.clinics.models.CLINIC_STATUS_CHOICES",
        "MembershipRoleEnum": "apps.clinics.models.MEMBERSHIP_ROLE_CHOICES",
        "MembershipStatusEnum": "apps.clinics.models.MEMBERSHIP_STATUS_CHOICES",
        "MembershipUpdateStatusEnum": (
            "apps.clinics.serializers.MEMBERSHIP_UPDATE_STATUS_CHOICES"
        ),
        "PatientStatusEnum": "apps.patients.models.PATIENT_STATUS_CHOICES",
        "PatientSourceEnum": "apps.patients.models.PATIENT_SOURCE_CHOICES",
        "PatientSexAtBirthEnum": "apps.patients.models.PATIENT_SEX_AT_BIRTH_CHOICES",
        "PatientPreferredContactEnum": (
            "apps.patients.models.PATIENT_PREFERRED_CONTACT_CHOICES"
        ),
        "AllergySeverityEnum": "apps.patients.models.ALLERGY_SEVERITY_CHOICES",
        "MedicalHistoryKindEnum": "apps.patients.models.MEDICAL_HISTORY_KIND_CHOICES",
        "EncounterKindEnum": "apps.patients.models.ENCOUNTER_KIND_CHOICES",
        "EncounterStatusEnum": "apps.patients.models.ENCOUNTER_STATUS_CHOICES",
        "AppointmentStatusEnum": "apps.scheduling.models.APPOINTMENT_STATUS_CHOICES",
        "AppointmentSourceEnum": "apps.scheduling.models.APPOINTMENT_SOURCE_CHOICES",
        "AppointmentEventTypeEnum": "apps.scheduling.models.APPOINTMENT_EVENT_TYPE_CHOICES",
        "AvailabilityRuleKindEnum": "apps.scheduling.models.AVAILABILITY_RULE_KIND_CHOICES",
        "ScheduleExceptionKindEnum": "apps.scheduling.models.SCHEDULE_EXCEPTION_KIND_CHOICES",
        "ReservationHoldStatusEnum": (
            "apps.booking.models.RESERVATION_HOLD_STATUS_CHOICES"
        ),
        "BookingReferenceSourceEnum": (
            "apps.booking.models.BOOKING_REFERENCE_SOURCE_CHOICES"
        ),
        "BookingConsentPurposeEnum": (
            "apps.booking.models.BOOKING_CONSENT_PURPOSE_CHOICES"
        ),
        "SignedLinkPurposeEnum": "apps.booking.models.SIGNED_LINK_PURPOSE_CHOICES",
        "MessagingChannelEnum": "apps.messaging.models.MESSAGING_CHANNEL_CHOICES",
        "MessagingPurposeEnum": "apps.messaging.models.MESSAGING_PURPOSE_CHOICES",
        "ConsentStatusEnum": "apps.messaging.models.CONSENT_STATUS_CHOICES",
        "SuppressionReasonEnum": "apps.messaging.models.SUPPRESSION_REASON_CHOICES",
        "TemplateApprovalStatusEnum": (
            "apps.messaging.models.TEMPLATE_APPROVAL_STATUS_CHOICES"
        ),
        "MessageStatusEnum": "apps.messaging.models.MESSAGE_STATUS_CHOICES",
        "RecallStatusEnum": "apps.messaging.models.RECALL_STATUS_CHOICES",
        "WaitlistStatusEnum": "apps.scheduling.models.WAITLIST_STATUS_CHOICES",
        "WaitlistOfferStatusEnum": "apps.scheduling.models.WAITLIST_OFFER_STATUS_CHOICES",
        "TreatmentPlanStatusEnum": "apps.plans.models.TREATMENT_PLAN_STATUS_CHOICES",
        "PlanItemStatusEnum": "apps.plans.models.PLAN_ITEM_STATUS_CHOICES",
        "ConsentSignatureTypeEnum": "apps.plans.models.CONSENT_SIGNATURE_TYPE_CHOICES",
        "ConsentRecordStatusEnum": "apps.plans.models.CONSENT_RECORD_STATUS_CHOICES",
        "InvoiceStatusEnum": "apps.billing.models.INVOICE_STATUS_CHOICES",
        "PaymentMethodEnum": "apps.billing.models.PAYMENT_METHOD_CHOICES",
        "PaymentStatusEnum": "apps.billing.models.PAYMENT_STATUS_CHOICES",
        "RefundStatusEnum": "apps.billing.models.REFUND_STATUS_CHOICES",
        "WebhookEventStatusEnum": "apps.billing.models.WEBHOOK_EVENT_STATUS_CHOICES",
        "ReconciliationStatusEnum": "apps.billing.models.RECONCILIATION_STATUS_CHOICES",
        "NoshowModelStatusEnum": "apps.ml.models.NOSHOW_MODEL_STATUS_CHOICES",
        "PredictionDecisionEnum": "apps.ml.models.PREDICTION_DECISION_CHOICES",
        "StudyStatusEnum": "apps.imaging.models.STUDY_STATUS_CHOICES",
        "StudySourceEnum": "apps.imaging.models.STUDY_SOURCE_CHOICES",
        "InstancePreviewStatusEnum": (
            "apps.imaging.models.INSTANCE_PREVIEW_STATUS_CHOICES"
        ),
        "InferenceJobStatusEnum": "apps.imaging.models.INFERENCE_JOB_STATUS_CHOICES",
        "ExportReportEnum": "apps.analytics.models.EXPORT_REPORT_CHOICES",
        "ExportFormatEnum": "apps.analytics.models.EXPORT_FORMAT_CHOICES",
        "ExportStatusEnum": "apps.analytics.models.EXPORT_STATUS_CHOICES",
        "ConnectorKindEnum": "apps.interop.models.CONNECTOR_KIND_CHOICES",
        "ConnectorModeEnum": "apps.interop.models.CONNECTOR_MODE_CHOICES",
        "EntityTypeEnum": "apps.interop.models.ENTITY_TYPE_CHOICES",
        "SyncStatusEnum": "apps.interop.models.SYNC_STATUS_CHOICES",
        "PortalChannelEnum": "apps.portal.models.PORTAL_CHANNEL_CHOICES",
        "CampaignStatusEnum": "apps.marketing.models.CAMPAIGN_STATUS_CHOICES",
        "CampaignRecipientStatusEnum": (
            "apps.marketing.models.CAMPAIGN_RECIPIENT_STATUS_CHOICES"
        ),
        "EligibilityStatusEnum": "apps.rcm.models.ELIGIBILITY_STATUS_CHOICES",
        "ClaimStatusEnum": "apps.rcm.models.CLAIM_STATUS_CHOICES",
        "ClaimEventTypeEnum": "apps.rcm.models.CLAIM_EVENT_TYPE_CHOICES",
        "RemittanceStatusEnum": "apps.rcm.models.REMITTANCE_STATUS_CHOICES",
    },
    "APPEND_COMPONENTS": {},
    # Publishes the error-envelope components (``CodeEnum``, ``ErrorDetail``,
    # ``ErrorEnvelope``) and keeps them referenced by every operation. The
    # code list is derived from ``apps.core.errors.ERROR_CODES`` (the single
    # source of truth) instead of being duplicated in the settings.
    #
    # The default enum hook is kept first: it turns the inline choice sets
    # into the named ``*Enum`` components, and it rebuilds ``components`` at
    # the end, so the error components have to be added after it.
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.core.schema.postprocess_error_contract",
    ],
}

# --- AI provider -----------------------------------------------------------
# Offline-safe defaults: the deterministic mock provider is selected until an
# operator explicitly configures a real provider. Model ids are configuration,
# never hard-coded in domain code. No real credentials belong in this file.

AI_PROVIDER = os.environ.get("AI_PROVIDER", "mock")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
AI_CHAT_MODEL = os.environ.get("AI_CHAT_MODEL", "")
# T123: the model id used for chat/completions. ``AI_MODEL`` is the canonical
# name; ``AI_CHAT_MODEL`` is kept for backwards compatibility.
AI_MODEL = os.environ.get("AI_MODEL", os.environ.get("AI_CHAT_MODEL", ""))
# The recommended fallback model (set from the measured free-model benchmark;
# see apps/ai/evals/configs/free-models.json). ``AI_FALLBACK_MODELS`` remains
# available for a longer explicit chain and is appended after this one.
AI_FALLBACK_MODEL = os.environ.get("AI_FALLBACK_MODEL", "")
AI_FALLBACK_MODELS = env_list("AI_FALLBACK_MODELS", "")
# Operator opt-in to paid models. The default (False) arms the free-model
# guard: configured model ids are resolved in the live OpenRouter /models
# catalogue and any model with paid pricing is refused before the first call.
AI_ALLOW_PAID = env_bool("AI_ALLOW_PAID", False)
# Hard cap on total HTTP requests per benchmark run (safety ceiling for the
# live free-model benchmark; the run stops when the cap is reached).
AI_BENCH_MAX_REQUESTS = int(os.environ.get("AI_BENCH_MAX_REQUESTS", "150"))
# Reserved for the retrieval work (the reranker model id). No code path uses
# it yet; it is declared here so the operator contract is fixed early.
AI_RERANK_MODEL = os.environ.get("AI_RERANK_MODEL", "")
# Embeddings are independent of the chat model: a hosted chat API rarely offers
# free embeddings, so the default demo/eval path is a local ONNX sentence model.
AI_EMBEDDING_PROVIDER = os.environ.get("AI_EMBEDDING_PROVIDER", "mock")
AI_EMBEDDING_MODEL = os.environ.get(
    "AI_EMBEDDING_MODEL",
    os.environ.get("AI_EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5"),
)
AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "30"))
AI_MAX_RETRIES = int(os.environ.get("AI_MAX_RETRIES", "2"))
# Spend cap in USD per day. 0 blocks paid calls; the mock provider ignores it.
AI_DAILY_BUDGET_USD = float(os.environ.get("AI_DAILY_BUDGET_USD", "0"))
AI_MOCK_MODE = env_bool("AI_MOCK_MODE", True)
# Monthly clinic allowance in minor units (cents). 0 disables the guard.
AI_MONTHLY_BUDGET_MINOR = int(os.environ.get("AI_MONTHLY_BUDGET_MINOR", "0"))
# USD per one million tokens, per model. Overridable by operators.
AI_PRICE_TABLE = {
    "mock-deterministic": {"prompt": 0.0, "completion": 0.0},
    "openrouter/auto": {"prompt": 0.0, "completion": 0.0},
    "anthropic/claude-3.5-sonnet": {"prompt": 3.0, "completion": 15.0},
    "openai/gpt-4o-mini": {"prompt": 0.15, "completion": 0.6},
    "meta-llama/llama-3.1-8b-instruct:free": {"prompt": 0.0, "completion": 0.0},
}
# Embedding dimension for the RAG vector column (T125). 384 keeps the mock
# embeddings small while remaining a realistic sentence-transformer width. The
# migration bakes this value into the pgvector column, so changing it later is a
# data-carrying migration.
AI_EMBEDDING_DIM = int(os.environ.get("AI_EMBEDDING_DIM", "384"))

# --- ML artifacts ----------------------------------------------------------
# The registry stores the path used when the model was trained. A model trained
# on a workstation and served from a container keeps its Windows/absolute path
# in the row, so the loader also looks for the same filename under this
# directory before failing. Operators can point it at a mounted volume.
ML_ARTIFACT_DIR = os.environ.get(
    "ML_ARTIFACT_DIR", str(BASE_DIR / "artifacts" / "ml")
)

# --- RAG knowledge base (T125) ---------------------------------------------
# Retrieval is clinic-scoped and cosine-ranked. ``RAG_MIN_SCORE`` is the cosine
# similarity (1 - distance) below which the assistant refuses instead of
# answering from weak evidence. Chunking is deterministic and paragraph-aware.
RAG_MIN_SCORE = float(os.environ.get("RAG_MIN_SCORE", "0.2"))
RAG_CHUNK_TOKENS = int(os.environ.get("RAG_CHUNK_TOKENS", "800"))
RAG_CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "120"))
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
# Retrieval mode: ``vector`` (cosine only), ``hybrid`` (cosine + Postgres
# full-text, fused with reciprocal rank fusion). The default is hybrid because
# it is strictly better on the labelled retrieval set; ``vector`` remains
# available for a controlled comparison.
RAG_MODE = os.environ.get("RAG_MODE", "hybrid")
RAG_HYBRID_CANDIDATES = int(os.environ.get("RAG_HYBRID_CANDIDATES", "30"))
RAG_RRF_K = int(os.environ.get("RAG_RRF_K", "60"))
# Rerank the fused candidates before the final top-k. ``fastembed`` uses a
# local ONNX cross-encoder when the optional dependency is installed;
# ``lexical`` is a deterministic term-overlap fallback; ``off`` disables it.
# Measured on the labelled set (backend/artifacts/evals/rag-*.md): with
# bge-small-en-v1.5, vector recall@5 is already 1.0, hybrid ties it, and the
# cross-encoder reranker costs ~4 s per query for a small recall loss. Rerank
# is therefore opt-in; turn it on only after re-measuring on your corpus.
RAG_RERANK = env_bool("RAG_RERANK", False)
RAG_RERANKER = os.environ.get("RAG_RERANKER", "fastembed")
RAG_RERANK_TOP_N = int(os.environ.get("RAG_RERANK_TOP_N", "20"))

# --- Localisation ----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True
DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "INR")

# --- Static and media ------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "/media/"
# Media stays on the local disk by default. ``config.settings.prod`` swaps the
# default backend to S3-compatible object storage (a private Supabase Storage
# bucket) only when ``AWS_STORAGE_BUCKET_NAME`` is configured; see the STORAGES
# block there. Nothing changes for the local Compose stack or the test suite.
_media_root = os.environ.get("MEDIA_ROOT", "").strip()
MEDIA_ROOT = Path(_media_root) if _media_root else BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Celery ----------------------------------------------------------------

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

# Queues declared in config/celery.py: default, reminders, ai, ml, sync.
# Beat schedule (T104). ``scan_due_actions`` reclaims stale claims and
# dispatches every ``JobRequest`` that is due; the other three are the
# reclamation, housekeeping and liveness entries named in ARCHITECTURE.md
# section 11. Schedules are seconds so they stay broker-agnostic.
CELERY_BEAT_SCHEDULE = {
    "scan_due_actions": {
        "task": "core.tasks.scan_due_actions",
        "schedule": 60.0,
    },
    "cleanup_expired": {
        "task": "core.tasks.cleanup_expired",
        "schedule": 86400.0,
    },
    "heartbeat": {
        "task": "core.tasks.heartbeat",
        "schedule": 300.0,
    },
    "reclaim_stale": {
        "task": "core.tasks.reclaim_stale",
        "schedule": 300.0,
    },
    # Every 60 s: cancel held appointments past ``hold_expires_at`` and mark the
    # matching ``booking.ReservationHold`` rows expired, returning the slot to
    # the pool (T112).
    "expire_holds": {
        "task": "booking.tasks.expire_holds",
        "schedule": 60.0,
    },
    # Daily: refresh dental recall due dates from the last completed encounter
    # (T114). Deferred/retried sends ride the shared scan_due_actions scan.
    "messaging_recall_recompute": {
        "task": "messaging.tasks.recall_recompute",
        "schedule": 86400.0,
    },
    # Every 60 s: release expired waitlist offers and their held slots (T116).
    "expire_waitlist_offers": {
        "task": "scheduling.tasks.expire_waitlist_offers",
        "schedule": 60.0,
    },
}

# --- Durable jobs (T104) ---------------------------------------------------
# Retention and retry tunables for core.JobRequest. Backoff follows
# ADR-008: base 30 s, factor 2, +/-20% jitter, dead-letter after 5 attempts.
JOB_BACKOFF_BASE_SECONDS = 30.0
JOB_BACKOFF_FACTOR = 2.0
JOB_BACKOFF_JITTER = 0.2
JOB_STALE_RUNNING_SECONDS = 600
JOB_DUE_BATCH_SIZE = 100
JOB_DONE_RETENTION_DAYS = 7
JOB_FAILED_RETENTION_DAYS = 30
SERVICE_NAME = "enamel-worker"

# --- Messaging (T114) ------------------------------------------------------
# The dev adapter never opens a socket; no real credentials belong here. Live
# transports stay unconfigured until an operator supplies the TWILIO_*/
# WHATSAPP_* values in the environment.
#
# The two placeholder literals below are the *only* webhook secrets the
# development settings may fall back to. ``config.settings.prod`` refuses to
# start while one of them is still in place, so a well-known default can never
# reach a production deployment.
DEV_MESSAGING_WEBHOOK_SECRET = "dev-messaging-webhook-secret"
DEV_PAYMENT_WEBHOOK_SECRET = "dev-payment-webhook-secret"

MESSAGING_ADAPTER = os.environ.get("MESSAGING_ADAPTER", "dev")
MESSAGING_WEBHOOK_SECRET = (
    os.environ.get("MESSAGING_WEBHOOK_SECRET") or DEV_MESSAGING_WEBHOOK_SECRET
)
MESSAGING_WEBHOOK_TOLERANCE_SECONDS = int(
    os.environ.get("MESSAGING_WEBHOOK_TOLERANCE_SECONDS", "300")
)
MESSAGING_QUIET_HOURS_START = int(os.environ.get("MESSAGING_QUIET_HOURS_START", "21"))
MESSAGING_QUIET_HOURS_END = int(os.environ.get("MESSAGING_QUIET_HOURS_END", "8"))
MESSAGING_FREQUENCY_CAP = int(os.environ.get("MESSAGING_FREQUENCY_CAP", "4"))
MESSAGING_FREQUENCY_PERIOD_DAYS = int(
    os.environ.get("MESSAGING_FREQUENCY_PERIOD_DAYS", "7")
)
MESSAGING_MAX_ATTEMPTS = int(os.environ.get("MESSAGING_MAX_ATTEMPTS", "3"))
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")

# --- Waitlist (T116) -------------------------------------------------------
WAITLIST_OFFER_TTL_MINUTES = int(os.environ.get("WAITLIST_OFFER_TTL_MINUTES", "120"))

# --- Plans, consent and documents (T117) -----------------------------------
# External e-signature is disabled unless an operator names a provider; the
# adapter raises AdapterNotConfigured while this is empty.
ESIGN_PROVIDER = os.environ.get("ESIGN_PROVIDER", "")
DOCUMENT_MAX_BYTES = int(os.environ.get("DOCUMENT_MAX_BYTES", str(25 * 1024 * 1024)))
DOCUMENT_ALLOWED_MIME = env_list(
    "DOCUMENT_ALLOWED_MIME",
    "application/pdf,image/png,image/jpeg,image/webp,text/plain",
)
DOCUMENT_DOWNLOAD_TTL_SECONDS = int(
    os.environ.get("DOCUMENT_DOWNLOAD_TTL_SECONDS", "300")
)

# --- Billing, payments and reconciliation (T119) ---------------------------
# The sandbox gateway signs payloads locally; it never opens a socket. A live
# gateway stays unconfigured until an operator supplies the credentials.
PAYMENT_GATEWAY = os.environ.get("PAYMENT_GATEWAY", "sandbox")
PAYMENT_WEBHOOK_SECRET = (
    os.environ.get("PAYMENT_WEBHOOK_SECRET") or DEV_PAYMENT_WEBHOOK_SECRET
)
PAYMENT_WEBHOOK_TOLERANCE_SECONDS = int(
    os.environ.get("PAYMENT_WEBHOOK_TOLERANCE_SECONDS", "300")
)

# --- Imaging archive (T127) ------------------------------------------------
# Upload caps and the de-identification tag list. The defaults are deliberately
# conservative: a DICOM file larger than ``IMAGING_MAX_BYTES`` is rejected before
# anything is written, and every identifier-like attribute listed here is removed
# from the stored metadata (pixel data is never touched). The default cap is
# 50 MB, matching free-tier object storage (Supabase Storage) and the nginx
# ``client_max_body_size`` of both web tiers; it must stay in sync with
# ``apps/imaging/services/ingest.py::DEFAULT_MAX_BYTES``.
IMAGING_MAX_BYTES = int(os.environ.get("IMAGING_MAX_BYTES", str(50 * 1024 * 1024)))
IMAGING_DAILY_INSTANCE_QUOTA = int(os.environ.get("IMAGING_DAILY_INSTANCE_QUOTA", "500"))
IMAGING_DEID_TAGS = env_list(
    "IMAGING_DEID_TAGS",
    ",".join(
        [
            "PatientName",
            "PatientID",
            "IssuerOfPatientID",
            "OtherPatientIDs",
            "OtherPatientNames",
            "PatientBirthDate",
            "PatientBirthTime",
            "PatientSex",
            "PatientAge",
            "PatientSize",
            "PatientWeight",
            "PatientAddress",
            "PatientTelephoneNumbers",
            "PatientMotherBirthName",
            "PatientBirthName",
            "PatientComments",
            "AccessionNumber",
            "AccessionComments",
            "InstitutionName",
            "InstitutionAddress",
            "InstitutionalDepartmentName",
            "ReferringPhysicianName",
            "ReferringPhysicianAddress",
            "ReferringPhysicianTelephoneNumbers",
            "PerformingPhysicianName",
            "OperatorsName",
            "PhysiciansOfRecord",
            "RequestingPhysician",
            "DeviceSerialNumber",
            "StationName",
            "StudyID",
        ]
    ),
)
IMAGING_PREVIEW_MAX_PIXELS = int(os.environ.get("IMAGING_PREVIEW_MAX_PIXELS", "512"))

# --- Assistive imaging analysis (T129) -------------------------------------
# The CV pipeline is a transparent, fixture-validated baseline; it never claims
# clinical validity. ``IMAGING_CV_CONFIG`` points at the YAML preprocessing +
# inference contract recorded on every inference job.
IMAGING_CV_CONFIG = os.environ.get("IMAGING_CV_CONFIG", "ml/cv/configs/fixture.yaml")

# --- Interoperability / PMS connectors (T130) ------------------------------
# Live connectors are refused unless an operator both stores credentials and
# explicitly opts in. ``CONNECTOR_ALLOWED_HOSTS`` is the outbound allowlist for
# the SSRF guard; an empty allowlist blocks every live base URL.
#
# ``CONNECTOR_KEY`` is empty in development, where the connector key is derived
# from ``SECRET_KEY``. ``config.settings.prod`` refuses to start without an
# explicit key.
CONNECTOR_KEY = os.environ.get("CONNECTOR_KEY", "")
CONNECTOR_ALLOW_LIVE = env_bool("CONNECTOR_ALLOW_LIVE", False)
CONNECTOR_ALLOWED_HOSTS = env_list("CONNECTOR_ALLOWED_HOSTS", "")

# --- Insurance and RCM (T134) ----------------------------------------------
# India-first: the RCM module is off by default. While disabled every RCM
# endpoint answers 404, so a self-pay deployment never exposes claims surface.
# The adapter is a deterministic mock; there is no network path in v1.
RCM_ENABLED = env_bool("RCM_ENABLED", False)
RCM_ADAPTER = os.environ.get("RCM_ADAPTER", "mock")

# --- Demo mode -------------------------------------------------------------
# ``DEMO_MODE`` is a deployment-level switch for a hosted demo of this
# repository (the compose overlay that serves it). It does exactly two things:
# the portal request-code response may carry the generated code in an
# explicitly labelled ``demo_code`` field plus a short ``demo_notice``, which
# makes the public demo of the portal usable. It must NEVER be enabled on a
# deployment that stores real patient data, and the production settings refuse
# to start with it (config/settings/prod.py).
DEMO_MODE = env_bool("DEMO_MODE", False)
DEMO_OTP_NOTICE = (
    "Demo mode: the server runs with DEMO_MODE, so the one-time code is shown "
    "here. A deployment without demo mode never reveals it; it is delivered "
    "to the patient contact instead."
)

# --- Patient portal (T121) -------------------------------------------------
# The portal cookie is separate from the staff session cookie. OTP codes live in
# the log/outbox in development; ``PORTAL_OTP_ECHO`` may echo the code in DEBUG
# only and is never honoured by the production settings.
PORTAL_SESSION_COOKIE_NAME = "enamel_portal"
PORTAL_SESSION_TTL_HOURS = int(os.environ.get("PORTAL_SESSION_TTL_HOURS", "12"))
PORTAL_OTP_TTL_MINUTES = int(os.environ.get("PORTAL_OTP_TTL_MINUTES", "10"))
PORTAL_OTP_MAX_ATTEMPTS = int(os.environ.get("PORTAL_OTP_MAX_ATTEMPTS", "5"))
PORTAL_OTP_LENGTH = 6
PORTAL_OTP_ECHO = env_bool("PORTAL_OTP_ECHO", False)
# The portal runs as its own database role (ADR-013, decision D-3). The switch
# is applied automatically when the connection is a non-superuser member of
# that role - which is the ``enamel_app`` runtime - and never for a superuser
# (the compose owner keeps the current behaviour until the role switch in
# T138). "1" forces the switch, "0" forbids it.
PORTAL_DB_ROLE_ENABLED = os.environ.get("ENAMEL_PORTAL_DB_ROLE", "auto").strip().lower()
# Optional file the dev OTP adapter appends the code to (one JSON object per
# line). Off by default: an operator has to ask for it. The compose rehearsal
# stack sets it so the end-to-end harness can read the code of a synthetic
# patient when the API does not echo it. The file holds contact identifiers
# and one-time codes, so it is sensitive and never belongs in the log stream.
PORTAL_OTP_LOG_PATH = os.environ.get("PORTAL_OTP_LOG_PATH", "").strip()

# --- Analytics and reports (T133) ------------------------------------------
# A materialised export is capped and its signed download link is short-lived.
ANALYTICS_EXPORT_ROW_CAP = int(os.environ.get("ANALYTICS_EXPORT_ROW_CAP", "10000"))
ANALYTICS_EXPORT_TTL_SECONDS = int(
    os.environ.get("ANALYTICS_EXPORT_TTL_SECONDS", "300")
)

# --- Logging ---------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOGGING = build_logging_config(level=LOG_LEVEL)
