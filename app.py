#!/usr/bin/env python3
"""FastAPI app for Fund Registry."""

from __future__ import annotations

import base64
import binascii
from collections import defaultdict, deque
import datetime as dt
import decimal
import hashlib
import html
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import sqlite3
import string
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import quote, urlparse

import qrcode
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware


from bitcoin_address import validate_bitcoin_address


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "fundregistry.db"
DEFAULT_PUBLIC_BASE_URL = "https://fundregistry.org"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 43134
DEFAULT_SATS_PER_USD = 1200
DEFAULT_MEMPOOL_BASE_URL = "https://mempool.space/api"
DEFAULT_TX_CACHE_TTL_SECONDS = 600
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_BITCOIN_WALLET_NAME = "fund-registry-anchor"
DEFAULT_PAYMENT_WALLET_NAME = "fund-registry-payments"
DEFAULT_BITCOIN_BACKEND = "local"
MAX_BITCOIN_BACKEND_BLOCK_LAG = 2
MESSAGES_ADMIN_TOKEN_HEADER = "X-Fund-Registry-Admin-Token"
PAYMENTS_PAUSED_MESSAGE = "Bitcoin payments are temporarily paused while Fund Registry UI updates are in progress."
PAYMENT_DETAILS_REDACTED_MESSAGE = (
    "Bitcoin checkout is available, but payment details are intentionally hidden during invite-code testing."
)
SATOSHIS_PER_BTC = decimal.Decimal("100000000")
BTC_QUANTUM = decimal.Decimal("0.00000001")
BITCOIN_BACKEND_ALIASES = {
    "local": "local",
    "bitcoind-local": "local",
    "ssh": "ssh",
    "remote": "ssh",
    "bitcoind-ssh": "ssh",
}
TIER2_DEFAULT_AMOUNT_CENTS = 0
TIER3_DEFAULT_AMOUNT_CENTS = 0
TIER2_DURATION_DAYS = 30
TIER3_DURATION_DAYS = 90
FREE_DURATION_DAYS = 10
PAID_GRACE_DAYS = 30
FREE_GRACE_DAYS = 10
INVOICE_EXPIRY_MINUTES = 15
PAYMENT_CONFIRMATION_TARGET = 1
LINK_EDIT_GRACE_HOURS = 24
STATS_CACHE_TTL_SECONDS = 300
PROOF_PAYLOAD_VERSION = 1
PROOF_CHALLENGE_VERSION = 1
EVENT_PAYLOAD_VERSION = 1
PROOF_STATEMENT = "I control the Bitcoin wallet listed for this Fund Registry page."
PROOF_CHALLENGE_STATEMENT = "I confirm control of the Bitcoin wallet for this one-time Fund Registry proof challenge."
ABORT_STATEMENT = (
    "This Fund Registry page has been intentionally aborted and should no longer be treated as an active funding page."
)
COMPROMISED_STATEMENT = (
    "This Fund Registry page has been marked compromised and should not be treated as active."
)
ANCHOR_RECEIPT_FORMAT = "FRG1"
ANCHOR_RECEIPT_MAGIC = ANCHOR_RECEIPT_FORMAT.encode("ascii")
ANCHOR_RECEIPT_VERSION = 1
ANCHOR_HASH_ALGORITHM_SHA256 = 0x01
ANCHOR_HASH_ALGORITHM_LABEL = "sha256"
ANCHOR_EVENT_CODES = {
    "activated": 0x01,
    "aborted": 0x02,
    "compromised": 0x03,
}
ANCHOR_PAYLOAD_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_EVENT_TYPES = {"activated", "aborted", "compromised", "expired"}
TIER_ALIASES = {
    "free": "free",
    "badge": "tier2",
    "vanity": "tier3",
    "tier2": "tier2",
    "tier3": "tier3",
}
CANONICAL_TIERS = {"free", "tier2", "tier3"}

STATIC_HTML_FILES = {
    "campaign-key.html",
    "create.html",
    "fund-badge.html",
    "fund-expired.html",
    "fund-free.html",
    "fund-tombstone.html",
    "fund-vanity.html",
    "how-it-works.html",
    "index.html",
    "renew.html",
    "terms.html",
    "manage.html",
    "how-it-works.html",
}
STATIC_JS_FILES = {
    "campaign-key.js",
    "contact-bubble.js",
    "create.js",
    "fund-demo.js",
    "fund-expired.js",
    "index.js",
    "manage.js",
    "renew.js",
}

PAGE_TITLE_SUFFIX = " — Fund Registry"
BETA_BANNER_HTML = '<div class="beta-banner"><strong>Beta</strong> Fund Registry is in beta. Features, data, and terms may change without notice. <a href="/terms.html#beta">Learn more</a></div>'
CONTACT_BUBBLE_HTML = """<!-- Contact bubble -->
<button id="cb-toggle" class="cb-toggle" aria-label="Send feedback"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></button>
<div id="cb-card" class="cb-card">
  <div class="cb-header"><span>Send us a message</span><button class="cb-close">&times;</button></div>
  <div id="cb-form">
    <div class="cb-body">
      <textarea id="cb-msg" placeholder="Question, feedback, or just say hi" maxlength="2000"></textarea>
      <input type="email" id="cb-email" placeholder="Email if you'd like a reply">
      <input type="text" id="cb-website" style="display:none" tabindex="-1" autocomplete="off">
    </div>
    <div class="cb-footer"><button id="cb-send" class="cb-send">Send</button></div>
  </div>
  <div id="cb-status" class="cb-status" style="display:none"></div>
</div>
<script src="/assets/contact-bubble.js" defer></script>"""
TRUST_BOUNDARY_COPY = (
    "This badge proves wallet control, not the truth of any claims."
)
VERIFICATION_CODE_LENGTH = 6
VERIFICATION_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_STORY_PHOTO_BYTES = 200 * 1024
TITLE_BUTTON_MAX_CHARS = 30
ALLOWED_STORY_PHOTO_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
PROMO_CODE_MAX_LENGTH = 40
ALLOWED_PUBLIC_LINK_SCHEMES = {"https"}
DEFAULT_ALLOWED_HOSTS = [
    "fundregistry.org",
    "www.fundregistry.org",
    "localhost",
    "127.0.0.1",
    "testserver",
]
STYLE_ATTR_PATTERN = re.compile(r"""style=(?:"([^"]+)"|'([^']+)')""")
STATIC_HTML_CSP_SOURCES = {
    "/": ("index.html",),
    "/create": ("create.html",),
    "/create.html": ("create.html",),
    "/renew": ("renew.html",),
    "/renew.html": ("renew.html",),
    "/how-verification-works": ("how-it-works.html",),
    "/how-it-works": ("how-it-works.html",),
    "/how-it-works.html": ("how-it-works.html",),
    "/campaign-key": ("campaign-key.html",),
    "/campaign-key.html": ("campaign-key.html",),
    "/manage": ("manage.html", "manage.js"),
    "/manage.html": ("manage.html", "manage.js"),
    "/fund-free.html": ("fund-free.html",),
    "/fund-badge.html": ("fund-badge.html",),
    "/fund-vanity.html": ("fund-vanity.html",),
    "/fund-expired.html": ("fund-expired.html",),
    "/fund-tombstone.html": ("fund-tombstone.html",),
    "/terms.html": ("terms.html",),
}
SECURITY_LOGGER = logging.getLogger("fund_registry.security")
SECURITY_LOGGER.setLevel(logging.INFO)
if not SECURITY_LOGGER.handlers:
    security_handler = logging.StreamHandler(sys.stdout)
    security_handler.setFormatter(logging.Formatter("%(message)s"))
    SECURITY_LOGGER.addHandler(security_handler)
SECURITY_LOGGER.propagate = False


class LinkInput(BaseModel):
    link_type: Optional[str] = None
    platform: Optional[str] = None
    url: str


class CreatePageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    btc_address: str
    tier: str = "free"
    vanity_slug: Optional[str] = None
    goal_btc: Optional[str] = None
    links: list[LinkInput] = []


class CampaignKeyPayload(BaseModel):
    version: int
    registry: str
    page_id: str
    key_id: str
    key_version: int
    secret: str


class ManagePageRequest(BaseModel):
    campaign_key: CampaignKeyPayload


class PageUpdateRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    body: str


class PageLinksUpdateRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    links: list[LinkInput] = []


class StoryPhotoUploadRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    content_type: str
    image_base64: str


class ProgressPhotoUploadRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    content_type: str
    image_base64: str


class UpgradePageRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    target_tier: str
    vanity_slug: Optional[str] = None


class PromoCodeValidateRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    code: str
    target_tier: Optional[str] = None
    vanity_slug: Optional[str] = None


class PromoCodeApplyRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    code: str
    target_tier: str
    vanity_slug: Optional[str] = None


class RenewPageRequest(BaseModel):
    campaign_key: CampaignKeyPayload


class ArchivePageRequest(BaseModel):
    campaign_key: CampaignKeyPayload


class LifecyclePageRequest(BaseModel):
    campaign_key: CampaignKeyPayload


class ReportPageRequest(BaseModel):
    reason: str
    note: Optional[str] = None


class ProofVerifyRequest(BaseModel):
    proof: str


class PageProofPrepareRequest(BaseModel):
    campaign_key: CampaignKeyPayload


class PageProofVerifyRequest(BaseModel):
    campaign_key: CampaignKeyPayload
    challenge_id: str
    proof: str


class RecoverRequest(BaseModel):
    page_ref: str


class MessageCreateRequest(BaseModel):
    message: Optional[str] = None
    email: Optional[str] = None
    page_url: Optional[str] = None
    website: Optional[str] = None


class CampaignKeyAuthFailure(HTTPException):
    def __init__(self, status_code: int, detail: str, *, reason_code: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    method: str
    path_pattern: re.Pattern[str]
    limit: int
    window_seconds: int = 60


@dataclass
class FundRegistrySettings:
    db_path: Path = DEFAULT_DB_PATH
    static_dir: Path = STATIC_DIR
    photo_dir: Path = DATA_DIR / "story-photos"
    transaction_cache_dir: Path = DATA_DIR / "tx-cache"
    messages_path: Path = DATA_DIR / "messages.jsonl"
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL
    mempool_base_url: str = DEFAULT_MEMPOOL_BASE_URL
    transaction_cache_ttl_seconds: int = DEFAULT_TX_CACHE_TTL_SECONDS
    cors_origins: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=lambda: DEFAULT_ALLOWED_HOSTS.copy())
    allow_dev_actions: bool = False
    payment_mode: str = "disabled"
    proof_mode: str = "disabled"
    anchor_mode: str = "disabled"
    bitcoin_cli_path: str = "bitcoin-cli"
    bitcoin_conf_path: str = ""
    bitcoin_wallet_name: str = DEFAULT_BITCOIN_WALLET_NAME
    payment_wallet_name: str = DEFAULT_PAYMENT_WALLET_NAME
    bitcoin_backend: str = DEFAULT_BITCOIN_BACKEND
    bitcoin_backend_source: str = "default-local"
    bitcoin_ssh_host: Optional[str] = None
    payment_confirmation_target: int = PAYMENT_CONFIRMATION_TARGET
    payment_expiry_minutes: int = INVOICE_EXPIRY_MINUTES
    sats_per_usd: int = DEFAULT_SATS_PER_USD
    tier2_amount_sats_override: Optional[int] = None
    tier3_amount_sats_override: Optional[int] = None
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    payments_paused: bool = False
    payment_details_redacted: bool = False
    messages_admin_token: Optional[str] = None
    fetch_json_fn: Optional[Callable[[str], Any]] = None
    bitcoin_cli_fn: Optional[Callable[[list[str], Optional[str]], Any]] = None
    now_fn: Callable[[], dt.datetime] = field(default_factory=lambda: now_utc)
    csp_policy: str = ""
    route_csp_policies: dict[str, str] = field(default_factory=dict)


class InMemoryRateLimiter:
    def __init__(self, rules: tuple[RateLimitRule, ...]) -> None:
        self.rules = rules
        self._events: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def match(self, method: str, path: str) -> Optional[RateLimitRule]:
        for rule in self.rules:
            if rule.method == method and rule.path_pattern.fullmatch(path):
                return rule
        return None

    def check(self, rule: RateLimitRule, subject: str) -> Optional[int]:
        now = time.monotonic()
        bucket_key = (rule.name, subject)
        with self._lock:
            bucket = self._events[bucket_key]
            cutoff = now - rule.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= rule.limit:
                oldest = bucket[0]
                retry_after = max(1, int(rule.window_seconds - (now - oldest)) + 1)
                return retry_after
            bucket.append(now)
        return None


RATE_LIMIT_RULES = (
    RateLimitRule("page_create", "POST", re.compile(r"^/v1/pages$"), 10),
    RateLimitRule("campaign_key_manage", "POST", re.compile(r"^/v1/pages/manage$"), 10),
    RateLimitRule("contact_message", "POST", re.compile(r"^/v1/messages$"), 3, window_seconds=3600),
    RateLimitRule("recovery_challenge", "POST", re.compile(r"^/v1/recover$"), 5),
    RateLimitRule("abuse_report", "POST", re.compile(r"^/v1/pages/[^/]+/report$"), 5),
    RateLimitRule("promo_validate", "POST", re.compile(r"^/v1/promo/validate$"), 10),
    RateLimitRule("promo_apply", "POST", re.compile(r"^/v1/pages/[^/]+/promo/apply$"), 10),
    RateLimitRule("proof_verify", "POST", re.compile(r"^/v1/pages/[^/]+/proof/verify$"), 5),
    RateLimitRule("proof_verify_global", "POST", re.compile(r"^/v1/proofs/[^/]+/verify$"), 5),
)


class PromoCodeError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        public_detail: str = "Invalid or unavailable promo code.",
        status_code: int = 400,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.public_detail = public_detail
        self.status_code = status_code


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_isoformat(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(normalized).astimezone(dt.timezone.utc)


def parse_optional_positive_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    try:
        parsed = int(trimmed)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Fund Registry integer override is misconfigured.") from exc
    if parsed <= 0:
        raise HTTPException(status_code=500, detail="Fund Registry integer override must be positive.")
    return parsed


def unix_timestamp_to_iso(value: Any) -> Optional[str]:
    if value in {None, ""}:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return utc_isoformat(dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc))


def format_long_date(value: Optional[str]) -> str:
    if not value:
        return "Unknown"
    parsed = parse_timestamp(value)
    if parsed is None:
        return "Unknown"
    return parsed.strftime("%B %-d, %Y")


def format_short_date(value: Optional[str]) -> str:
    if not value:
        return "Unknown"
    parsed = parse_timestamp(value)
    if parsed is None:
        return "Unknown"
    return parsed.strftime("%b %-d")


def bitcoin_uri(address: str) -> str:
    return f"bitcoin:{address}"


def sats_to_btc_string(amount_sat: int) -> str:
    amount = (decimal.Decimal(int(amount_sat)) / SATOSHIS_PER_BTC).quantize(BTC_QUANTUM)
    return format(amount, "f")


def btc_amount_to_sats(value: Any) -> int:
    amount = decimal.Decimal(str(value)).quantize(BTC_QUANTUM)
    return int((amount * SATOSHIS_PER_BTC).to_integral_value(rounding=decimal.ROUND_HALF_UP))


def build_bip21_uri(address: str, amount_btc: str, label: str, message: str) -> str:
    query = urllib_parse.urlencode({"amount": amount_btc, "label": label, "message": message})
    return f"bitcoin:{address}?{query}"


def render_qr_png_data_uri(value: str) -> str:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def canonicalize_base_url(value: str) -> str:
    trimmed = value.strip().rstrip("/")
    return trimmed or DEFAULT_PUBLIC_BASE_URL


def normalize_tier(value: str) -> str:
    tier = value.strip().lower()
    canonical = TIER_ALIASES.get(tier)
    if canonical is None:
        raise HTTPException(status_code=400, detail="Unsupported tier.")
    return canonical


def normalize_mode(value: str, *, allowed: set[str]) -> str:
    mode = value.strip().lower()
    if mode not in allowed:
        raise HTTPException(status_code=500, detail="Fund Registry mode is misconfigured.")
    return mode


def normalize_bitcoin_backend(value: Optional[str], *, ssh_host: Optional[str]) -> tuple[str, str]:
    candidate = normalize_optional_text(value)
    if candidate is None:
        if ssh_host:
            return "ssh", "inferred-from-ssh-host"
        return DEFAULT_BITCOIN_BACKEND, "default-local"
    normalized = BITCOIN_BACKEND_ALIASES.get(candidate.lower())
    if normalized is None:
        raise HTTPException(status_code=500, detail="Fund Registry Bitcoin backend is misconfigured.")
    return normalized, "explicit"


def bitcoin_message_address_supported(address: str) -> bool:
    # Bitcoin Core signmessage/verifymessage currently works for legacy P2PKH addresses only.
    return address.strip().startswith("1")


def bitcoin_message_address_requirement_detail() -> str:
    return (
        "Fund Registry wallet proof currently requires a legacy Bitcoin address "
        "(starts with 1). bc1 and 3-addresses are not supported yet."
    )


def mixed_proof_supported_address_detail() -> str:
    return (
        "Fund Registry wallet proof currently supports legacy 1-addresses and native "
        "SegWit bc1q-addresses. Taproot bc1p-addresses and 3-addresses are not supported yet."
    )


def taproot_address_requirement_detail() -> str:
    return (
        "Fund Registry wallet proof for Taproot addresses (bc1p...) is not live yet. "
        "Use a legacy 1-address or a native SegWit bc1q-address for now."
    )


def p2sh_address_requirement_detail() -> str:
    return (
        "Fund Registry wallet proof for 3-addresses is not live yet. "
        "Use a legacy 1-address or a native SegWit bc1q-address for now."
    )


def configured_wallet_proof_method(address: str, proof_mode: str) -> tuple[Optional[str], Optional[str]]:
    candidate = address.strip()
    lowered = candidate.lower()
    if proof_mode == "mock":
        return "mock", None
    if proof_mode == "bitcoin-message":
        if bitcoin_message_address_supported(candidate):
            return "bitcoin-message", None
        return None, bitcoin_message_address_requirement_detail()
    if proof_mode == "mixed":
        if bitcoin_message_address_supported(candidate):
            return "bitcoin-message", None
        if lowered.startswith("bc1q"):
            return "bip322-simple", None
        if lowered.startswith("bc1p"):
            return None, taproot_address_requirement_detail()
        if candidate.startswith("3"):
            return None, p2sh_address_requirement_detail()
        return None, mixed_proof_supported_address_detail()
    if proof_mode == "disabled":
        return None, "Wallet proof verification is not enabled yet."
    return None, "Fund Registry wallet proof mode is misconfigured."


def proof_method_display_name(method: Optional[str]) -> str:
    normalized = (method or "").strip().lower()
    if normalized == "bitcoin-message":
        return "Bitcoin Signed Message"
    if normalized == "bip322-simple":
        return "BIP-322 simple"
    if normalized == "mock":
        return "Mock"
    if normalized == "unconfigured":
        return "Unconfigured"
    return method or "Unknown"


def proof_instructions_text(method: str) -> str:
    if method == "bip322-simple":
        return (
            "Sign this exact one-time challenge payload with a wallet that supports BIP-322 simple "
            "signing for the listed bc1q address, then submit the base64 signature."
        )
    return "Sign this exact one-time challenge payload with the listed Bitcoin wallet, then submit the base64 signature."


def is_paid_tier(value: str) -> bool:
    return normalize_tier(value) in {"tier2", "tier3"}


def amount_cents_for_tier(tier: str) -> int:
    tier = normalize_tier(tier)
    if tier == "tier2":
        return TIER2_DEFAULT_AMOUNT_CENTS
    if tier == "tier3":
        return TIER3_DEFAULT_AMOUNT_CENTS
    return 0


def amount_sats_override_for_tier(settings: FundRegistrySettings, tier: str) -> Optional[int]:
    tier = normalize_tier(tier)
    if tier == "tier2":
        return settings.tier2_amount_sats_override
    if tier == "tier3":
        return settings.tier3_amount_sats_override
    return None


def amount_quote_for_tier(settings: FundRegistrySettings, tier: str) -> tuple[int, int]:
    amount_usd_cents = amount_cents_for_tier(tier)
    override_sats = amount_sats_override_for_tier(settings, tier)
    if override_sats is not None:
        amount_sats = max(1, int(override_sats))
        effective_usd_cents = max(1, round((amount_sats / settings.sats_per_usd) * 100))
        return effective_usd_cents, amount_sats
    amount_sats = max(1, round((amount_usd_cents / 100) * settings.sats_per_usd))
    return amount_usd_cents, amount_sats


def active_days_for_tier(tier: str) -> int:
    tier = normalize_tier(tier)
    if tier == "free":
        return FREE_DURATION_DAYS
    if tier == "tier2":
        return TIER2_DURATION_DAYS
    return TIER3_DURATION_DAYS


def grace_days_for_tier(tier: str) -> int:
    return FREE_GRACE_DAYS if normalize_tier(tier) == "free" else PAID_GRACE_DAYS


def normalize_slug(value: str) -> str:
    candidate = value.strip().lower()
    if not candidate:
        raise HTTPException(status_code=400, detail="Tier3 slug is required.")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    if any(char not in allowed for char in candidate):
        raise HTTPException(status_code=400, detail="Tier3 slug may only use letters, numbers, and hyphens.")
    if candidate.startswith("-") or candidate.endswith("-") or "--" in candidate:
        raise HTTPException(status_code=400, detail="Tier3 slug format is invalid.")
    if len(candidate) < 3 or len(candidate) > 48:
        raise HTTPException(status_code=400, detail="Tier3 slug must be between 3 and 48 characters.")
    return candidate


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def normalize_message_field(value: Any, *, field_name: str, required: bool = False, max_length: int) -> Optional[str]:
    if value is None:
        if required:
            raise HTTPException(status_code=400, detail=f"{field_name} is required.")
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field_name} must be text.")
    trimmed = value.strip()
    if required and not trimmed:
        raise HTTPException(status_code=400, detail=f"{field_name} is required.")
    if not trimmed:
        return None
    if len(trimmed) > max_length:
        raise HTTPException(status_code=400, detail=f"{field_name} must be {max_length} characters or fewer.")
    return trimmed


def normalize_message_request_payload(body: Any) -> MessageCreateRequest:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    return MessageCreateRequest(
        message=normalize_message_field(body.get("message"), field_name="Message", required=True, max_length=2000),
        email=normalize_message_field(body.get("email"), field_name="Email", max_length=200),
        page_url=normalize_message_field(body.get("page_url"), field_name="Page URL", max_length=500),
        website=normalize_message_field(body.get("website"), field_name="Website", max_length=200),
    )


def normalize_goal_btc(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    try:
        parsed = float(trimmed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Goal BTC must be numeric.") from exc
    if parsed <= 0:
        raise HTTPException(status_code=400, detail="Goal BTC must be greater than zero.")
    return f"{parsed:.8f}".rstrip("0").rstrip(".")


def normalize_promo_code(value: str) -> str:
    candidate = value.strip().upper()
    if not candidate:
        raise HTTPException(status_code=400, detail="Promo code is required.")
    allowed = set(string.ascii_uppercase + string.digits + "-_")
    if len(candidate) > PROMO_CODE_MAX_LENGTH or any(char not in allowed for char in candidate):
        raise HTTPException(status_code=400, detail="Promo code format is invalid.")
    return candidate


def normalize_link_platform(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def normalize_link_url(value: Optional[str]) -> Optional[str]:
    url = normalize_optional_text(value)
    if url is None:
        return None
    parsed = urllib_parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_PUBLIC_LINK_SCHEMES:
        raise HTTPException(status_code=400, detail="Links must use https:// URLs.")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="Links must include a hostname.")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Links may not embed credentials.")
    return urllib_parse.urlunsplit(parsed)


def make_random_slug() -> str:
    return secrets.token_hex(6)


def make_verification_code() -> str:
    return "".join(secrets.choice(VERIFICATION_CODE_ALPHABET) for _ in range(VERIFICATION_CODE_LENGTH))


def make_key_id() -> str:
    return f"frk_{secrets.token_hex(8)}"


def make_secret() -> str:
    return f"frk_{secrets.token_hex(32)}"


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def first_paragraph(text: str) -> str:
    for chunk in text.splitlines():
        trimmed = chunk.strip()
        if trimmed:
            return trimmed
    return text.strip()


def truncate_text(value: str, *, limit: int) -> str:
    trimmed = value.strip()
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: max(0, limit - 1)].rstrip() + "…"


def normalize_links_payload(raw_links: list[dict[str, Any]], *, reject_invalid: bool = False) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw_link in raw_links:
        platform = normalize_link_platform(raw_link.get("platform") or raw_link.get("link_type"))
        try:
            url = normalize_link_url(raw_link.get("url"))
        except HTTPException:
            if reject_invalid:
                raise
            continue
        if not platform or not url:
            continue
        normalized.append({"platform": platform, "url": url})
    return normalized


def promo_code_tiers(payload: dict[str, Any]) -> list[str]:
    tiers: list[str] = []
    if bool(payload.get("valid_for_badge")):
        tiers.append("tier2")
    if bool(payload.get("valid_for_vanity")):
        tiers.append("tier3")
    return tiers


def display_tier_label(value: str) -> str:
    tier = normalize_tier(value)
    if tier == "free":
        return "free"
    return tier


def address_fingerprint(address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    trimmed = address.strip()
    if len(trimmed) <= 12:
        return trimmed
    return f"{trimmed[:6]}…{trimmed[-6:]}"


def canonical_json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_base64(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def build_anchor_receipt(payload_hash: Optional[str], event_type: str) -> Optional[dict[str, Any]]:
    normalized_hash = str(payload_hash or "").strip().lower()
    event_code = ANCHOR_EVENT_CODES.get(event_type)
    if event_code is None or not ANCHOR_PAYLOAD_HASH_RE.fullmatch(normalized_hash):
        return None
    digest_bytes = binascii.unhexlify(normalized_hash)
    receipt_bytes = ANCHOR_RECEIPT_MAGIC + bytes([event_code, ANCHOR_HASH_ALGORITHM_SHA256]) + digest_bytes
    return {
        "format": ANCHOR_RECEIPT_FORMAT,
        "version": ANCHOR_RECEIPT_VERSION,
        "magic": ANCHOR_RECEIPT_FORMAT,
        "event_type": event_type,
        "event_code": event_code,
        "event_code_hex": f"0x{event_code:02x}",
        "hash_algorithm": ANCHOR_HASH_ALGORITHM_LABEL,
        "hash_algorithm_code": ANCHOR_HASH_ALGORITHM_SHA256,
        "hash_algorithm_code_hex": f"0x{ANCHOR_HASH_ALGORITHM_SHA256:02x}",
        "digest_hex": normalized_hash,
        "op_return_hex": receipt_bytes.hex(),
        "size_bytes": len(receipt_bytes),
    }


def build_style_attr_hashes(source_paths: list[Path]) -> tuple[str, ...]:
    hashes: set[str] = set()
    for path in source_paths:
        try:
            text = path.read_text()
        except OSError:
            continue
        for match in STYLE_ATTR_PATTERN.finditer(text):
            style_value = next((group for group in match.groups() if group is not None), "").strip()
            if style_value:
                hashes.add(f"'sha256-{sha256_base64(style_value)}'")
    return tuple(sorted(hashes))


def build_csp_policy(source_paths: list[Path]) -> str:
    style_attr_hashes = build_style_attr_hashes(source_paths)
    style_attr_directive = "style-src-attr 'none'"
    if style_attr_hashes:
        style_attr_directive = "style-src-attr 'unsafe-hashes' " + " ".join(style_attr_hashes)
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "style-src-elem 'self'; "
        f"{style_attr_directive}; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )


def build_csp_policies(static_dir: Path) -> tuple[str, dict[str, str]]:
    default_policy = build_csp_policy([Path(__file__)])
    route_policies: dict[str, str] = {}
    for route, source_names in STATIC_HTML_CSP_SOURCES.items():
        source_paths = [static_dir / source_name for source_name in source_names]
        route_policies[route] = build_csp_policy(source_paths)
    return default_policy, route_policies


def request_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    for candidate in forwarded_for.split(","):
        trimmed = candidate.strip()
        if trimmed:
            return trimmed
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def normalize_source_host(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    candidate = value.split(",", 1)[0].strip().lower()
    if not candidate:
        return None
    if "://" in candidate:
        parsed = urlparse(candidate)
        candidate = parsed.netloc or parsed.path
    candidate = candidate.strip().strip("[]")
    if "@" in candidate or "/" in candidate:
        return None
    if ":" in candidate:
        host, port = candidate.rsplit(":", 1)
        if port.isdigit():
            candidate = host
    candidate = candidate.strip(".")
    if not candidate or len(candidate) > 253:
        return None
    if not re.fullmatch(r"[a-z0-9.-]+", candidate):
        return None
    return candidate


def request_source_host(request: Request) -> str:
    for header_name in ("x-fund-registry-source-host", "x-forwarded-host", "host"):
        source_host = normalize_source_host(request.headers.get(header_name))
        if source_host:
            return source_host
    source_host = normalize_source_host(request.url.hostname)
    return source_host or "unknown"


def anonymize_stored_client_ip(ip_value: str) -> str:
    value = str(ip_value or "").strip()
    if not value or value == "unknown":
        return "unknown"
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return "unknown"
    if isinstance(parsed, ipaddress.IPv4Address):
        return str(ipaddress.ip_network(f"{parsed}/24", strict=False))
    return str(ipaddress.ip_network(f"{parsed}/64", strict=False))


def security_log(event_type: str, **fields: Any) -> None:
    payload = {
        "ts": utc_isoformat(now_utc()),
        "event": event_type,
        **fields,
    }
    SECURITY_LOGGER.info(json.dumps(payload, sort_keys=True, ensure_ascii=True))


def payment_poll_status(payload: dict[str, Any], *, now: Optional[dt.datetime] = None) -> str:
    status = str(payload.get("status") or "").strip().lower()
    current = now or now_utc()
    expires_at = parse_timestamp(payload.get("expires_at"))
    if status in {"paid", "activated"}:
        return "paid"
    if status == "paid_pending_proof":
        return "paid_pending_proof"
    if status == "confirming":
        return "confirming"
    if status == "expired":
        return "expired"
    if status == "pending" and expires_at is not None and expires_at <= current:
        return "expired"
    return "pending"


def image_signature_is_valid(content_type: str, payload: bytes) -> bool:
    if content_type == "image/jpeg":
        return payload.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP"
    return False


def decode_base64_bytes(raw_value: str) -> bytes:
    try:
        return base64.b64decode(raw_value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Story photo encoding is invalid.") from exc


def normalize_search_query(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Search query is required.")
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        if "/fund/" in path:
            return urllib_parse.unquote(path.rsplit("/fund/", 1)[-1].lstrip("/"))
        return urllib_parse.unquote(path.rsplit("/", 1)[-1]) if path else raw
    return raw


class FundRegistryStore:
    def __init__(self, settings: FundRegistrySettings) -> None:
        self.settings = settings
        self._stats_cache_payload: Optional[dict[str, Any]] = None
        self._stats_cache_expires_at = 0.0
        self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.photo_dir.mkdir(parents=True, exist_ok=True)
        self.settings.transaction_cache_dir.mkdir(parents=True, exist_ok=True)
        self.settings.messages_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _invalidate_stats_cache(self) -> None:
        self._stats_cache_payload = None
        self._stats_cache_expires_at = 0.0

    def init_schema(self) -> None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS pages (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    slug_kind TEXT NOT NULL,
                    verification_code TEXT,
                    tier TEXT NOT NULL,
                    requested_tier TEXT,
                    pending_vanity_slug TEXT,
                    public_state TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    story_photo_path TEXT,
                    story_photo_media_type TEXT,
                    progress_photo_path TEXT,
                    progress_photo_media_type TEXT,
                    btc_address TEXT NOT NULL,
                    links_json TEXT NOT NULL DEFAULT '[]',
                    links_editable_until TEXT,
                    goal_btc TEXT,
                    amount_raised_btc TEXT,
                    contribution_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    active_until TEXT NOT NULL,
                    grace_until TEXT NOT NULL,
                    deleted_at TEXT,
                    tombstoned_at TEXT,
                    wallet_proof_method TEXT,
                    wallet_proof_verified_at TEXT
                );

                CREATE TABLE IF NOT EXISTS campaign_keys (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    key_id TEXT NOT NULL UNIQUE,
                    key_version INTEGER NOT NULL,
                    secret_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revocation_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS page_updates (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS payment_intents (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    target_tier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payment_method TEXT NOT NULL DEFAULT 'mock',
                    amount_usd_cents INTEGER NOT NULL,
                    amount_sats INTEGER NOT NULL,
                    amount_btc TEXT,
                    payment_reference TEXT NOT NULL,
                    invoice TEXT NOT NULL,
                    payment_address TEXT,
                    payment_uri TEXT,
                    confirmation_target INTEGER NOT NULL DEFAULT 1,
                    confirmations INTEGER NOT NULL DEFAULT 0,
                    txids_json TEXT NOT NULL DEFAULT '[]',
                    unconfirmed_received_sats INTEGER NOT NULL DEFAULT 0,
                    confirmed_received_sats INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    paid_at TEXT,
                    activated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS wallet_proof_challenges (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    payment_intent_id TEXT,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    challenge_text TEXT NOT NULL,
                    proof_method TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    verified_at TEXT
                );

                CREATE TABLE IF NOT EXISTS wallet_proof_records (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    challenge_id TEXT,
                    payment_intent_id TEXT,
                    tier TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    signature_method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    verified_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS page_events (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    payload_json TEXT,
                    payload_hash TEXT,
                    proof_record_id TEXT,
                    anchor_mode TEXT,
                    anchor_status TEXT,
                    anchor_txid TEXT,
                    anchor_block_height INTEGER,
                    anchor_block_hash TEXT,
                    anchor_broadcast_at TEXT,
                    anchor_confirmed_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS slug_tombstones (
                    slug TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS abuse_reports (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    valid_for_badge INTEGER NOT NULL DEFAULT 0,
                    valid_for_vanity INTEGER NOT NULL DEFAULT 0,
                    max_uses INTEGER NOT NULL DEFAULT 0,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                """
            )
            self._ensure_page_columns(cursor)
            self._ensure_promo_code_columns(cursor)
            self._ensure_payment_intent_columns(cursor)
            self._ensure_wallet_proof_challenge_columns(cursor)
            self._migrate_legacy_tiers(cursor)
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_verification_code ON pages(verification_code)")
            self._backfill_verification_codes(cursor)
            self._backfill_links_editable_until(cursor)
            connection.commit()

    def _ensure_page_columns(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(pages)")
        columns = {row[1] for row in cursor.fetchall()}
        if "verification_code" not in columns:
            cursor.execute("ALTER TABLE pages ADD COLUMN verification_code TEXT")
        if "story_photo_path" not in columns:
            cursor.execute("ALTER TABLE pages ADD COLUMN story_photo_path TEXT")
        if "story_photo_media_type" not in columns:
            cursor.execute("ALTER TABLE pages ADD COLUMN story_photo_media_type TEXT")
        if "progress_photo_path" not in columns:
            cursor.execute("ALTER TABLE pages ADD COLUMN progress_photo_path TEXT")
        if "progress_photo_media_type" not in columns:
            cursor.execute("ALTER TABLE pages ADD COLUMN progress_photo_media_type TEXT")
        if "links_editable_until" not in columns:
            cursor.execute("ALTER TABLE pages ADD COLUMN links_editable_until TEXT")

    def _ensure_promo_code_columns(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(promo_codes)")
        columns = {row[1] for row in cursor.fetchall()}
        if columns and "revoked_at" not in columns:
            cursor.execute("ALTER TABLE promo_codes ADD COLUMN revoked_at TEXT")

    def _ensure_payment_intent_columns(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(payment_intents)")
        columns = {row[1] for row in cursor.fetchall()}
        if not columns:
            return
        if "payment_method" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'mock'")
        if "amount_btc" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN amount_btc TEXT")
        if "payment_address" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN payment_address TEXT")
        if "payment_uri" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN payment_uri TEXT")
        if "confirmation_target" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN confirmation_target INTEGER NOT NULL DEFAULT 1")
        if "confirmations" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN confirmations INTEGER NOT NULL DEFAULT 0")
        if "txids_json" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN txids_json TEXT NOT NULL DEFAULT '[]'")
        if "unconfirmed_received_sats" not in columns:
            cursor.execute(
                "ALTER TABLE payment_intents ADD COLUMN unconfirmed_received_sats INTEGER NOT NULL DEFAULT 0"
            )
        if "confirmed_received_sats" not in columns:
            cursor.execute(
                "ALTER TABLE payment_intents ADD COLUMN confirmed_received_sats INTEGER NOT NULL DEFAULT 0"
            )
        if "last_checked_at" not in columns:
            cursor.execute("ALTER TABLE payment_intents ADD COLUMN last_checked_at TEXT")

        cursor.execute("SELECT id, amount_sats, amount_btc, payment_method, confirmation_target, txids_json FROM payment_intents")
        rows = cursor.fetchall()
        for row in rows:
            amount_btc = row["amount_btc"] if isinstance(row, sqlite3.Row) else row[2]
            payment_method = row["payment_method"] if isinstance(row, sqlite3.Row) else row[3]
            confirmation_target = row["confirmation_target"] if isinstance(row, sqlite3.Row) else row[4]
            txids_json = row["txids_json"] if isinstance(row, sqlite3.Row) else row[5]
            updates: list[str] = []
            params: list[Any] = []
            if not payment_method:
                updates.append("payment_method = ?")
                params.append("mock")
            if not amount_btc:
                updates.append("amount_btc = ?")
                params.append(sats_to_btc_string(int(row["amount_sats"])))
            if not confirmation_target:
                updates.append("confirmation_target = ?")
                params.append(self.settings.payment_confirmation_target)
            if not txids_json:
                updates.append("txids_json = '[]'")
            if updates:
                params.append(row["id"])
                cursor.execute(
                    f"UPDATE payment_intents SET {', '.join(updates)} WHERE id = ?",
                    tuple(params),
                )

    def _ensure_wallet_proof_challenge_columns(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(wallet_proof_challenges)")
        columns = {row[1] for row in cursor.fetchall()}
        if columns and "payload_json" not in columns:
            cursor.execute("ALTER TABLE wallet_proof_challenges ADD COLUMN payload_json TEXT")
        if columns and "payload_hash" not in columns:
            cursor.execute("ALTER TABLE wallet_proof_challenges ADD COLUMN payload_hash TEXT")

    def _migrate_legacy_tiers(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("UPDATE pages SET tier = 'tier2' WHERE tier = 'badge'")
        cursor.execute("UPDATE pages SET tier = 'tier3' WHERE tier = 'vanity'")
        cursor.execute("UPDATE pages SET requested_tier = 'tier2' WHERE requested_tier = 'badge'")
        cursor.execute("UPDATE pages SET requested_tier = 'tier3' WHERE requested_tier = 'vanity'")
        cursor.execute("UPDATE payment_intents SET target_tier = 'tier2' WHERE target_tier = 'badge'")
        cursor.execute("UPDATE payment_intents SET target_tier = 'tier3' WHERE target_tier = 'vanity'")
        cursor.execute("UPDATE wallet_proof_records SET tier = 'tier2' WHERE tier = 'badge'")
        cursor.execute("UPDATE wallet_proof_records SET tier = 'tier3' WHERE tier = 'vanity'")

    def _backfill_links_editable_until(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("SELECT id, created_at FROM pages WHERE links_editable_until IS NULL OR links_editable_until = ''")
        rows = cursor.fetchall()
        for row in rows:
            created_at = parse_timestamp(row["created_at"]) or self.settings.now_fn()
            links_editable_until = created_at + dt.timedelta(hours=LINK_EDIT_GRACE_HOURS)
            cursor.execute(
                "UPDATE pages SET links_editable_until = ? WHERE id = ?",
                (utc_isoformat(links_editable_until), row["id"]),
            )

    def _backfill_verification_codes(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("SELECT id FROM pages WHERE verification_code IS NULL OR verification_code = ''")
        rows = cursor.fetchall()
        for row in rows:
            code = self._generate_unique_verification_code()
            cursor.execute("UPDATE pages SET verification_code = ? WHERE id = ?", (code, row["id"]))

    def _generate_unique_verification_code(self) -> str:
        code = make_verification_code()
        while self.verification_code_exists(code):
            code = make_verification_code()
        return code

    def _current_proof_method(self, btc_address: Optional[str] = None) -> str:
        if self.settings.allow_dev_actions:
            return "mock"
        if self.settings.proof_mode == "mock":
            return "mock"
        if btc_address is None:
            if self.settings.proof_mode in {"bitcoin-message", "mixed"}:
                return self.settings.proof_mode
            return "unconfigured"
        method, detail = configured_wallet_proof_method(btc_address, self.settings.proof_mode)
        if method is None:
            raise HTTPException(status_code=400, detail=detail or "Wallet proof is not available for this address.")
        return method

    def _ensure_wallet_proof_address_supported(self, btc_address: str) -> None:
        if self.settings.allow_dev_actions:
            return
        if self.settings.proof_mode not in {"bitcoin-message", "mixed"}:
            return
        method, detail = configured_wallet_proof_method(btc_address, self.settings.proof_mode)
        if method is None:
            raise HTTPException(status_code=400, detail=detail or "Wallet proof is not available for this address.")

    def _challenge_response_payload(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["payload"] = json.loads(payload["payload_json"]) if payload.get("payload_json") else None
        challenge_text = payload.get("challenge_text")
        challenge_payload = None
        if challenge_text:
            try:
                parsed = json.loads(challenge_text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                challenge_payload = parsed
        payload["challenge_payload"] = challenge_payload
        payload["challenge_hash"] = sha256_hex(challenge_text) if challenge_text else None
        return payload

    def _challenge_is_one_time_payload(self, challenge: dict[str, Any]) -> bool:
        challenge_payload = challenge.get("challenge_payload")
        if not isinstance(challenge_payload, dict):
            return False
        required_fields = {
            "version",
            "domain",
            "challenge_id",
            "page_id",
            "page_ref",
            "tier",
            "purpose",
            "btc_address",
            "nonce",
            "issued_at",
            "expires_at",
            "canonical_proof_payload_hash",
            "statement",
        }
        if any(field not in challenge_payload for field in required_fields):
            return False
        if challenge_payload["challenge_id"] != challenge["id"]:
            return False
        if challenge_payload["page_id"] != challenge["page_id"]:
            return False
        if challenge_payload["purpose"] != challenge["purpose"]:
            return False
        if challenge_payload["issued_at"] != challenge["created_at"]:
            return False
        if challenge_payload["expires_at"] != challenge["expires_at"]:
            return False
        if challenge.get("payload_hash") and challenge_payload["canonical_proof_payload_hash"] != challenge["payload_hash"]:
            return False
        return True

    def sweep_pages(self, now: Optional[dt.datetime] = None) -> None:
        current = now or self.settings.now_fn()
        current_iso = utc_isoformat(current)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM pages
                WHERE public_state = 'active'
                  AND active_until < ?
                """,
                (current_iso,),
            )
            expiring_rows = [dict(row) for row in cursor.fetchall()]
            for row in expiring_rows:
                cursor.execute(
                    """
                    UPDATE pages
                    SET public_state = 'expired',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (current_iso, row["id"]),
                )
                self._record_page_event(
                    cursor,
                    page={**row, "page_ref": row["slug"], "tier": normalize_tier(row["tier"]), "public_state": "expired"},
                    event_type="expired",
                    created_at=current_iso,
                    details={"reason": "active_window_elapsed"},
                )
            cursor.execute(
                """
                SELECT *
                FROM pages
                WHERE public_state = 'expired'
                  AND grace_until < ?
                """,
                (current_iso,),
            )
            rows = cursor.fetchall()
            for row in rows:
                cursor.execute(
                    """
                    UPDATE pages
                    SET public_state = 'dead',
                        deleted_at = COALESCE(deleted_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (current_iso, current_iso, row["id"]),
                )
            connection.commit()

    def page_exists_for_slug(self, slug: str, *, exclude_page_id: Optional[str] = None) -> bool:
        query = "SELECT 1 FROM pages WHERE slug = ?"
        params: list[Any] = [slug]
        if exclude_page_id:
            query += " AND id != ?"
            params.append(exclude_page_id)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(query, tuple(params))
            return cursor.fetchone() is not None

    def tier2_page_exists_for_btc_address(self, btc_address: str, *, exclude_page_id: Optional[str] = None) -> bool:
        query = "SELECT 1 FROM pages WHERE btc_address = ? AND tier = 'tier2'"
        params: list[Any] = [btc_address]
        if exclude_page_id:
            query += " AND id != ?"
            params.append(exclude_page_id)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(query, tuple(params))
            return cursor.fetchone() is not None

    def slug_is_tombstoned(self, slug: str) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 FROM slug_tombstones WHERE slug = ?", (slug,))
            return cursor.fetchone() is not None

    def verification_code_exists(self, verification_code: str, *, exclude_page_id: Optional[str] = None) -> bool:
        query = "SELECT 1 FROM pages WHERE verification_code = ?"
        params: list[Any] = [verification_code]
        if exclude_page_id:
            query += " AND id != ?"
            params.append(exclude_page_id)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(query, tuple(params))
            return cursor.fetchone() is not None

    def _promo_payload(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["valid_for_badge"] = bool(payload.get("valid_for_badge"))
        payload["valid_for_vanity"] = bool(payload.get("valid_for_vanity"))
        payload["eligible_tiers"] = promo_code_tiers(payload)
        return payload

    def get_promo_code(self, code: str) -> Optional[dict[str, Any]]:
        normalized_code = normalize_promo_code(code)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (normalized_code,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._promo_payload(row)

    def create_promo_code(
        self,
        *,
        code: str,
        valid_for_badge: bool,
        valid_for_vanity: bool,
        max_uses: int,
        expires_at: Optional[dt.datetime] = None,
        now: Optional[dt.datetime] = None,
    ) -> dict[str, Any]:
        normalized_code = normalize_promo_code(code)
        if not valid_for_badge and not valid_for_vanity:
            raise HTTPException(status_code=400, detail="Promo code must be valid for at least one tier.")
        if max_uses < 0:
            raise HTTPException(status_code=400, detail="Promo code max uses cannot be negative.")
        created_at = utc_isoformat(now or self.settings.now_fn())
        expires_at_iso = utc_isoformat(expires_at) if expires_at is not None else None
        with self.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO promo_codes (
                        code, valid_for_badge, valid_for_vanity, max_uses, used_count, expires_at, created_at, revoked_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, NULL)
                    """,
                    (
                        normalized_code,
                        1 if valid_for_badge else 0,
                        1 if valid_for_vanity else 0,
                        max_uses,
                        expires_at_iso,
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Promo code already exists.") from exc
            connection.commit()
        created = self.get_promo_code(normalized_code)
        assert created is not None
        return created

    def list_promo_codes(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM promo_codes ORDER BY created_at DESC, code ASC")
            return [self._promo_payload(row) for row in cursor.fetchall()]

    def revoke_promo_code(self, code: str, *, now: Optional[dt.datetime] = None) -> dict[str, Any]:
        normalized_code = normalize_promo_code(code)
        revoked_at = utc_isoformat(now or self.settings.now_fn())
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE promo_codes
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE code = ?
                """,
                (revoked_at, normalized_code),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Promo code not found.")
            connection.commit()
        revoked = self.get_promo_code(normalized_code)
        assert revoked is not None
        return revoked

    def _proof_row_payload(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["tier"] = normalize_tier(payload["tier"])
        payload["payload"] = json.loads(payload.pop("payload_json") or "{}")
        return payload

    def _proof_record_with_challenge(self, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        challenge_id = enriched.get("challenge_id")
        if challenge_id:
            challenge = self.get_challenge_by_id(str(challenge_id))
            if challenge is not None:
                enriched["challenge"] = challenge
        return enriched

    def _event_row_payload(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        details = json.loads(payload.pop("details_json") or "{}")
        payload["details"] = details
        payload["payload"] = json.loads(payload["payload_json"]) if payload.get("payload_json") else None
        payload["anchor_receipt"] = details.get("anchor_receipt") if isinstance(details, dict) else None
        if payload["anchor_receipt"] is None:
            payload["anchor_receipt"] = build_anchor_receipt(payload.get("payload_hash"), str(payload.get("event_type") or ""))
        return payload

    def _links_locked(self, page: dict[str, Any], *, now: Optional[dt.datetime] = None) -> bool:
        editable_until = parse_timestamp(page.get("links_editable_until"))
        if editable_until is None:
            return True
        current = now or self.settings.now_fn()
        return current > editable_until

    def get_latest_proof_record(self, page_id: str) -> Optional[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM wallet_proof_records
                WHERE page_id = ?
                ORDER BY verified_at DESC, created_at DESC
                LIMIT 1
                """,
                (page_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._proof_record_with_challenge(self._proof_row_payload(row))

    def get_proof_record_by_id(self, proof_record_id: str) -> Optional[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM wallet_proof_records WHERE id = ?", (proof_record_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._proof_record_with_challenge(self._proof_row_payload(row))

    def get_page_events(self, page_id: str, *, public_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT *
            FROM page_events
            WHERE page_id = ?
        """
        params: list[Any] = [page_id]
        if public_only:
            placeholders = ",".join("?" for _ in PUBLIC_EVENT_TYPES)
            query += f" AND event_type IN ({placeholders})"
            params.extend(sorted(PUBLIC_EVENT_TYPES))
        query += " ORDER BY created_at ASC"
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(query, tuple(params))
            events = [self._event_row_payload(row) for row in cursor.fetchall()]
        if self.settings.anchor_mode != "bitcoin-core":
            return events
        refreshed_events: list[dict[str, Any]] = []
        for event in events:
            try:
                refreshed_events.append(self._refresh_anchor_event(event))
            except HTTPException:
                refreshed_events.append(event)
        return refreshed_events

    def _latest_event_by_type(self, page_id: str, event_type: str) -> Optional[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM page_events
                WHERE page_id = ? AND event_type = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (page_id, event_type),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._event_row_payload(row)

    def _verification_state(self, page: dict[str, Any], proof_record: Optional[dict[str, Any]], events: list[dict[str, Any]]) -> str:
        state = page["public_state"]
        if state == "compromised":
            return "compromised"
        if state == "aborted":
            return "aborted"
        if state in {"dead", "deleted", "tombstoned"}:
            return "dead"
        if state == "expired":
            return "expired"
        if not is_paid_tier(page["tier"]) or proof_record is None:
            return "unverified"
        if page["tier"] == "tier3":
            activated_events = [event for event in events if event["event_type"] == "activated"]
            latest_activated = activated_events[-1] if activated_events else None
            if latest_activated and latest_activated.get("anchor_status") == "confirmed":
                return "anchored"
            if latest_activated and latest_activated.get("anchor_status"):
                return "anchor_pending"
        return "verified"

    def _verify_payload(self, page: dict[str, Any]) -> dict[str, Any]:
        proof_record = self.get_latest_proof_record(page["id"])
        events = self.get_page_events(page["id"], public_only=True)
        latest_anchor_event = None
        for event in reversed(events):
            if event.get("anchor_status"):
                latest_anchor_event = event
                break
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "canonical_url": page["canonical_url"],
            "tier": page["tier"],
            "verification_code": page.get("verification_code"),
            "btc_address": page["btc_address"],
            "address_fingerprint": address_fingerprint(page["btc_address"]),
            "current_funding_state": page["public_state"],
            "historical_proof_exists": proof_record is not None,
            "proof_status": self._verification_state(page, proof_record, events),
            "proof_record": proof_record,
            "events": events,
            "latest_anchor_event": latest_anchor_event,
            "disclosure": "This verifies control of the listed Bitcoin wallet, not identity or campaign claims.",
        }

    def _base_page_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["tier"] = normalize_tier(payload["tier"])
        if payload.get("requested_tier"):
            payload["requested_tier"] = normalize_tier(payload["requested_tier"])
        payload.pop("lightning_destination", None)
        payload["links"] = normalize_links_payload(json.loads(payload.pop("links_json") or "[]"))
        payload["status"] = payload["public_state"]
        payload["expires_at"] = payload["active_until"]
        payload["page_ref"] = payload["slug"]
        payload["canonical_url"] = self.page_url(payload["slug"])
        payload["story_photo_url"] = self.story_photo_url(payload["id"]) if payload.get("story_photo_path") else None
        payload["progress_photo_url"] = self.progress_photo_url(payload["id"]) if payload.get("progress_photo_path") else None
        payload["has_badge"] = is_paid_tier(payload["tier"])
        payload["links_locked"] = self._links_locked(payload)
        payload["address_fingerprint"] = address_fingerprint(payload["btc_address"])
        payload["verify_url"] = f"{self.settings.public_base_url}/verify/{quote(payload['slug'], safe='')}"
        payload["updates"] = self.get_updates(payload["id"])
        verify_payload = self._verify_payload(payload)
        payload["proof_status"] = verify_payload["proof_status"]
        payload["proof_record"] = verify_payload["proof_record"]
        payload["event_ledger"] = verify_payload["events"]
        payload["historical_proof_exists"] = verify_payload["historical_proof_exists"]
        payload["latest_anchor_event"] = verify_payload["latest_anchor_event"]
        payload["button_state"] = button_state(payload)
        payload["can_edit_links"] = not payload["links_locked"]
        payload["can_abort"] = payload["public_state"] not in {"aborted", "compromised", "dead", "deleted", "tombstoned"}
        payload["can_post_update"] = payload["tier"] == "tier3" and payload["public_state"] in {"active", "expired"}
        payload["can_upload_progress_photo"] = (
            payload["tier"] == "tier3"
            and payload["public_state"] == "active"
            and not bool(payload.get("progress_photo_path"))
        )
        return payload

    def get_updates(self, page_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT id, body, created_at, updated_at FROM page_updates WHERE page_id = ? ORDER BY created_at DESC",
                (page_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_page_by_id(self, page_id: str) -> Optional[dict[str, Any]]:
        self.sweep_pages()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._base_page_payload(row)

    def get_page_by_ref(self, page_ref: str) -> Optional[dict[str, Any]]:
        self.sweep_pages()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM pages WHERE slug = ?", (page_ref,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._base_page_payload(row)

    def get_page_by_verification_code(self, verification_code: str, *, include_dead: bool = False) -> Optional[dict[str, Any]]:
        self.sweep_pages()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT * FROM pages WHERE LOWER(verification_code) = LOWER(?)",
                (verification_code,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        page = self._base_page_payload(row)
        if not include_dead and page["public_state"] in {"deleted", "tombstoned"}:
            return None
        return page

    def _address_record_payload(self, page: dict[str, Any]) -> dict[str, Any]:
        latest_anchor = page.get("latest_anchor_event") or {}
        return {
            "page_id": page["id"],
            "title": page["title"],
            "page_ref": page["page_ref"],
            "canonical_url": page["canonical_url"],
            "verify_url": page["verify_url"],
            "tier": page["tier"],
            "verification_code": page.get("verification_code"),
            "btc_address": page["btc_address"],
            "address_fingerprint": page.get("address_fingerprint"),
            "current_funding_state": page["public_state"],
            "historical_proof_exists": page.get("historical_proof_exists", False),
            "proof_status": page.get("proof_status"),
            "anchor_status": latest_anchor.get("anchor_status"),
            "anchor_txid": latest_anchor.get("anchor_txid"),
            "anchor_block_height": latest_anchor.get("anchor_block_height"),
            "anchor_confirmed_at": latest_anchor.get("anchor_confirmed_at"),
            "created_at": page.get("created_at"),
            "updated_at": page.get("updated_at"),
        }

    def address_records_payload(self, btc_address: str) -> dict[str, Any]:
        query = normalize_search_query(btc_address)
        if not validate_bitcoin_address(query):
            raise HTTPException(status_code=400, detail="Bitcoin address is invalid.")
        self.sweep_pages()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM pages
                WHERE btc_address = ? AND public_state != 'deleted'
                ORDER BY
                    CASE public_state
                        WHEN 'active' THEN 0
                        WHEN 'expired' THEN 1
                        WHEN 'aborted' THEN 2
                        WHEN 'compromised' THEN 3
                        WHEN 'dead' THEN 4
                        WHEN 'tombstoned' THEN 5
                        ELSE 6
                    END,
                    updated_at DESC,
                    created_at DESC
                """,
                (query,),
            )
            rows = cursor.fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            page = self._base_page_payload(row)
            if page["public_state"] == "deleted":
                continue
            records.append(self._address_record_payload(page))
        return {
            "btc_address": query,
            "address_fingerprint": address_fingerprint(query),
            "record_count": len(records),
            "active_count": sum(1 for record in records if record["current_funding_state"] == "active"),
            "historical_count": sum(1 for record in records if record["current_funding_state"] != "active"),
            "records": records,
            "disclosure": (
                "This confirms whether this Bitcoin address appears in Fund Registry records and what proof/anchor "
                "state those records have. It does not verify campaign truth, identity, or donor safety."
            ),
        }

    def search_page(self, raw_query: str) -> dict[str, Any]:
        query = normalize_search_query(raw_query)
        page = self.get_page_by_ref(query)
        if page is not None and page["public_state"] != "deleted":
            return {"resolved_by": "page_ref", "page": page}

        page = self.get_page_by_verification_code(query)
        if page is not None:
            return {"resolved_by": "verification_code", "page": page}

        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT * FROM pages
                WHERE btc_address = ?
                ORDER BY
                    CASE public_state
                        WHEN 'active' THEN 0
                        WHEN 'expired' THEN 1
                        ELSE 2
                    END,
                    updated_at DESC
                LIMIT 1
                """,
                (query,),
            )
            row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Funding page not found.")
        page = self._base_page_payload(row)
        if page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Funding page not found.")
        return {"resolved_by": "btc_address", "page": page}

    def stats_payload(self) -> dict[str, Any]:
        current_monotonic = time.monotonic()
        if self._stats_cache_payload is not None and current_monotonic < self._stats_cache_expires_at:
            return dict(self._stats_cache_payload)

        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) AS total_pages FROM pages WHERE public_state != 'deleted'")
            row = cursor.fetchone()

        payload = {
            "total_pages": int(row["total_pages"] or 0) if row is not None else 0,
            "generated_at": utc_isoformat(self.settings.now_fn()),
        }
        self._stats_cache_payload = payload
        self._stats_cache_expires_at = current_monotonic + STATS_CACHE_TTL_SECONDS
        return dict(payload)

    def page_url(self, page_ref: str) -> str:
        return f"{self.settings.public_base_url}/fund/{quote(page_ref, safe='')}"

    def badge_url(self, page_ref: str) -> str:
        return f"{self.settings.public_base_url}/badge/{quote(page_ref, safe='')}.svg"

    def story_photo_url(self, page_id: str) -> str:
        return f"{self.settings.public_base_url}/story-photo/{quote(page_id, safe='')}"

    def progress_photo_url(self, page_id: str) -> str:
        return f"{self.settings.public_base_url}/progress-photo/{quote(page_id, safe='')}"

    def _target_page_ref(self, page: dict[str, Any], target_tier: str) -> str:
        target_tier = normalize_tier(target_tier)
        if target_tier == "tier2":
            return page["btc_address"]
        if target_tier == "tier3":
            candidate = page.get("pending_vanity_slug") or page.get("slug")
            if not candidate:
                raise HTTPException(status_code=400, detail="Tier3 slug is missing.")
            return candidate
        return page["slug"]

    def _bitcoin_backend_name(self) -> str:
        if self.settings.bitcoin_backend == "ssh":
            return f"bitcoind-ssh:{self.settings.bitcoin_ssh_host or 'missing-host'}"
        return "bitcoind-local"

    def payment_backend_summary(self) -> dict[str, Any]:
        return {
            "backend": self._bitcoin_backend_name(),
            "transport": self.settings.bitcoin_backend,
            "selection_source": self.settings.bitcoin_backend_source,
            "ssh_host": self.settings.bitcoin_ssh_host if self.settings.bitcoin_backend == "ssh" else None,
            "operator_controlled": True,
            "auto_failover": False,
            "wallet_name": self.settings.payment_wallet_name,
            "confirmation_target": self.settings.payment_confirmation_target,
            "expiry_minutes": self.settings.payment_expiry_minutes,
        }

    def anchor_backend_summary(self) -> dict[str, Any]:
        return {
            "backend": self._bitcoin_backend_name(),
            "transport": self.settings.bitcoin_backend,
            "selection_source": self.settings.bitcoin_backend_source,
            "ssh_host": self.settings.bitcoin_ssh_host if self.settings.bitcoin_backend == "ssh" else None,
            "operator_controlled": True,
            "auto_failover": False,
            "wallet_name": self.settings.bitcoin_wallet_name,
        }

    def _bitcoin_cli_call(self, args: list[str], *, wallet: Optional[str] = None) -> Any:
        if self.settings.bitcoin_cli_fn is not None:
            return self.settings.bitcoin_cli_fn(args, wallet)

        command = [self.settings.bitcoin_cli_path]
        if self.settings.bitcoin_conf_path:
            command.append(f"-conf={self.settings.bitcoin_conf_path}")
        if wallet:
            command.append(f"-rpcwallet={wallet}")
        command.extend(args)

        subprocess_command = list(command)
        if self.settings.bitcoin_backend == "ssh":
            if not self.settings.bitcoin_ssh_host:
                raise HTTPException(
                    status_code=503,
                    detail="Bitcoin SSH backend is selected but BITCOIN_SSH_HOST is missing.",
                )
            subprocess_command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ControlMaster=auto",
                "-o",
                "ControlPersist=60",
                "-o",
                "ControlPath=/tmp/fund-registry-bitcoin-ssh-%C",
                self.settings.bitcoin_ssh_host,
                shlex.join(command),
            ]

        try:
            result = subprocess.run(
                subprocess_command,
                capture_output=True,
                text=True,
                timeout=self.settings.request_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=503, detail="Bitcoin CLI request timed out.") from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"Bitcoin CLI request failed: {exc}") from exc

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            detail = stderr or stdout or f"bitcoin-cli exited with status {result.returncode}."
            raise HTTPException(status_code=502, detail=detail)
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return stdout

    def _ensure_named_wallet_loaded(self, wallet_name: str, *, public_label: str) -> None:
        loaded = self._bitcoin_cli_call(["listwallets"])
        if isinstance(loaded, list) and wallet_name in loaded:
            return

        wallet_dir = self._bitcoin_cli_call(["listwalletdir"])
        wallet_entries = wallet_dir.get("wallets") if isinstance(wallet_dir, dict) else []
        wallet_names = {
            str(entry.get("name", "")).strip()
            for entry in wallet_entries
            if isinstance(entry, dict) and str(entry.get("name", "")).strip()
        }
        if wallet_name not in wallet_names:
            raise HTTPException(status_code=503, detail=f"{public_label} '{wallet_name}' is unavailable.")
        try:
            self._bitcoin_cli_call(["loadwallet", wallet_name, "true"])
        except HTTPException as exc:
            if "already loaded" not in str(exc.detail):
                raise

        loaded = self._bitcoin_cli_call(["listwallets"])
        if not isinstance(loaded, list) or wallet_name not in loaded:
            raise HTTPException(status_code=503, detail=f"{public_label} '{wallet_name}' is not loaded.")

    def _ensure_anchor_wallet_loaded(self) -> None:
        self._ensure_named_wallet_loaded(self.settings.bitcoin_wallet_name, public_label="Bitcoin anchor wallet")

    def _ensure_payment_wallet_loaded(self) -> None:
        self._ensure_named_wallet_loaded(
            self.settings.payment_wallet_name,
            public_label="Fund Registry payment wallet",
        )

    def _wallet_balances_payload(self, balances: Any) -> dict[str, Optional[float]]:
        mine = balances.get("mine") if isinstance(balances, dict) else {}

        def parse_balance(key: str) -> Optional[float]:
            value = mine.get(key) if isinstance(mine, dict) else None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return {
            "trusted": parse_balance("trusted"),
            "untrusted_pending": parse_balance("untrusted_pending"),
            "immature": parse_balance("immature"),
        }

    def payment_preflight_payload(self, *, require_funds: bool = False) -> dict[str, Any]:
        wallet_name = self.settings.payment_wallet_name
        blocking_reasons: list[str] = []
        payload: dict[str, Any] = {
            "checked_at": utc_isoformat(self.settings.now_fn()),
            "current_payment_mode": self.settings.payment_mode,
            "proof_mode": self.settings.proof_mode,
            "requires_confirmed_funds": require_funds,
            "backend": {
                **self.payment_backend_summary(),
                "bitcoin_cli_path": self.settings.bitcoin_cli_path,
                "bitcoin_conf_path": self.settings.bitcoin_conf_path,
            },
            "chain": {
                "reachable": False,
                "chain": None,
                "blocks": None,
                "headers": None,
                "block_lag": None,
                "max_ready_block_lag": MAX_BITCOIN_BACKEND_BLOCK_LAG,
                "initialblockdownload": None,
            },
            "wallet": {
                "present_in_walletdir": False,
                "loaded": False,
                "auto_load_attempted": False,
                "auto_load_succeeded": False,
                "rpc_ready": False,
                "balances_btc": {
                    "trusted": None,
                    "untrusted_pending": None,
                    "immature": None,
                },
            },
            "checks": {
                "bitcoin_cli_reachable": False,
                "chain_ready": False,
                "wallet_present": False,
                "wallet_loaded": False,
                "wallet_rpc_ready": False,
                "wallet_has_confirmed_funds": False,
            },
            "wiring_ready": False,
            "receive_ready": False,
            "ready": False,
            "blocking_reasons": blocking_reasons,
            "next_step": "",
        }

        def add_blocker(reason: str) -> None:
            if reason and reason not in blocking_reasons:
                blocking_reasons.append(reason)

        try:
            blockchain = self._bitcoin_cli_call(["getblockchaininfo"])
        except HTTPException as exc:
            add_blocker(str(exc.detail))
            payload["next_step"] = "Resolve the blocking reasons before enabling Fund Registry paid checkout."
            return payload
        if not isinstance(blockchain, dict):
            add_blocker("Fund Registry payment backend returned an invalid chain status.")
            payload["next_step"] = "Resolve the blocking reasons before enabling Fund Registry paid checkout."
            return payload

        try:
            block_height = int(blockchain.get("blocks"))
        except (TypeError, ValueError):
            block_height = None
        try:
            header_height = int(blockchain.get("headers"))
        except (TypeError, ValueError):
            header_height = None
        block_lag = None
        if block_height is not None and header_height is not None:
            block_lag = max(0, header_height - block_height)
        initial_block_download = bool(blockchain.get("initialblockdownload"))
        payload["chain"].update(
            {
                "reachable": True,
                "chain": blockchain.get("chain"),
                "blocks": block_height,
                "headers": header_height,
                "block_lag": block_lag,
                "initialblockdownload": initial_block_download,
            }
        )
        payload["checks"]["bitcoin_cli_reachable"] = True
        if initial_block_download:
            add_blocker("Fund Registry payment backend is still syncing.")
        elif block_lag is None:
            add_blocker("Fund Registry payment backend did not return comparable block heights.")
        elif block_lag > MAX_BITCOIN_BACKEND_BLOCK_LAG:
            add_blocker(f"Fund Registry payment backend is {block_lag} blocks behind headers.")
        else:
            payload["checks"]["chain_ready"] = True

        try:
            loaded_wallets = self._bitcoin_cli_call(["listwallets"])
        except HTTPException as exc:
            add_blocker(str(exc.detail))
            payload["next_step"] = "Resolve the blocking reasons before enabling Fund Registry paid checkout."
            return payload
        loaded_wallet_names = [str(entry).strip() for entry in loaded_wallets] if isinstance(loaded_wallets, list) else []

        try:
            wallet_dir = self._bitcoin_cli_call(["listwalletdir"])
        except HTTPException as exc:
            add_blocker(str(exc.detail))
            payload["next_step"] = "Resolve the blocking reasons before enabling Fund Registry paid checkout."
            return payload
        wallet_entries = wallet_dir.get("wallets") if isinstance(wallet_dir, dict) else []
        available_wallets = {
            str(entry.get("name", "")).strip()
            for entry in wallet_entries
            if isinstance(entry, dict) and str(entry.get("name", "")).strip()
        }
        payload["wallet"]["present_in_walletdir"] = wallet_name in available_wallets
        payload["checks"]["wallet_present"] = payload["wallet"]["present_in_walletdir"]
        if not payload["checks"]["wallet_present"]:
            add_blocker(f"Fund Registry payment wallet '{wallet_name}' is unavailable.")
        if payload["checks"]["wallet_present"] and wallet_name not in loaded_wallet_names:
            payload["wallet"]["auto_load_attempted"] = True
            try:
                self._bitcoin_cli_call(["loadwallet", wallet_name, "true"])
                payload["wallet"]["auto_load_succeeded"] = True
            except HTTPException as exc:
                detail = str(exc.detail)
                if "already loaded" not in detail.lower():
                    add_blocker(f"Fund Registry payment wallet '{wallet_name}' could not be loaded.")
            try:
                loaded_wallets = self._bitcoin_cli_call(["listwallets"])
                loaded_wallet_names = [str(entry).strip() for entry in loaded_wallets] if isinstance(loaded_wallets, list) else []
            except HTTPException as exc:
                add_blocker(str(exc.detail))

        payload["wallet"]["loaded"] = wallet_name in loaded_wallet_names
        payload["checks"]["wallet_loaded"] = payload["wallet"]["loaded"]
        if payload["checks"]["wallet_present"] and not payload["checks"]["wallet_loaded"]:
            add_blocker(f"Fund Registry payment wallet '{wallet_name}' is not loaded.")

        if payload["checks"]["wallet_loaded"]:
            try:
                balances = self._bitcoin_cli_call(["getbalances"], wallet=wallet_name)
                payload["wallet"]["rpc_ready"] = True
                payload["checks"]["wallet_rpc_ready"] = True
                parsed_balances = self._wallet_balances_payload(balances)
                payload["wallet"]["balances_btc"] = parsed_balances
                trusted_balance = parsed_balances.get("trusted")
                if trusted_balance is not None and trusted_balance > 0:
                    payload["checks"]["wallet_has_confirmed_funds"] = True
                elif require_funds:
                    add_blocker("Fund Registry payment wallet has no confirmed funds.")
            except HTTPException as exc:
                add_blocker(str(exc.detail))

        payload["wiring_ready"] = all(
            [
                payload["checks"]["bitcoin_cli_reachable"],
                payload["checks"]["chain_ready"],
                payload["checks"]["wallet_present"],
                payload["checks"]["wallet_loaded"],
                payload["checks"]["wallet_rpc_ready"],
            ]
        )
        payload["receive_ready"] = payload["wiring_ready"]
        payload["ready"] = payload["receive_ready"] if not require_funds else (
            payload["receive_ready"] and payload["checks"]["wallet_has_confirmed_funds"]
        )

        if payload["ready"]:
            if self.settings.payment_mode == "bitcoin-core":
                payload["next_step"] = "Payment backend is ready for live Fund Registry Bitcoin checkout."
            else:
                payload["next_step"] = "Preflight is green. Set FUND_REGISTRY_PAYMENT_MODE=bitcoin-core to enable live checkout."
        elif require_funds and payload["receive_ready"]:
            payload["next_step"] = "Wiring is ready, but confirmed wallet funds are still required."
        else:
            payload["next_step"] = "Resolve the blocking reasons before enabling Fund Registry paid checkout."
        return payload

    def anchor_preflight_payload(self, *, require_funds: bool = True) -> dict[str, Any]:
        wallet_name = self.settings.bitcoin_wallet_name
        blocking_reasons: list[str] = []
        payload: dict[str, Any] = {
            "checked_at": utc_isoformat(self.settings.now_fn()),
            "current_anchor_mode": self.settings.anchor_mode,
            "proof_mode": self.settings.proof_mode,
            "requires_confirmed_funds": require_funds,
            "backend": {
                "backend": self._bitcoin_backend_name(),
                "transport": self.settings.bitcoin_backend,
                "selection_source": self.settings.bitcoin_backend_source,
                "ssh_host": self.settings.bitcoin_ssh_host if self.settings.bitcoin_backend == "ssh" else None,
                "operator_controlled": True,
                "auto_failover": False,
                "bitcoin_cli_path": self.settings.bitcoin_cli_path,
                "bitcoin_conf_path": self.settings.bitcoin_conf_path,
                "wallet_name": wallet_name,
            },
            "chain": {
                "reachable": False,
                "chain": None,
                "blocks": None,
                "headers": None,
                "block_lag": None,
                "max_ready_block_lag": MAX_BITCOIN_BACKEND_BLOCK_LAG,
                "initialblockdownload": None,
            },
            "wallet": {
                "present_in_walletdir": False,
                "loaded": False,
                "auto_load_attempted": False,
                "auto_load_succeeded": False,
                "rpc_ready": False,
                "balances_btc": {
                    "trusted": None,
                    "untrusted_pending": None,
                    "immature": None,
                },
            },
            "checks": {
                "bitcoin_cli_reachable": False,
                "chain_ready": False,
                "wallet_present": False,
                "wallet_loaded": False,
                "wallet_rpc_ready": False,
                "wallet_has_confirmed_funds": False,
            },
            "wiring_ready": False,
            "broadcast_ready": False,
            "ready": False,
            "blocking_reasons": blocking_reasons,
            "next_step": "",
        }

        def add_blocker(reason: str) -> None:
            if reason and reason not in blocking_reasons:
                blocking_reasons.append(reason)

        try:
            blockchain = self._bitcoin_cli_call(["getblockchaininfo"])
        except HTTPException as exc:
            add_blocker(str(exc.detail))
            payload["next_step"] = "Resolve the blocking reasons before enabling live tier3 Bitcoin anchors."
            return payload
        if not isinstance(blockchain, dict):
            add_blocker("Bitcoin anchor backend returned an invalid chain status.")
            payload["next_step"] = "Resolve the blocking reasons before enabling live tier3 Bitcoin anchors."
            return payload

        try:
            block_height = int(blockchain.get("blocks"))
        except (TypeError, ValueError):
            block_height = None
        try:
            header_height = int(blockchain.get("headers"))
        except (TypeError, ValueError):
            header_height = None
        block_lag = None
        if block_height is not None and header_height is not None:
            block_lag = max(0, header_height - block_height)
        initial_block_download = bool(blockchain.get("initialblockdownload"))
        payload["chain"].update(
            {
                "reachable": True,
                "chain": blockchain.get("chain"),
                "blocks": block_height,
                "headers": header_height,
                "block_lag": block_lag,
                "initialblockdownload": initial_block_download,
            }
        )
        payload["checks"]["bitcoin_cli_reachable"] = True
        if initial_block_download:
            add_blocker("Bitcoin anchor backend is still syncing.")
        elif block_lag is None:
            add_blocker("Bitcoin anchor backend did not return comparable block heights.")
        elif block_lag > MAX_BITCOIN_BACKEND_BLOCK_LAG:
            add_blocker(f"Bitcoin anchor backend is {block_lag} blocks behind headers.")
        else:
            payload["checks"]["chain_ready"] = True

        try:
            loaded_wallets = self._bitcoin_cli_call(["listwallets"])
        except HTTPException as exc:
            add_blocker(str(exc.detail))
            payload["next_step"] = "Resolve the blocking reasons before enabling live tier3 Bitcoin anchors."
            return payload
        loaded_wallet_names = [str(entry).strip() for entry in loaded_wallets] if isinstance(loaded_wallets, list) else []

        try:
            wallet_dir = self._bitcoin_cli_call(["listwalletdir"])
        except HTTPException as exc:
            add_blocker(str(exc.detail))
            payload["next_step"] = "Resolve the blocking reasons before enabling live tier3 Bitcoin anchors."
            return payload
        wallet_entries = wallet_dir.get("wallets") if isinstance(wallet_dir, dict) else []
        available_wallets = {
            str(entry.get("name", "")).strip()
            for entry in wallet_entries
            if isinstance(entry, dict) and str(entry.get("name", "")).strip()
        }
        payload["wallet"]["present_in_walletdir"] = wallet_name in available_wallets
        payload["checks"]["wallet_present"] = payload["wallet"]["present_in_walletdir"]
        if not payload["checks"]["wallet_present"]:
            add_blocker(f"Bitcoin anchor wallet '{wallet_name}' is unavailable.")
        if payload["checks"]["wallet_present"] and wallet_name not in loaded_wallet_names:
            payload["wallet"]["auto_load_attempted"] = True
            try:
                self._bitcoin_cli_call(["loadwallet", wallet_name, "true"])
                payload["wallet"]["auto_load_succeeded"] = True
            except HTTPException as exc:
                detail = str(exc.detail)
                if "already loaded" not in detail.lower():
                    add_blocker(f"Bitcoin anchor wallet '{wallet_name}' could not be loaded.")
            try:
                loaded_wallets = self._bitcoin_cli_call(["listwallets"])
                loaded_wallet_names = [str(entry).strip() for entry in loaded_wallets] if isinstance(loaded_wallets, list) else []
            except HTTPException as exc:
                add_blocker(str(exc.detail))

        payload["wallet"]["loaded"] = wallet_name in loaded_wallet_names
        payload["checks"]["wallet_loaded"] = payload["wallet"]["loaded"]
        if payload["checks"]["wallet_present"] and not payload["checks"]["wallet_loaded"]:
            add_blocker(f"Bitcoin anchor wallet '{wallet_name}' is not loaded.")

        if payload["checks"]["wallet_loaded"]:
            try:
                balances = self._bitcoin_cli_call(["getbalances"], wallet=wallet_name)
                payload["wallet"]["rpc_ready"] = True
                payload["checks"]["wallet_rpc_ready"] = True
                parsed_balances = self._wallet_balances_payload(balances)
                payload["wallet"]["balances_btc"] = parsed_balances
                trusted_balance = parsed_balances.get("trusted")
                if trusted_balance is not None and trusted_balance > 0:
                    payload["checks"]["wallet_has_confirmed_funds"] = True
                elif require_funds:
                    add_blocker("Bitcoin anchor wallet has no confirmed funds.")
            except HTTPException as exc:
                add_blocker(str(exc.detail))

        payload["wiring_ready"] = all(
            [
                payload["checks"]["bitcoin_cli_reachable"],
                payload["checks"]["chain_ready"],
                payload["checks"]["wallet_present"],
                payload["checks"]["wallet_loaded"],
                payload["checks"]["wallet_rpc_ready"],
            ]
        )
        payload["broadcast_ready"] = payload["wiring_ready"] and payload["checks"]["wallet_has_confirmed_funds"]
        payload["ready"] = payload["broadcast_ready"] if require_funds else payload["wiring_ready"]

        if payload["ready"]:
            if self.settings.anchor_mode == "bitcoin-core":
                payload["next_step"] = "Anchor backend is ready for a live tier3 smoke."
            else:
                payload["next_step"] = "Preflight is green. Set FUND_REGISTRY_ANCHOR_MODE=bitcoin-core and run a live tier3 smoke."
        elif not require_funds and payload["wiring_ready"]:
            payload["next_step"] = "Wiring is ready, but confirmed wallet funds are still required before live tier3 anchoring."
        else:
            payload["next_step"] = "Resolve the blocking reasons before enabling live tier3 Bitcoin anchors."
        return payload

    def _anchor_broadcast_public_detail(self, reason: str) -> str:
        lowered = reason.lower()
        if "no confirmed funds" in lowered or "insufficient funds" in lowered:
            summary = "Bitcoin anchor wallet is not funded yet."
        elif "still syncing" in lowered or "behind headers" in lowered:
            summary = "Bitcoin anchor backend is still syncing."
        elif "wallet" in lowered and ("not loaded" in lowered or "unavailable" in lowered or "could not be loaded" in lowered):
            summary = "Bitcoin anchor wallet is not ready yet."
        elif "timed out" in lowered:
            summary = "Bitcoin anchor backend did not respond in time."
        else:
            summary = "Bitcoin anchor backend is unavailable."
        return f"{summary} Your tier3 activation has not been completed yet. Please retry after the anchor backend is ready."

    def _assert_anchor_backend_ready(self, *, require_funds: bool) -> None:
        preflight = self.anchor_preflight_payload(require_funds=require_funds)
        if preflight["ready"]:
            return
        reasons = preflight.get("blocking_reasons") or ["Bitcoin anchor backend is unavailable."]
        raise HTTPException(status_code=503, detail=str(reasons[0]))

    def _assert_payment_backend_ready(self, *, require_funds: bool = False) -> None:
        preflight = self.payment_preflight_payload(require_funds=require_funds)
        if preflight["ready"]:
            return
        reasons = preflight.get("blocking_reasons") or ["Fund Registry payment backend is unavailable."]
        raise HTTPException(status_code=503, detail=str(reasons[0]))

    def _anchor_fields_from_wallet_transaction(self, tx: Any, *, created_at: str, txid: str) -> dict[str, Any]:
        payload: dict[str, Any] = tx if isinstance(tx, dict) else {}
        confirmations = payload.get("confirmations")
        try:
            confirmation_count = int(confirmations)
        except (TypeError, ValueError):
            confirmation_count = 0
        confirmed = confirmation_count > 0
        return {
            "anchor_mode": "bitcoin-core",
            "anchor_status": "confirmed" if confirmed else "broadcast",
            "anchor_txid": txid,
            "anchor_block_height": payload.get("blockheight") if confirmed else None,
            "anchor_block_hash": payload.get("blockhash") if confirmed else None,
            "anchor_broadcast_at": unix_timestamp_to_iso(payload.get("timereceived"))
            or unix_timestamp_to_iso(payload.get("time"))
            or created_at,
            "anchor_confirmed_at": unix_timestamp_to_iso(payload.get("blocktime")) if confirmed else None,
        }

    def _bitcoin_core_anchor_fields(
        self,
        *,
        page: dict[str, Any],
        payload_hash: str,
        event_type: str,
        created_at: str,
        anchor_receipt: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        op_return_hex = anchor_receipt.get("op_return_hex") if isinstance(anchor_receipt, dict) else payload_hash
        try:
            self._assert_anchor_backend_ready(require_funds=True)
            raw_transaction = self._bitcoin_cli_call(
                ["createrawtransaction", "[]", json.dumps({"data": op_return_hex}, separators=(",", ":"))],
                wallet=self.settings.bitcoin_wallet_name,
            )
            if not isinstance(raw_transaction, str) or not raw_transaction:
                raise HTTPException(status_code=502, detail="Bitcoin anchor transaction creation returned no hex.")
            funded = self._bitcoin_cli_call(
                ["fundrawtransaction", raw_transaction],
                wallet=self.settings.bitcoin_wallet_name,
            )
            funded_hex = funded.get("hex") if isinstance(funded, dict) else None
            if not isinstance(funded_hex, str) or not funded_hex:
                raise HTTPException(status_code=502, detail="Bitcoin anchor transaction funding returned no hex.")
            signed = self._bitcoin_cli_call(
                ["signrawtransactionwithwallet", funded_hex],
                wallet=self.settings.bitcoin_wallet_name,
            )
            signed_hex = signed.get("hex") if isinstance(signed, dict) else None
            if not isinstance(signed_hex, str) or not signed_hex or not signed.get("complete"):
                raise HTTPException(status_code=502, detail="Bitcoin anchor transaction signing failed.")
            txid = self._bitcoin_cli_call(
                ["sendrawtransaction", signed_hex],
                wallet=self.settings.bitcoin_wallet_name,
            )
            if not isinstance(txid, str) or not txid:
                raise HTTPException(status_code=502, detail="Bitcoin anchor transaction broadcast returned no txid.")
            tx = self._bitcoin_cli_call(
                ["gettransaction", txid, "true"],
                wallet=self.settings.bitcoin_wallet_name,
            )
            anchor_fields = self._anchor_fields_from_wallet_transaction(tx, created_at=created_at, txid=txid)
            security_log(
                "anchor_broadcast_success",
                page_id=page["id"],
                page_ref=page["page_ref"],
                anchor_event_type=event_type,
                anchor_txid=txid,
                anchor_status=anchor_fields["anchor_status"],
            )
            return {
                **anchor_fields,
                "anchor_receipt": anchor_receipt,
            }
        except HTTPException as exc:
            security_log(
                "anchor_broadcast_failure",
                page_id=page["id"],
                page_ref=page["page_ref"],
                anchor_event_type=event_type,
                reason=str(exc.detail),
            )
            raise HTTPException(
                status_code=503,
                detail=self._anchor_broadcast_public_detail(str(exc.detail)),
            ) from exc

    def _refresh_anchor_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if event.get("anchor_mode") != "bitcoin-core" or event.get("anchor_status") != "broadcast":
            return event
        txid = event.get("anchor_txid")
        if not txid:
            return event
        tx = self._bitcoin_cli_call(
            ["gettransaction", str(txid), "true"],
            wallet=self.settings.bitcoin_wallet_name,
        )
        refreshed = self._anchor_fields_from_wallet_transaction(tx, created_at=event["created_at"], txid=str(txid))
        if refreshed["anchor_status"] == event.get("anchor_status") and refreshed["anchor_confirmed_at"] == event.get("anchor_confirmed_at"):
            return event
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE page_events
                SET anchor_status = ?,
                    anchor_block_height = ?,
                    anchor_block_hash = ?,
                    anchor_broadcast_at = ?,
                    anchor_confirmed_at = ?
                WHERE id = ?
                """,
                (
                    refreshed["anchor_status"],
                    refreshed["anchor_block_height"],
                    refreshed["anchor_block_hash"],
                    refreshed["anchor_broadcast_at"],
                    refreshed["anchor_confirmed_at"],
                    event["id"],
                ),
            )
            connection.commit()
        return {
            **event,
            **refreshed,
        }

    def _verify_bitcoin_message(self, address: str, signature: str, message: str) -> bool:
        """Verify a Bitcoin signed message using bitcoin-cli verifymessage."""
        try:
            result = self._bitcoin_cli_call(["verifymessage", address, signature, message])
            return str(result).strip().lower() == "true"
        except HTTPException:
            return False

    def _verify_bip322_simple(self, address: str, signature: str, message: str) -> bool:
        """Verify a BIP-322 simple signature locally."""
        try:
            from bip322 import VerificationError, verify_simple_encoded
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="Fund Registry BIP-322 verification dependency is unavailable.",
            ) from None

        try:
            verify_simple_encoded(address, message, signature)
            return True
        except VerificationError:
            return False

    def _proof_payload_for_page(self, page: dict[str, Any], *, target_tier: str) -> tuple[dict[str, Any], str, str]:
        payload = {
            "version": PROOF_PAYLOAD_VERSION,
            "domain": canonicalize_base_url(self.settings.public_base_url).removeprefix("https://").removeprefix("http://"),
            "page_id": page["id"],
            "page_ref": self._target_page_ref(page, target_tier),
            "tier": normalize_tier(target_tier),
            "btc_address": page["btc_address"],
            "verification_code": page["verification_code"],
            "created_at": page["created_at"],
            "statement": PROOF_STATEMENT,
        }
        payload_json = canonical_json_dumps(payload)
        payload_hash = sha256_hex(payload_json)
        return payload, payload_json, payload_hash

    def _issue_wallet_proof_challenge(
        self,
        cursor: sqlite3.Cursor,
        *,
        page: dict[str, Any],
        purpose: str,
        proof_method: str,
        created_at: str,
        expires_at: str,
        payment_intent_id: Optional[str] = None,
        target_tier: Optional[str] = None,
    ) -> dict[str, Any]:
        challenge_id = str(uuid.uuid4())
        normalized_tier = normalize_tier(target_tier or page["tier"])
        proof_payload, payload_json, payload_hash = self._proof_payload_for_page(page, target_tier=normalized_tier)
        challenge_payload = {
            "version": PROOF_CHALLENGE_VERSION,
            "domain": canonicalize_base_url(self.settings.public_base_url).removeprefix("https://").removeprefix("http://"),
            "challenge_id": challenge_id,
            "page_id": page["id"],
            "page_ref": proof_payload["page_ref"],
            "tier": normalized_tier,
            "purpose": purpose,
            "btc_address": page["btc_address"],
            "nonce": secrets.token_hex(16),
            "issued_at": created_at,
            "expires_at": expires_at,
            "canonical_proof_payload_hash": payload_hash,
            "statement": PROOF_CHALLENGE_STATEMENT,
        }
        challenge_text = canonical_json_dumps(challenge_payload)
        cursor.execute(
            """
            INSERT INTO wallet_proof_challenges (
                id, page_id, payment_intent_id, purpose, status, challenge_text, proof_method,
                created_at, expires_at, payload_json, payload_hash
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                challenge_id,
                page["id"],
                payment_intent_id,
                purpose,
                challenge_text,
                proof_method,
                created_at,
                expires_at,
                payload_json,
                payload_hash,
            ),
        )
        return self._challenge_response_payload(
            {
                "id": challenge_id,
                "page_id": page["id"],
                "payment_intent_id": payment_intent_id,
                "purpose": purpose,
                "status": "pending",
                "challenge_text": challenge_text,
                "proof_method": proof_method,
                "created_at": created_at,
                "expires_at": expires_at,
                "verified_at": None,
                "payload_json": payload_json,
                "payload_hash": payload_hash,
            }
        )

    def _event_payload_for_page(self, page: dict[str, Any], event_type: str, *, created_at: str) -> tuple[dict[str, Any], str, str]:
        if event_type == "aborted":
            statement = ABORT_STATEMENT
        elif event_type == "compromised":
            statement = COMPROMISED_STATEMENT
        else:
            raise HTTPException(status_code=400, detail="Unsupported lifecycle event.")
        payload = {
            "version": EVENT_PAYLOAD_VERSION,
            "domain": canonicalize_base_url(self.settings.public_base_url).removeprefix("https://").removeprefix("http://"),
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "btc_address": page["btc_address"],
            "event": event_type,
            "created_at": created_at,
            "statement": statement,
        }
        payload_json = canonical_json_dumps(payload)
        payload_hash = sha256_hex(payload_json)
        return payload, payload_json, payload_hash

    def _mock_anchor_fields(
        self,
        payload_hash: str,
        *,
        event_type: str,
        created_at: str,
        anchor_receipt: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        txid = hashlib.sha256(f"fundregistry:{event_type}:{payload_hash}".encode("utf-8")).hexdigest()
        block_height = 900_000 + int(txid[:4], 16) % 10_000
        return {
            "anchor_mode": "mock",
            "anchor_status": "confirmed",
            "anchor_txid": txid,
            "anchor_block_height": block_height,
            "anchor_block_hash": hashlib.sha256(f"block:{txid}".encode("utf-8")).hexdigest(),
            "anchor_broadcast_at": created_at,
            "anchor_confirmed_at": created_at,
            "anchor_receipt": anchor_receipt,
        }

    def _anchor_fields_for_event(self, *, page: dict[str, Any], payload_hash: Optional[str], event_type: str, created_at: str) -> dict[str, Any]:
        if not payload_hash or page["tier"] != "tier3":
            return {
                "anchor_mode": None,
                "anchor_status": None,
                "anchor_txid": None,
                "anchor_block_height": None,
                "anchor_block_hash": None,
                "anchor_broadcast_at": None,
                "anchor_confirmed_at": None,
                "anchor_receipt": None,
            }
        anchor_receipt = build_anchor_receipt(payload_hash, event_type)
        if self.settings.anchor_mode == "mock":
            return self._mock_anchor_fields(
                payload_hash,
                event_type=event_type,
                created_at=created_at,
                anchor_receipt=anchor_receipt,
            )
        if self.settings.anchor_mode == "bitcoin-core":
            return self._bitcoin_core_anchor_fields(
                page=page,
                payload_hash=payload_hash,
                event_type=event_type,
                created_at=created_at,
                anchor_receipt=anchor_receipt,
            )
        return {
            "anchor_mode": self.settings.anchor_mode,
            "anchor_status": "disabled",
            "anchor_txid": None,
            "anchor_block_height": None,
            "anchor_block_hash": None,
            "anchor_broadcast_at": None,
            "anchor_confirmed_at": None,
            "anchor_receipt": anchor_receipt,
        }

    def _record_page_event(
        self,
        cursor: sqlite3.Cursor,
        *,
        page: dict[str, Any],
        event_type: str,
        created_at: str,
        details: Optional[dict[str, Any]] = None,
        payload_json: Optional[str] = None,
        payload_hash: Optional[str] = None,
        proof_record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        anchor_fields = self._anchor_fields_for_event(
            page=page,
            payload_hash=payload_hash,
            event_type=event_type,
            created_at=created_at,
        )
        event_details = dict(details or {})
        anchor_receipt = anchor_fields.get("anchor_receipt")
        if anchor_receipt is not None:
            event_details["anchor_receipt"] = anchor_receipt
        event_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO page_events (
                id, page_id, event_type, details_json, payload_json, payload_hash, proof_record_id,
                anchor_mode, anchor_status, anchor_txid, anchor_block_height, anchor_block_hash,
                anchor_broadcast_at, anchor_confirmed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                page["id"],
                event_type,
                json.dumps(event_details, sort_keys=True),
                payload_json,
                payload_hash,
                proof_record_id,
                anchor_fields["anchor_mode"],
                anchor_fields["anchor_status"],
                anchor_fields["anchor_txid"],
                anchor_fields["anchor_block_height"],
                anchor_fields["anchor_block_hash"],
                anchor_fields["anchor_broadcast_at"],
                anchor_fields["anchor_confirmed_at"],
                created_at,
            ),
        )
        return {
            "id": event_id,
            "page_id": page["id"],
            "event_type": event_type,
            "details": event_details,
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "proof_record_id": proof_record_id,
            **anchor_fields,
            "created_at": created_at,
        }

    def create_page(self, request: CreatePageRequest) -> dict[str, Any]:
        now = self.settings.now_fn()
        created_at = utc_isoformat(now)
        title = request.title.strip()
        description = request.description.strip()
        btc_address = request.btc_address.strip()
        requested_tier = normalize_tier(request.tier)
        if is_paid_tier(requested_tier):
            self._ensure_wallet_proof_address_supported(btc_address)
            self.ensure_paid_activation_available(requested_tier)
        goal_btc = normalize_goal_btc(request.goal_btc)
        if not title:
            raise HTTPException(status_code=400, detail="Campaign title is required.")
        if not description:
            raise HTTPException(status_code=400, detail="Description is required.")
        if not validate_bitcoin_address(btc_address):
            raise HTTPException(status_code=400, detail="Bitcoin address is invalid.")

        links = normalize_links_payload([link.model_dump() for link in request.links], reject_invalid=True)

        slug = make_random_slug()
        while self.page_exists_for_slug(slug):
            slug = make_random_slug()

        pending_vanity_slug = None
        if requested_tier == "tier3":
            pending_vanity_slug = normalize_slug(request.vanity_slug or "")
            if self.slug_is_tombstoned(pending_vanity_slug) or self.page_exists_for_slug(pending_vanity_slug):
                raise HTTPException(status_code=409, detail="Tier3 slug is unavailable.")

        page_id = str(uuid.uuid4())
        key_id = make_key_id()
        secret = make_secret()
        verification_code = self._generate_unique_verification_code()
        tier = "free"
        links_editable_until = utc_isoformat(now + dt.timedelta(hours=LINK_EDIT_GRACE_HOURS))
        active_until = utc_isoformat(now + dt.timedelta(days=FREE_DURATION_DAYS))
        grace_until = utc_isoformat(now + dt.timedelta(days=FREE_DURATION_DAYS + FREE_GRACE_DAYS))
        payment_intent: Optional[dict[str, Any]] = None

        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO pages (
                    id, slug, slug_kind, verification_code, tier, requested_tier, pending_vanity_slug, public_state,
                    title, description, story_photo_path, story_photo_media_type, progress_photo_path, progress_photo_media_type,
                    btc_address, links_json, links_editable_until, goal_btc,
                    amount_raised_btc, contribution_count, created_at, updated_at, active_until,
                    grace_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    slug,
                    "random",
                    verification_code,
                    tier,
                    requested_tier,
                    pending_vanity_slug,
                    "active",
                    title,
                    description,
                    None,
                    None,
                    None,
                    None,
                    btc_address,
                    json.dumps(links),
                    links_editable_until,
                    goal_btc,
                    None,
                    0,
                    created_at,
                    created_at,
                    active_until,
                    grace_until,
                ),
            )
            cursor.execute(
                """
                INSERT INTO campaign_keys (id, page_id, key_id, key_version, secret_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), page_id, key_id, 1, secret_hash(secret), created_at),
            )
            connection.commit()
        self._invalidate_stats_cache()

        if requested_tier in {"tier2", "tier3"}:
            payment_intent = self.create_payment_intent(
                page_id=page_id,
                purpose="activate",
                target_tier=requested_tier,
                optional_vanity_slug=pending_vanity_slug,
            )

        page = self.get_page_by_id(page_id)
        assert page is not None
        return {
            "page": page,
            "campaign_key": {
                "version": 1,
                "registry": canonicalize_base_url(self.settings.public_base_url).removeprefix("https://").removeprefix("http://"),
                "page_id": page_id,
                "key_id": key_id,
                "key_version": 1,
                "secret": secret,
            },
            "payment_intent": payment_intent,
        }

    def authenticate_campaign_key(self, payload: CampaignKeyPayload) -> dict[str, Any]:
        registry_host = canonicalize_base_url(self.settings.public_base_url).removeprefix("https://").removeprefix("http://")
        if payload.registry != registry_host:
            raise CampaignKeyAuthFailure(
                status_code=400,
                detail="Campaign Key belongs to a different registry.",
                reason_code="registry_mismatch",
            )
        provided_secret_hash = secret_hash(payload.secret)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT page_id, key_version, secret_hash
                FROM campaign_keys
                WHERE page_id = ? AND key_id = ? AND key_version = ? AND revoked_at IS NULL
                """,
                (payload.page_id, payload.key_id, payload.key_version),
            )
            active_row = cursor.fetchone()
            if active_row is None or not secrets.compare_digest(str(active_row["secret_hash"]), provided_secret_hash):
                cursor.execute(
                    """
                    SELECT page_id, key_version, secret_hash, revoked_at, revocation_reason
                    FROM campaign_keys
                    WHERE page_id = ? AND key_id = ? AND key_version = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (payload.page_id, payload.key_id, payload.key_version),
                )
                any_row = cursor.fetchone()
            else:
                any_row = active_row
        if active_row is None or not secrets.compare_digest(str(active_row["secret_hash"]), provided_secret_hash):
            reason_code = "unknown_key"
            if any_row is not None:
                if secrets.compare_digest(str(any_row["secret_hash"]), provided_secret_hash):
                    reason_code = "revoked" if any_row["revoked_at"] else "inactive"
                else:
                    reason_code = "wrong_secret"
            raise CampaignKeyAuthFailure(
                status_code=403,
                detail="Campaign Key is invalid.",
                reason_code=reason_code,
            )
        page = self.get_page_by_id(payload.page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        return page

    def create_payment_intent(
        self,
        *,
        page_id: str,
        purpose: str,
        target_tier: str,
        optional_vanity_slug: Optional[str] = None,
    ) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        target_tier = normalize_tier(target_tier)
        if target_tier == "free":
            raise HTTPException(status_code=400, detail="Free pages do not require payment.")
        self._ensure_wallet_proof_address_supported(page["btc_address"])
        self.ensure_paid_activation_available(target_tier)
        if purpose == "upgrade":
            if page["tier"] == "free" and target_tier != "tier2":
                raise HTTPException(status_code=400, detail="Free pages must upgrade to tier2 before tier3.")
            if page["tier"] == "tier2" and target_tier != "tier3":
                raise HTTPException(status_code=400, detail="Tier2 pages may only upgrade to tier3.")
            if page["tier"] == "tier3":
                raise HTTPException(status_code=400, detail="Tier3 pages cannot upgrade further.")
        if purpose == "renew" and page["tier"] != target_tier:
            raise HTTPException(status_code=400, detail="Renewal tier must match the current page tier.")
        now = self.settings.now_fn()
        with self.connection() as connection:
            cursor = connection.cursor()
            if target_tier == "tier3":
                self._prepare_pending_vanity_slug(cursor, page, optional_vanity_slug, now=now)
            connection.commit()

        payment_id = str(uuid.uuid4())
        amount_usd_cents, amount_sats = amount_quote_for_tier(self.settings, target_tier)
        amount_btc = sats_to_btc_string(amount_sats)
        payment_method = "mock"
        payment_reference = f"frpay_{secrets.token_hex(6)}"
        invoice = f"mock-bitcoin-payment:{payment_reference}"
        payment_address = None
        payment_uri = None
        confirmation_target = self.settings.payment_confirmation_target
        created_at = utc_isoformat(now)
        expires_at = utc_isoformat(now + dt.timedelta(minutes=self.settings.payment_expiry_minutes))

        if self.settings.payment_mode == "bitcoin-core":
            self._assert_payment_backend_ready(require_funds=False)
            payment_method = "btc_onchain"
            label = f"fundregistry-{purpose}-{payment_id[:12]}"
            payment_address = str(
                self._bitcoin_cli_call(["getnewaddress", label, "bech32"], wallet=self.settings.payment_wallet_name)
            ).strip()
            if not payment_address:
                raise HTTPException(status_code=502, detail="Bitcoin Core did not return a Fund Registry checkout address.")
            payment_uri = build_bip21_uri(
                payment_address,
                amount_btc,
                "Fund Registry tier payment",
                f"{purpose} {target_tier} for {page['title'][:48]}",
            )
            invoice = payment_uri

        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO payment_intents (
                    id, page_id, purpose, target_tier, status, payment_method, amount_usd_cents, amount_sats,
                    amount_btc, payment_reference, invoice, payment_address, payment_uri, confirmation_target,
                    confirmations, txids_json, unconfirmed_received_sats, confirmed_received_sats, created_at,
                    expires_at, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '[]', 0, 0, ?, ?, NULL)
                """,
                (
                    payment_id,
                    page_id,
                    purpose,
                    target_tier,
                    "pending",
                    payment_method,
                    amount_usd_cents,
                    amount_sats,
                    amount_btc,
                    payment_reference,
                    invoice,
                    payment_address,
                    payment_uri,
                    confirmation_target,
                    created_at,
                    expires_at,
                ),
            )
            connection.commit()
        payment = self.get_payment_intent(payment_id)
        assert payment is not None
        return payment

    def _prepare_pending_vanity_slug(
        self,
        cursor: sqlite3.Cursor,
        page: dict[str, Any],
        optional_vanity_slug: Optional[str],
        *,
        now: dt.datetime,
    ) -> str:
        candidate = optional_vanity_slug or page.get("pending_vanity_slug")
        if not candidate and page.get("slug_kind") == "vanity":
            candidate = page["slug"]
        vanity_slug = normalize_slug(candidate or "")
        if self.slug_is_tombstoned(vanity_slug):
            raise HTTPException(status_code=409, detail="Vanity slug has already been retired.")
        if self.page_exists_for_slug(vanity_slug, exclude_page_id=page["id"]) and page["slug"] != vanity_slug:
            raise HTTPException(status_code=409, detail="Vanity slug is unavailable.")
        cursor.execute(
            "UPDATE pages SET pending_vanity_slug = ?, updated_at = ? WHERE id = ?",
            (vanity_slug, utc_isoformat(now), page["id"]),
        )
        page["pending_vanity_slug"] = vanity_slug
        return vanity_slug

    def _promo_validation_error(self, code: str, reason: str, *, detail: Optional[str] = None) -> dict[str, Any]:
        return {
            "valid": False,
            "code": code,
            "reason": reason,
            "detail": detail or "Invalid or unavailable promo code.",
            "eligible_tiers": [],
            "requested_tier_valid": None,
            "requested_tier": None,
            "requires_vanity_slug": False,
            "remaining_uses": None,
        }

    def _load_promo_code_for_use(self, code: str, now: dt.datetime) -> dict[str, Any]:
        promo = self.get_promo_code(code)
        if promo is None:
            raise PromoCodeError("invalid")
        if promo.get("revoked_at"):
            raise PromoCodeError("revoked", public_detail="Promo code is no longer available.", status_code=409)
        expires_at = parse_timestamp(promo.get("expires_at"))
        if expires_at is not None and expires_at < now:
            raise PromoCodeError("expired", public_detail="Promo code has expired.", status_code=409)
        max_uses = int(promo.get("max_uses") or 0)
        used_count = int(promo.get("used_count") or 0)
        if max_uses > 0 and used_count >= max_uses:
            raise PromoCodeError("exhausted", public_detail="Promo code is no longer available.", status_code=409)
        return promo

    def _eligible_promo_targets(self, page: dict[str, Any], promo: dict[str, Any]) -> list[str]:
        if page["public_state"] in {"deleted", "tombstoned"}:
            return []
        eligible: list[str] = []
        if page["tier"] == "free":
            if promo.get("valid_for_badge"):
                eligible.append("tier2")
            if promo.get("valid_for_vanity"):
                eligible.append("tier3")
            return eligible
        if page["tier"] == "tier2":
            if promo.get("valid_for_badge"):
                eligible.append("tier2")
            if promo.get("valid_for_vanity"):
                eligible.append("tier3")
            return eligible
        if page["tier"] == "tier3" and promo.get("valid_for_vanity"):
            eligible.append("tier3")
        return eligible

    def _promo_purpose_for_target(self, page: dict[str, Any], target_tier: str) -> str:
        if target_tier == "tier2":
            return "activate" if page["tier"] == "free" else "renew"
        if target_tier == "tier3":
            if page["tier"] == "free":
                return "activate"
            return "upgrade" if page["tier"] == "tier2" else "renew"
        raise HTTPException(status_code=400, detail="Unsupported promo target tier.")

    def validate_promo_code(
        self,
        *,
        page_id: str,
        code: str,
        target_tier: Optional[str] = None,
        vanity_slug: Optional[str] = None,
    ) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        normalized_code = normalize_promo_code(code)
        normalized_target_tier = normalize_tier(target_tier) if target_tier is not None else None
        now = self.settings.now_fn()
        try:
            promo = self._load_promo_code_for_use(normalized_code, now)
        except PromoCodeError as exc:
            return self._promo_validation_error(normalized_code, exc.reason, detail=exc.public_detail)

        eligible_tiers = self._eligible_promo_targets(page, promo)
        if self.settings.proof_mode in {"bitcoin-message", "mixed"} and any(is_paid_tier(tier) for tier in eligible_tiers):
            method, detail = configured_wallet_proof_method(page["btc_address"], self.settings.proof_mode)
            if method is None:
                return self._promo_validation_error(
                    normalized_code,
                    "unsupported_address",
                    detail=detail or mixed_proof_supported_address_detail(),
                )
        if not eligible_tiers:
            return self._promo_validation_error(
                normalized_code,
                "ineligible",
                detail="Promo code does not apply to this page.",
            )

        requires_vanity_slug = False
        if normalized_target_tier == "tier3":
            candidate = vanity_slug or page.get("pending_vanity_slug")
            if not candidate and page.get("slug_kind") == "vanity":
                candidate = page["slug"]
            requires_vanity_slug = not bool(candidate)

        requested_tier_valid = None
        if normalized_target_tier is not None:
            requested_tier_valid = normalized_target_tier in eligible_tiers and not requires_vanity_slug
        max_uses = int(promo.get("max_uses") or 0)
        used_count = int(promo.get("used_count") or 0)
        remaining_uses = None if max_uses <= 0 else max(0, max_uses - used_count)
        return {
            "valid": requested_tier_valid if normalized_target_tier is not None else True,
            "code": normalized_code,
            "current_tier": page["tier"],
            "eligible_tiers": eligible_tiers,
            "requested_tier": normalized_target_tier,
            "requested_tier_valid": requested_tier_valid,
            "requires_vanity_slug": requires_vanity_slug,
            "expires_at": promo.get("expires_at"),
            "remaining_uses": remaining_uses,
            "used_count": used_count,
            "max_uses": max_uses,
        }

    def apply_promo_code(
        self,
        *,
        page_id: str,
        code: str,
        target_tier: str,
        vanity_slug: Optional[str] = None,
    ) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if page["public_state"] in {"deleted", "tombstoned"}:
            raise HTTPException(status_code=409, detail="Promo codes cannot reactivate retired pages.")

        normalized_code = normalize_promo_code(code)
        normalized_target_tier = normalize_tier(target_tier)
        now = self.settings.now_fn()
        try:
            promo = self._load_promo_code_for_use(normalized_code, now)
        except PromoCodeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.public_detail) from exc
        self._ensure_wallet_proof_address_supported(page["btc_address"])
        eligible_tiers = self._eligible_promo_targets(page, promo)
        if normalized_target_tier not in eligible_tiers:
            raise HTTPException(status_code=400, detail="Promo code does not apply to the requested tier.")
        purpose = self._promo_purpose_for_target(page, normalized_target_tier)

        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
            row = cursor.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Page not found.")
            page_row = dict(row)
            if normalized_target_tier == "tier3":
                self._prepare_pending_vanity_slug(cursor, page_row, vanity_slug, now=now)
            cursor.execute(
                """
                UPDATE promo_codes
                SET used_count = used_count + 1
                WHERE code = ?
                  AND revoked_at IS NULL
                  AND (max_uses <= 0 OR used_count < max_uses)
                """,
                (normalized_code,),
            )
            if cursor.rowcount != 1:
                raise HTTPException(status_code=409, detail="Promo code is no longer available.")
            self._apply_activation(
                cursor,
                {"page_id": page_id, "target_tier": normalized_target_tier, "purpose": purpose},
                now,
                page_row.get("wallet_proof_method") if page_row.get("wallet_proof_verified_at") else None,
                page_row.get("wallet_proof_verified_at"),
            )
            cursor.execute(
                """
                UPDATE payment_intents
                SET status = 'expired',
                    last_checked_at = ?,
                    expires_at = CASE
                        WHEN expires_at IS NULL OR expires_at > ? THEN ?
                        ELSE expires_at
                    END
                WHERE page_id = ?
                  AND status IN ('pending', 'confirming', 'paid_pending_proof')
                """,
                (utc_isoformat(now), utc_isoformat(now), utc_isoformat(now), page_id),
            )
            connection.commit()

        updated_promo = self.get_promo_code(normalized_code)
        updated_page = self.get_page_by_id(page_id)
        assert updated_page is not None
        return {
            "activated": True,
            "code": normalized_code,
            "target_tier": normalized_target_tier,
            "promo_code": updated_promo,
            "page": updated_page,
        }

    def ensure_paid_activation_available(self, target_tier: Optional[str] = None) -> None:
        if self.settings.allow_dev_actions:
            return
        if self.settings.payments_paused:
            raise HTTPException(
                status_code=503,
                detail=PAYMENTS_PAUSED_MESSAGE,
            )
        if self.settings.payment_mode == "disabled" and not self.settings.allow_dev_actions:
            raise HTTPException(
                status_code=503,
                detail="Paid tier activation is not enabled yet (payment processing is offline).",
            )
        if self.settings.proof_mode == "disabled" and not self.settings.allow_dev_actions:
            raise HTTPException(
                status_code=503,
                detail="Wallet proof verification is not enabled yet.",
            )
        if self.settings.payment_mode == "bitcoin-core":
            self._assert_payment_backend_ready(require_funds=False)
        if normalize_tier(target_tier or "free") == "tier3":
            if self.settings.anchor_mode == "disabled":
                raise HTTPException(status_code=503, detail="tier3 Bitcoin anchoring is not enabled yet.")
            if self.settings.anchor_mode == "bitcoin-core":
                self._assert_anchor_backend_ready(require_funds=True)

    def _payment_row_payload(
        self,
        row: sqlite3.Row | dict[str, Any],
        *,
        now: Optional[dt.datetime] = None,
        payment_backend_error: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = dict(row)
        current = now or self.settings.now_fn()
        payload["amount_btc"] = payload.get("amount_btc") or sats_to_btc_string(int(payload.get("amount_sats") or 0))
        payload["confirmation_target"] = int(payload.get("confirmation_target") or self.settings.payment_confirmation_target)
        payload["confirmations"] = int(payload.get("confirmations") or 0)
        payload["unconfirmed_received_sats"] = int(payload.get("unconfirmed_received_sats") or 0)
        payload["confirmed_received_sats"] = int(payload.get("confirmed_received_sats") or 0)
        txids_json = payload.pop("txids_json", "[]")
        try:
            txids = json.loads(txids_json or "[]")
        except json.JSONDecodeError:
            txids = []
        payload["txids"] = txids if isinstance(txids, list) else []
        payment_uri = str(payload.get("payment_uri") or "").strip()
        if not payment_uri:
            invoice = str(payload.get("invoice") or "").strip()
            if invoice.startswith("bitcoin:"):
                payment_uri = invoice
        payload["payment_uri"] = payment_uri or None
        if payload["payment_method"] == "btc_onchain" and payload["payment_uri"]:
            payload["payment_request"] = payload["payment_uri"]
            payload["qr_value"] = payload["payment_uri"]
            payload["qr_image_uri"] = render_qr_png_data_uri(payload["payment_uri"])
        received_sats = max(payload["unconfirmed_received_sats"], payload["confirmed_received_sats"])
        payload["received_sats"] = received_sats
        payload["underpaid_sats"] = max(0, int(payload["amount_sats"]) - received_sats)
        payload["overpaid_sats"] = max(0, received_sats - int(payload["amount_sats"]))
        payload["payment_status"] = payment_poll_status(payload, now=current)
        payload["late_payment_detected"] = payload["payment_status"] == "expired" and received_sats > 0
        if payment_backend_error:
            payload["payment_backend_error"] = payment_backend_error
        if payload["payment_method"] == "btc_onchain" and self.settings.payments_paused:
            payload["payment_ui_paused"] = True
            payload["payment_ui_message"] = PAYMENTS_PAUSED_MESSAGE
            payload["payment_address"] = None
            payload["payment_uri"] = None
            payload["payment_request"] = None
            payload.pop("qr_value", None)
            payload.pop("qr_image_uri", None)
        elif payload["payment_method"] == "btc_onchain" and self.settings.payment_details_redacted:
            payload["payment_ui_redacted"] = True
            payload["payment_ui_message"] = PAYMENT_DETAILS_REDACTED_MESSAGE
            payload["payment_address"] = None
            payload["payment_uri"] = None
            payload["payment_request"] = None
            payload.pop("qr_value", None)
            payload.pop("qr_image_uri", None)
        return payload

    def _payment_received_sats_from_wallet_transaction(self, tx: Any, *, payment_address: str) -> int:
        payload: dict[str, Any] = tx if isinstance(tx, dict) else {}
        details = payload.get("details")
        matched_sats = 0
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                if str(detail.get("address") or "").strip() != payment_address:
                    continue
                if str(detail.get("category") or "").strip().lower() != "receive":
                    continue
                matched_sats += max(0, btc_amount_to_sats(detail.get("amount", 0)))
        if matched_sats > 0:
            return matched_sats
        return max(0, btc_amount_to_sats(payload.get("amount", 0)))

    def _wallet_transaction_related_txids(self, tx: Any) -> set[str]:
        payload: dict[str, Any] = tx if isinstance(tx, dict) else {}
        related: set[str] = set()
        conflicts = payload.get("walletconflicts")
        if isinstance(conflicts, list):
            for txid in conflicts:
                candidate = str(txid or "").strip()
                if candidate:
                    related.add(candidate)
        for key in ("replaced_by_txid", "replaces_txid"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                related.add(candidate)
        return related

    def _current_payment_candidates_from_txids(
        self,
        *,
        payment_address: str,
        payment_wallet_name: str,
        txids: list[str],
    ) -> dict[str, Any]:
        pending = [str(txid).strip() for txid in txids if str(txid or "").strip()]
        if not pending:
            return {
                "unconfirmed_received_sats": 0,
                "confirmations": 0,
                "txids": [],
            }

        entries: dict[str, dict[str, Any]] = {}
        while pending:
            txid = pending.pop()
            if txid in entries:
                continue
            try:
                tx = self._bitcoin_cli_call(["gettransaction", txid], wallet=payment_wallet_name)
            except HTTPException as exc:
                detail = str(exc.detail).lower()
                if "non-wallet transaction" in detail or "not found" in detail:
                    continue
                raise
            if not isinstance(tx, dict):
                continue
            related = self._wallet_transaction_related_txids(tx)
            entries[txid] = {
                "txid": txid,
                "received_sats": self._payment_received_sats_from_wallet_transaction(tx, payment_address=payment_address),
                "confirmations": int(tx.get("confirmations") or 0),
                "timereceived": int(tx.get("timereceived") or tx.get("time") or 0),
                "replaced_by_txid": str(tx.get("replaced_by_txid") or "").strip() or None,
                "replaces_txid": str(tx.get("replaces_txid") or "").strip() or None,
                "related_txids": related,
            }
            for related_txid in related:
                if related_txid not in entries:
                    pending.append(related_txid)

        if not entries:
            return {
                "unconfirmed_received_sats": 0,
                "confirmations": 0,
                "txids": [],
            }

        chosen_entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root_txid in list(entries.keys()):
            if root_txid in seen:
                continue
            component_ids: set[str] = set()
            stack = [root_txid]
            while stack:
                current_txid = stack.pop()
                if current_txid in seen or current_txid not in entries:
                    continue
                seen.add(current_txid)
                component_ids.add(current_txid)
                for related_txid in entries[current_txid]["related_txids"]:
                    if related_txid in entries and related_txid not in seen:
                        stack.append(related_txid)
            component = [entries[txid] for txid in component_ids]
            superseded_txids = {
                entry["txid"]
                for entry in component
                if entry["replaced_by_txid"] in component_ids
            }
            superseded_txids.update(
                entry["replaces_txid"]
                for entry in component
                if entry["replaces_txid"] in component_ids
            )
            candidates = [entry for entry in component if entry["txid"] not in superseded_txids]
            if not candidates:
                candidates = component
            confirmed_candidates = [entry for entry in candidates if entry["confirmations"] > 0]
            pool = confirmed_candidates or candidates
            # Conflicting wallet transactions in an RBF chain are mutually exclusive
            # payment candidates, so only the best surviving tx in each conflict set counts.
            chosen_entries.append(
                max(
                    pool,
                    key=lambda entry: (
                        entry["confirmations"] > 0,
                        max(entry["confirmations"], 0),
                        entry["timereceived"],
                        entry["txid"],
                    ),
                )
            )

        active_entries = [entry for entry in chosen_entries if entry["received_sats"] > 0]
        active_entries.sort(key=lambda entry: (entry["timereceived"], entry["txid"]))
        confirmations = min((max(entry["confirmations"], 0) for entry in active_entries), default=0)
        return {
            "unconfirmed_received_sats": sum(entry["received_sats"] for entry in active_entries),
            "confirmations": confirmations,
            "txids": [entry["txid"] for entry in active_entries],
        }

    def _refresh_bitcoin_core_payment_intent(
        self,
        connection: sqlite3.Connection,
        cursor: sqlite3.Cursor,
        payment: dict[str, Any],
        *,
        now: dt.datetime,
    ) -> sqlite3.Row:
        payment_address = str(payment.get("payment_address") or "").strip()
        if not payment_address:
            raise HTTPException(status_code=502, detail="Bitcoin checkout is missing a payment address.")
        self._assert_payment_backend_ready(require_funds=False)
        confirmation_target = int(payment.get("confirmation_target") or self.settings.payment_confirmation_target)
        payment_wallet_name = self.settings.payment_wallet_name
        received_rows = self._bitcoin_cli_call(
            ["listreceivedbyaddress", "0", "false", "true", payment_address],
            wallet=payment_wallet_name,
        )
        row = received_rows[0] if isinstance(received_rows, list) and received_rows else None
        unconfirmed_received_sats = btc_amount_to_sats(row.get("amount", 0)) if isinstance(row, dict) else 0
        confirmations = int(row.get("confirmations") or 0) if isinstance(row, dict) else 0
        txids = row.get("txids") if isinstance(row, dict) and isinstance(row.get("txids"), list) else []
        if txids:
            candidate_summary = self._current_payment_candidates_from_txids(
                payment_address=payment_address,
                payment_wallet_name=payment_wallet_name,
                txids=txids,
            )
            unconfirmed_received_sats = int(candidate_summary["unconfirmed_received_sats"])
            confirmations = int(candidate_summary["confirmations"])
            txids = candidate_summary["txids"]
        confirmed_received_btc = self._bitcoin_cli_call(
            ["getreceivedbyaddress", payment_address, str(confirmation_target)],
            wallet=payment_wallet_name,
        )
        confirmed_received_sats = btc_amount_to_sats(confirmed_received_btc)
        expires_at = parse_timestamp(payment.get("expires_at"))
        expired = expires_at is not None and expires_at <= now
        current_status = str(payment.get("status") or "").strip().lower()
        amount_sats = int(payment.get("amount_sats") or 0)
        next_status = current_status

        if current_status == "paid":
            next_status = "paid"
        elif current_status == "expired":
            next_status = "expired"
        elif confirmed_received_sats >= amount_sats:
            next_status = "expired" if expired else "paid_pending_proof"
        elif unconfirmed_received_sats >= amount_sats:
            next_status = "expired" if expired else "confirming"
        elif expired:
            next_status = "expired"
        else:
            next_status = "pending"

        paid_at = payment.get("paid_at")
        if next_status == "paid_pending_proof" and not paid_at:
            paid_at = utc_isoformat(now)

        cursor.execute(
            """
            UPDATE payment_intents
            SET status = ?,
                confirmations = ?,
                txids_json = ?,
                unconfirmed_received_sats = ?,
                confirmed_received_sats = ?,
                paid_at = COALESCE(?, paid_at),
                last_checked_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                confirmations,
                json.dumps(txids),
                unconfirmed_received_sats,
                confirmed_received_sats,
                paid_at,
                utc_isoformat(now),
                payment["id"],
            ),
        )

        if next_status == "paid_pending_proof" and current_status != "paid_pending_proof":
            page = self.get_page_by_id(payment["page_id"])
            if page is None:
                raise HTTPException(status_code=404, detail="Page not found.")
            challenge = self.get_challenge_for_payment(payment["id"])
            challenge_valid = False
            if challenge is not None:
                challenge_expires_at = parse_timestamp(challenge.get("expires_at"))
                challenge_valid = (
                    challenge.get("status") == "pending"
                    and challenge_expires_at is not None
                    and challenge_expires_at > now
                    and self._challenge_is_one_time_payload(challenge)
                )
                if not challenge_valid:
                    cursor.execute(
                        "UPDATE wallet_proof_challenges SET status = 'superseded' WHERE id = ? AND status = 'pending'",
                        (challenge["id"],),
                    )
            if not challenge_valid:
                created_at = utc_isoformat(now)
                expires_at = utc_isoformat(now + dt.timedelta(minutes=15))
                self._issue_wallet_proof_challenge(
                    cursor,
                    page=page,
                    purpose=payment["purpose"],
                    proof_method=self._current_proof_method(page["btc_address"]),
                    created_at=created_at,
                    expires_at=expires_at,
                    payment_intent_id=payment["id"],
                    target_tier=payment["target_tier"],
                )

        connection.commit()
        cursor.execute("SELECT * FROM payment_intents WHERE id = ?", (payment["id"],))
        refreshed_row = cursor.fetchone()
        if refreshed_row is None:
            raise HTTPException(status_code=404, detail="Payment intent not found.")
        return refreshed_row

    def get_payment_intent(self, payment_id: str) -> Optional[dict[str, Any]]:
        now = self.settings.now_fn()
        payment_backend_error: Optional[str] = None
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM payment_intents WHERE id = ?", (payment_id,))
            row = cursor.fetchone()
            if row is not None:
                payload = dict(row)
                if payload.get("payment_method") == "btc_onchain" and self.settings.payments_paused:
                    payment_backend_error = PAYMENTS_PAUSED_MESSAGE
                    if payment_poll_status(payload, now=now) == "expired" and payload["status"] == "pending":
                        cursor.execute(
                            "UPDATE payment_intents SET status = 'expired' WHERE id = ? AND status = 'pending'",
                            (payment_id,),
                        )
                        connection.commit()
                        cursor.execute("SELECT * FROM payment_intents WHERE id = ?", (payment_id,))
                        row = cursor.fetchone()
                elif payload.get("payment_method") == "btc_onchain":
                    try:
                        row = self._refresh_bitcoin_core_payment_intent(connection, cursor, payload, now=now)
                    except HTTPException as exc:
                        payment_backend_error = str(exc.detail)
                        cursor.execute("SELECT * FROM payment_intents WHERE id = ?", (payment_id,))
                        row = cursor.fetchone()
                elif payment_poll_status(payload, now=now) == "expired" and payload["status"] == "pending":
                    cursor.execute(
                        "UPDATE payment_intents SET status = 'expired' WHERE id = ? AND status = 'pending'",
                        (payment_id,),
                    )
                    connection.commit()
                    cursor.execute("SELECT * FROM payment_intents WHERE id = ?", (payment_id,))
                    row = cursor.fetchone()
        if row is None:
            return None
        payload = self._payment_row_payload(row, now=now, payment_backend_error=payment_backend_error)
        if payload["status"] == "paid_pending_proof":
            payload["challenge"] = self.get_challenge_for_payment(payment_id)
        return payload

    def latest_manage_payment_intent(self, page: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT id
                FROM payment_intents
                WHERE page_id = ? AND status IN ('pending', 'confirming', 'paid_pending_proof')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (page["id"],),
            )
            row = cursor.fetchone()
            if row is None and page.get("requested_tier"):
                cursor.execute(
                    """
                    SELECT id
                    FROM payment_intents
                    WHERE page_id = ? AND target_tier = ? AND status = 'expired'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (page["id"], page["requested_tier"]),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self.get_payment_intent(str(row["id"]))

    def manage_page_payload(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        page["payment_intent"] = self.latest_manage_payment_intent(page)
        page["payments_paused"] = self.settings.payments_paused
        page["payment_details_redacted"] = self.settings.payment_details_redacted
        return page

    def get_challenge_for_payment(self, payment_id: str) -> Optional[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT id, page_id, payment_intent_id, purpose, status, challenge_text, proof_method,
                       created_at, expires_at, verified_at, payload_json, payload_hash
                FROM wallet_proof_challenges
                WHERE payment_intent_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (payment_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._challenge_response_payload(row)

    def get_challenge_by_id(self, challenge_id: str) -> Optional[dict[str, Any]]:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM wallet_proof_challenges WHERE id = ?", (challenge_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._challenge_response_payload(row)

    def prepare_proof(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        self._ensure_wallet_proof_address_supported(page["btc_address"])
        now = self.settings.now_fn()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM wallet_proof_challenges
                WHERE page_id = ? AND status = 'pending' AND payment_intent_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (page_id,),
            )
            row = cursor.fetchone()
            challenge: Optional[dict[str, Any]] = None
            if row is not None:
                challenge = self._challenge_response_payload(row)
                expires_at = parse_timestamp(challenge.get("expires_at"))
                if expires_at is None or expires_at <= now or not self._challenge_is_one_time_payload(challenge):
                    cursor.execute(
                        "UPDATE wallet_proof_challenges SET status = 'superseded' WHERE id = ? AND status = 'pending'",
                        (challenge["id"],),
                    )
                    challenge = None
            if challenge is None:
                cursor.execute(
                    """
                    SELECT *
                    FROM payment_intents
                    WHERE page_id = ? AND status = 'paid_pending_proof'
                    ORDER BY paid_at DESC, created_at DESC
                    LIMIT 1
                    """,
                    (page_id,),
                )
                payment_row = cursor.fetchone()
                if payment_row is None:
                    if is_paid_tier(page["tier"]) and not page.get("wallet_proof_verified_at"):
                        created_at = utc_isoformat(now)
                        expires_at_str = utc_isoformat(now + dt.timedelta(minutes=15))
                        challenge = self._issue_wallet_proof_challenge(
                            cursor,
                            page=page,
                            purpose="wallet_proof",
                            proof_method=self._current_proof_method(page["btc_address"]),
                            created_at=created_at,
                            expires_at=expires_at_str,
                            payment_intent_id=None,
                            target_tier=page["tier"],
                        )
                        connection.commit()
                        payload = challenge.get("payload")
                        if payload is None:
                            raise HTTPException(status_code=409, detail="Wallet-proof payload is missing.")
                        return {
                            "page_id": page["id"],
                            "page_ref": page["page_ref"],
                            "tier": payload["tier"],
                            "challenge": {
                                "id": challenge["id"],
                                "status": challenge["status"],
                                "proof_method": challenge["proof_method"],
                                "created_at": challenge["created_at"],
                                "expires_at": challenge["expires_at"],
                            },
                            "payment_intent": None,
                            "payload": payload,
                            "payload_json": challenge.get("challenge_text", ""),
                            "payload_hash": challenge.get("payload_hash", ""),
                            "instructions": proof_instructions_text(challenge["proof_method"]),
                        }
                    raise HTTPException(status_code=404, detail="No pending wallet-proof challenge was found for this page. Upgrade to a paid tier first.")
                payment = dict(payment_row)
                created_at = utc_isoformat(now)
                expires_at = utc_isoformat(now + dt.timedelta(minutes=15))
                challenge = self._issue_wallet_proof_challenge(
                    cursor,
                    page=page,
                    purpose=payment["purpose"],
                    proof_method=self._current_proof_method(page["btc_address"]),
                    created_at=created_at,
                    expires_at=expires_at,
                    payment_intent_id=payment["id"],
                    target_tier=payment["target_tier"],
                )
                connection.commit()
            payment = self.get_payment_intent(challenge["payment_intent_id"])
        payload = challenge.get("payload")
        if payload is None:
            raise HTTPException(status_code=409, detail="Wallet-proof payload is missing for this challenge.")
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "tier": payload["tier"],
            "challenge": {
                "id": challenge["id"],
                "status": challenge["status"],
                "proof_method": challenge["proof_method"],
                "created_at": challenge["created_at"],
                "expires_at": challenge["expires_at"],
                "text": challenge["challenge_text"],
                "payload": challenge["challenge_payload"],
                "hash": challenge["challenge_hash"],
            },
            "challenge_text": challenge["challenge_text"],
            "challenge_payload": challenge["challenge_payload"],
            "challenge_hash": challenge["challenge_hash"],
            "payment_intent": payment,
            "payload": payload,
            "payload_json": challenge["payload_json"],
            "payload_hash": challenge["payload_hash"],
            "instructions": proof_instructions_text(challenge["proof_method"]),
        }

    def prepare_manage_verification(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if page["public_state"] in {"deleted", "tombstoned"}:
            raise HTTPException(status_code=409, detail="Wallet verification is unavailable for this page state.")
        now = self.settings.now_fn()

        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM wallet_proof_challenges
                WHERE page_id = ? AND status = 'pending' AND payment_intent_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (page_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                challenge = self._challenge_response_payload(row)
                expires_at = parse_timestamp(challenge.get("expires_at"))
                if expires_at is not None and expires_at > now and self._challenge_is_one_time_payload(challenge):
                    payment = self.get_payment_intent(challenge["payment_intent_id"])
                    payload = challenge.get("payload")
                    return {
                        "page_id": page["id"],
                        "page_ref": page["page_ref"],
                        "tier": payload.get("tier") if isinstance(payload, dict) else page["tier"],
                        "challenge_id": challenge["id"],
                        "challenge_text": challenge["challenge_text"],
                        "challenge_payload": challenge["challenge_payload"],
                        "challenge_hash": challenge["challenge_hash"],
                        "proof_method": challenge["proof_method"],
                        "created_at": challenge["created_at"],
                        "expires_at": challenge["expires_at"],
                        "payload": payload,
                        "payload_json": challenge.get("payload_json"),
                        "payload_hash": challenge.get("payload_hash"),
                        "payment_intent": payment,
                        "instructions": proof_instructions_text(challenge["proof_method"]),
                    }
                cursor.execute(
                    "UPDATE wallet_proof_challenges SET status = 'superseded' WHERE id = ? AND status = 'pending'",
                    (challenge["id"],),
                )
                payment = self.get_payment_intent(challenge["payment_intent_id"])
                if payment is not None:
                    created_at = utc_isoformat(now)
                    expires_at = utc_isoformat(now + dt.timedelta(minutes=15))
                    refreshed = self._issue_wallet_proof_challenge(
                        cursor,
                        page=page,
                        purpose=payment["purpose"],
                        proof_method=self._current_proof_method(page["btc_address"]),
                        created_at=created_at,
                        expires_at=expires_at,
                        payment_intent_id=payment["id"],
                        target_tier=payment["target_tier"],
                    )
                    connection.commit()
                    payload = refreshed.get("payload")
                    return {
                        "page_id": page["id"],
                        "page_ref": page["page_ref"],
                        "tier": payload.get("tier") if isinstance(payload, dict) else page["tier"],
                        "challenge_id": refreshed["id"],
                        "challenge_text": refreshed["challenge_text"],
                        "challenge_payload": refreshed["challenge_payload"],
                        "challenge_hash": refreshed["challenge_hash"],
                        "proof_method": refreshed["proof_method"],
                        "created_at": refreshed["created_at"],
                        "expires_at": refreshed["expires_at"],
                        "payload": payload,
                        "payload_json": refreshed.get("payload_json"),
                        "payload_hash": refreshed.get("payload_hash"),
                        "payment_intent": payment,
                        "instructions": proof_instructions_text(refreshed["proof_method"]),
                    }

        if not is_paid_tier(page["tier"]):
            raise HTTPException(
                status_code=409,
                detail="Wallet verification becomes available after payment completes or once the page is on a paid tier.",
            )

        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM wallet_proof_challenges
                WHERE page_id = ? AND status = 'pending' AND purpose = 'verify' AND payment_intent_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (page_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                challenge = self._challenge_response_payload(row)
                expires_at = parse_timestamp(challenge.get("expires_at"))
                if expires_at is not None and expires_at > now and self._challenge_is_one_time_payload(challenge):
                    payload = challenge.get("payload")
                    return {
                        "page_id": page["id"],
                        "page_ref": page["page_ref"],
                        "tier": payload.get("tier") if isinstance(payload, dict) else page["tier"],
                        "challenge_id": challenge["id"],
                        "challenge_text": challenge["challenge_text"],
                        "challenge_payload": challenge["challenge_payload"],
                        "challenge_hash": challenge["challenge_hash"],
                        "proof_method": challenge["proof_method"],
                        "created_at": challenge["created_at"],
                        "expires_at": challenge["expires_at"],
                        "payload": payload,
                        "payload_json": challenge.get("payload_json"),
                        "payload_hash": challenge.get("payload_hash"),
                        "payment_intent": None,
                        "instructions": proof_instructions_text(challenge["proof_method"]),
                    }
                cursor.execute(
                    "UPDATE wallet_proof_challenges SET status = 'superseded' WHERE id = ? AND status = 'pending'",
                    (challenge["id"],),
                )

            created_at = utc_isoformat(now)
            expires_at = utc_isoformat(now + dt.timedelta(minutes=15))
            challenge = self._issue_wallet_proof_challenge(
                cursor,
                page=page,
                purpose="verify",
                proof_method=self._current_proof_method(page["btc_address"]),
                created_at=created_at,
                expires_at=expires_at,
                payment_intent_id=None,
                target_tier=page["tier"],
            )
            connection.commit()
        payload = challenge.get("payload")
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "tier": page["tier"],
            "challenge_id": challenge["id"],
            "challenge_text": challenge["challenge_text"],
            "challenge_payload": challenge["challenge_payload"],
            "challenge_hash": challenge["challenge_hash"],
            "proof_method": challenge["proof_method"],
            "created_at": challenge["created_at"],
            "expires_at": challenge["expires_at"],
            "payload": payload,
            "payload_json": challenge["payload_json"],
            "payload_hash": challenge["payload_hash"],
            "payment_intent": None,
            "instructions": proof_instructions_text(challenge["proof_method"]),
        }

    def mark_payment_paid(self, payment_id: str) -> dict[str, Any]:
        payment = self.get_payment_intent(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment intent not found.")
        if payment["status"] != "pending":
            return payment
        page = self.get_page_by_id(payment["page_id"])
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        now = self.settings.now_fn()
        paid_at = utc_isoformat(now)
        expires_at = utc_isoformat(now + dt.timedelta(minutes=15))
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE payment_intents
                SET status = 'paid_pending_proof',
                    paid_at = ?
                WHERE id = ?
                """,
                (paid_at, payment_id),
            )
            self._issue_wallet_proof_challenge(
                cursor,
                page=page,
                purpose=payment["purpose"],
                proof_method=self._current_proof_method(page["btc_address"]),
                created_at=paid_at,
                expires_at=expires_at,
                payment_intent_id=payment_id,
                target_tier=payment["target_tier"],
            )
            connection.commit()
        payload = self.get_payment_intent(payment_id)
        assert payload is not None
        return payload

    def _create_proof_record(
        self,
        cursor: sqlite3.Cursor,
        *,
        page_id: str,
        challenge: dict[str, Any],
        payment: Optional[dict[str, Any]],
        signature: str,
        signature_method: str,
        verified_at: str,
    ) -> dict[str, Any]:
        payload_json = challenge.get("payload_json")
        payload_hash = challenge.get("payload_hash")
        if not payload_json or not payload_hash:
            raise HTTPException(status_code=409, detail="Wallet-proof payload is missing for this challenge.")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=409, detail="Wallet-proof payload is invalid.") from exc
        target_tier = payment["target_tier"] if payment is not None else payload.get("tier") or "free"
        proof_record_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO wallet_proof_records (
                id, page_id, challenge_id, payment_intent_id, tier, purpose, payload_json, payload_hash,
                signature, signature_method, status, created_at, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified', ?, ?)
            """,
            (
                proof_record_id,
                page_id,
                challenge["id"],
                challenge.get("payment_intent_id"),
                normalize_tier(target_tier),
                challenge["purpose"],
                payload_json,
                payload_hash,
                signature.strip(),
                signature_method,
                challenge["created_at"],
                verified_at,
            ),
            )
        cursor.execute("SELECT * FROM wallet_proof_records WHERE id = ?", (proof_record_id,))
        row = cursor.fetchone()
        assert row is not None
        return self._proof_row_payload(row)

    def verify_challenge(self, challenge_id: str, proof: str) -> dict[str, Any]:
        challenge_dict = self.get_challenge_by_id(challenge_id)
        if challenge_dict is None:
            raise HTTPException(status_code=404, detail="Proof challenge not found.")
        if challenge_dict["status"] != "pending":
            raise HTTPException(status_code=400, detail="Proof challenge is not pending.")
        expires_at = parse_timestamp(challenge_dict["expires_at"])
        if expires_at is not None and expires_at < self.settings.now_fn():
            raise HTTPException(status_code=400, detail="Proof challenge has expired.")
        if not self._challenge_is_one_time_payload(challenge_dict):
            raise HTTPException(status_code=409, detail="Legacy proof challenge must be refreshed before verification.")
        if challenge_dict["proof_method"] == "mock":
            if proof.strip() != "mock-valid":
                raise HTTPException(status_code=400, detail="Proof verification failed.")
        elif challenge_dict["proof_method"] == "bitcoin-message":
            page = self.get_page_by_id(challenge_dict["page_id"])
            if page is None:
                raise HTTPException(status_code=404, detail="Page not found.")
            btc_address = page["btc_address"]
            message = challenge_dict["challenge_text"]
            if not self._verify_bitcoin_message(btc_address, proof.strip(), message):
                raise HTTPException(status_code=400, detail="Bitcoin signature verification failed. Make sure you signed the exact one-time challenge payload with the correct wallet.")
        elif challenge_dict["proof_method"] == "bip322-simple":
            page = self.get_page_by_id(challenge_dict["page_id"])
            if page is None:
                raise HTTPException(status_code=404, detail="Page not found.")
            btc_address = page["btc_address"]
            message = challenge_dict["challenge_text"]
            if not self._verify_bip322_simple(btc_address, proof.strip(), message):
                raise HTTPException(
                    status_code=400,
                    detail="BIP-322 signature verification failed. Make sure you signed the exact one-time challenge payload with a compatible wallet for the listed bc1q address.",
                )
        else:
            raise HTTPException(status_code=501, detail="Wallet proof verification is not configured yet.")

        now = self.settings.now_fn()
        verified_at = utc_isoformat(now)
        payment_id = challenge_dict["payment_intent_id"]
        result: dict[str, Any] = {}
        activated_payment_id: Optional[str] = None
        recovery_page_id: Optional[str] = None
        proof_record: Optional[dict[str, Any]] = None
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE wallet_proof_challenges
                SET status = 'verified',
                    verified_at = ?
                WHERE id = ?
                """,
                (verified_at, challenge_id),
            )
            if payment_id:
                cursor.execute("SELECT * FROM payment_intents WHERE id = ?", (payment_id,))
                payment = cursor.fetchone()
                if payment is None:
                    raise HTTPException(status_code=404, detail="Payment intent not found.")
                payment_dict = dict(payment)
                proof_record = self._create_proof_record(
                    cursor,
                    page_id=challenge_dict["page_id"],
                    challenge=challenge_dict,
                    payment=payment_dict,
                    signature=proof,
                    signature_method=challenge_dict["proof_method"],
                    verified_at=verified_at,
                )
                cursor.execute(
                    """
                    UPDATE payment_intents
                    SET status = 'paid',
                        activated_at = ?
                    WHERE id = ?
                    """,
                    (verified_at, payment_id),
                )
                self._apply_activation(
                    cursor,
                    payment_dict,
                    now,
                    challenge_dict["proof_method"],
                    verified_at,
                    proof_record=proof_record,
                )
                activated_payment_id = payment_id
            elif challenge_dict["purpose"] == "recover":
                recovery_page_id = challenge_dict["page_id"]
            else:
                proof_record = self._create_proof_record(
                    cursor,
                    page_id=challenge_dict["page_id"],
                    challenge=challenge_dict,
                    payment=None,
                    signature=proof,
                    signature_method=challenge_dict["proof_method"],
                    verified_at=verified_at,
                )
                cursor.execute(
                    """
                    UPDATE pages
                    SET wallet_proof_method = ?,
                        wallet_proof_verified_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        challenge_dict["proof_method"],
                        verified_at,
                        verified_at,
                        challenge_dict["page_id"],
                    ),
                )
            connection.commit()

        if activated_payment_id:
            result["payment_intent"] = self.get_payment_intent(activated_payment_id)
        if recovery_page_id:
            result["campaign_key"] = self.rotate_campaign_key(recovery_page_id, now=now)
        page = self.get_page_by_id(challenge_dict["page_id"])
        assert page is not None
        result["page"] = page
        if proof_record is not None:
            proof_record = self.get_proof_record_by_id(proof_record["id"]) or proof_record
        result["challenge"] = {
            **challenge_dict,
            "status": "verified",
            "verified_at": verified_at,
        }
        if proof_record is not None:
            result["proof_record"] = proof_record
        return result

    def _apply_activation(
        self,
        cursor: sqlite3.Cursor,
        payment: dict[str, Any],
        now: dt.datetime,
        proof_method: Optional[str],
        verified_at: Optional[str],
        proof_record: Optional[dict[str, Any]] = None,
    ) -> None:
        cursor.execute("SELECT * FROM pages WHERE id = ?", (payment["page_id"],))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        page = dict(row)
        target_tier = normalize_tier(payment["target_tier"])
        purpose = payment["purpose"]
        current_active_until = parse_timestamp(page["active_until"]) or now
        if purpose == "renew" and page["tier"] != target_tier:
            raise HTTPException(status_code=400, detail="Renewal tier mismatch.")
        if purpose == "renew":
            base_start = current_active_until if page["public_state"] == "active" and current_active_until > now else now
            new_active_until = base_start + dt.timedelta(days=active_days_for_tier(target_tier))
        elif purpose == "upgrade" and target_tier == "tier3":
            base_start = current_active_until if current_active_until > now else now
            new_active_until = base_start + dt.timedelta(days=TIER3_DURATION_DAYS)
        else:
            new_active_until = now + dt.timedelta(days=active_days_for_tier(target_tier))

        new_slug = page["slug"]
        new_slug_kind = page["slug_kind"]
        if target_tier == "tier2":
            if self.tier2_page_exists_for_btc_address(page["btc_address"], exclude_page_id=page["id"]):
                raise HTTPException(status_code=409, detail="A tier2 page already exists for this Bitcoin address.")
            # During redacted invite-code testing, keep the opaque random slug so the public page
            # does not leak the BTC destination via its route or inline verify links.
            if not self.settings.payment_details_redacted:
                new_slug = page["btc_address"]
                new_slug_kind = "address"
        elif target_tier == "tier3":
            pending_vanity_slug = page["pending_vanity_slug"]
            if not pending_vanity_slug:
                raise HTTPException(status_code=400, detail="Tier3 slug is missing.")
            if self.slug_is_tombstoned(pending_vanity_slug):
                raise HTTPException(status_code=409, detail="Tier3 slug has been retired.")
            if self.page_exists_for_slug(pending_vanity_slug, exclude_page_id=page["id"]) and page["slug"] != pending_vanity_slug:
                raise HTTPException(status_code=409, detail="Tier3 slug is unavailable.")
            new_slug = pending_vanity_slug
            new_slug_kind = "vanity"

        new_active_until_iso = utc_isoformat(new_active_until)
        new_grace_until_iso = utc_isoformat(new_active_until + dt.timedelta(days=grace_days_for_tier(target_tier)))
        updated_at = utc_isoformat(now)
        cursor.execute(
            """
            UPDATE pages
            SET slug = ?,
                slug_kind = ?,
                tier = ?,
                requested_tier = NULL,
                public_state = 'active',
                active_until = ?,
                grace_until = ?,
                updated_at = ?,
                wallet_proof_method = ?,
                wallet_proof_verified_at = ?
            WHERE id = ?
            """,
            (
                new_slug,
                new_slug_kind,
                target_tier,
                new_active_until_iso,
                new_grace_until_iso,
                updated_at,
                proof_method,
                verified_at,
                page["id"],
            ),
        )
        event_page = {
            **page,
            "tier": target_tier,
            "slug": new_slug,
            "page_ref": new_slug,
            "public_state": "active",
        }
        if proof_record is not None:
            self._record_page_event(
                cursor,
                page=event_page,
                event_type="activated",
                created_at=verified_at,
                payload_json=canonical_json_dumps(proof_record["payload"]),
                payload_hash=proof_record["payload_hash"],
                proof_record_id=proof_record["id"],
            )

    def rotate_campaign_key(self, page_id: str, *, now: Optional[dt.datetime] = None) -> dict[str, Any]:
        current = now or self.settings.now_fn()
        timestamp = utc_isoformat(current)
        new_key_id = make_key_id()
        new_secret = make_secret()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE campaign_keys
                SET revoked_at = ?, revocation_reason = 'recovery'
                WHERE page_id = ? AND revoked_at IS NULL
                """,
                (timestamp, page_id),
            )
            cursor.execute(
                """
                INSERT INTO campaign_keys (id, page_id, key_id, key_version, secret_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), page_id, new_key_id, 1, secret_hash(new_secret), timestamp),
            )
            connection.commit()
        return {
            "version": 1,
            "registry": canonicalize_base_url(self.settings.public_base_url).removeprefix("https://").removeprefix("http://"),
            "page_id": page_id,
            "key_id": new_key_id,
            "key_version": 1,
            "secret": new_secret,
        }

    def create_recovery_challenge(self, page_ref: str) -> dict[str, Any]:
        page = self.get_page_by_ref(page_ref)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if page["public_state"] in {"deleted", "tombstoned"}:
            raise HTTPException(status_code=400, detail="Recovery is unavailable for this page state.")
        if not self.settings.allow_dev_actions and self.settings.proof_mode == "disabled":
            raise HTTPException(status_code=503, detail="Wallet-proof recovery is not enabled yet.")
        self._ensure_wallet_proof_address_supported(page["btc_address"])
        now = self.settings.now_fn()
        created_at = utc_isoformat(now)
        expires_at = utc_isoformat(now + dt.timedelta(minutes=15))
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM wallet_proof_challenges
                WHERE page_id = ? AND purpose = 'recover' AND status = 'pending'
                ORDER BY created_at DESC
                """,
                (page["id"],),
            )
            rows = cursor.fetchall()
            reusable_challenge: Optional[dict[str, Any]] = None
            superseded_ids: list[str] = []
            for row in rows:
                candidate = self._challenge_response_payload(row)
                expires_at_dt = parse_timestamp(candidate.get("expires_at"))
                still_valid = (
                    expires_at_dt is not None
                    and expires_at_dt > now
                    and self._challenge_is_one_time_payload(candidate)
                )
                if reusable_challenge is None and still_valid:
                    reusable_challenge = candidate
                    continue
                superseded_ids.append(candidate["id"])
            if superseded_ids:
                cursor.executemany(
                    "UPDATE wallet_proof_challenges SET status = 'superseded' WHERE id = ? AND status = 'pending'",
                    [(challenge_id,) for challenge_id in superseded_ids],
                )
            if reusable_challenge is None:
                challenge = self._issue_wallet_proof_challenge(
                    cursor,
                    page=page,
                    purpose="recover",
                    proof_method=self._current_proof_method(page["btc_address"]),
                    created_at=created_at,
                    expires_at=expires_at,
                    payment_intent_id=None,
                    target_tier=page["tier"],
                )
            else:
                challenge = reusable_challenge
            connection.commit()
        return {
            "challenge_id": challenge["id"],
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "challenge_text": challenge["challenge_text"],
            "challenge_payload": challenge["challenge_payload"],
            "challenge_hash": challenge["challenge_hash"],
            "proof_method": challenge["proof_method"],
            "created_at": challenge["created_at"],
            "expires_at": challenge["expires_at"],
            "payload": challenge["payload"],
            "payload_json": challenge["payload_json"],
            "payload_hash": challenge["payload_hash"],
        }

    def update_links(self, page_id: str, links: list[LinkInput]) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if self._links_locked(page):
            raise HTTPException(status_code=409, detail="Links are locked for this page.")
        normalized_links = normalize_links_payload([link.model_dump() for link in links], reject_invalid=True)
        now = utc_isoformat(self.settings.now_fn())
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE pages SET links_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(normalized_links), now, page_id),
            )
            self._record_page_event(
                cursor,
                page=page,
                event_type="links_updated",
                created_at=now,
                details={"links": normalized_links},
            )
            connection.commit()
        updated_page = self.get_page_by_id(page_id)
        assert updated_page is not None
        return updated_page

    def add_update(self, page_id: str, body: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if page["tier"] != "tier3":
            raise HTTPException(status_code=403, detail="Only tier3 pages may publish updates.")
        if page["public_state"] not in {"active", "expired"}:
            raise HTTPException(status_code=409, detail="Updates are not allowed for this page state.")
        text = body.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Update body is required.")
        if len(text) > 4000:
            raise HTTPException(status_code=400, detail="Update body is too long.")
        now = utc_isoformat(self.settings.now_fn())
        update_id = str(uuid.uuid4())
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO page_updates (id, page_id, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (update_id, page_id, text, now, now),
            )
            connection.execute("UPDATE pages SET updated_at = ? WHERE id = ?", (now, page_id))
            connection.commit()
        return {"id": update_id, "page_id": page_id, "body": text, "created_at": now, "updated_at": now}

    def _change_page_lifecycle(self, page_id: str, *, event_type: str, new_state: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if page["public_state"] in {"aborted", "compromised", "dead", "deleted", "tombstoned"}:
            raise HTTPException(status_code=409, detail="This page is no longer active.")
        created_at = utc_isoformat(self.settings.now_fn())
        payload_json = None
        payload_hash = None
        if page["tier"] == "tier3":
            _payload, payload_json, payload_hash = self._event_payload_for_page(page, event_type, created_at=created_at)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE pages
                SET public_state = ?,
                    active_until = ?,
                    grace_until = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_state, created_at, created_at, created_at, page_id),
            )
            lifecycle_page = {**page, "public_state": new_state}
            self._record_page_event(
                cursor,
                page=lifecycle_page,
                event_type=event_type,
                created_at=created_at,
                payload_json=payload_json,
                payload_hash=payload_hash,
            )
            connection.commit()
        updated_page = self.get_page_by_id(page_id)
        assert updated_page is not None
        return updated_page

    def abort_page(self, page_id: str) -> dict[str, Any]:
        return self._change_page_lifecycle(page_id, event_type="aborted", new_state="aborted")

    def compromise_page(self, page_id: str) -> dict[str, Any]:
        return self._change_page_lifecycle(page_id, event_type="compromised", new_state="compromised")

    def archive_page(self, page_id: str) -> dict[str, Any]:
        return self.abort_page(page_id)

    def report_page(self, page_id: str, *, reason: str, note: Optional[str]) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        normalized_reason = reason.strip().lower()
        if not normalized_reason:
            raise HTTPException(status_code=400, detail="Report reason is required.")
        report_id = str(uuid.uuid4())
        created_at = utc_isoformat(self.settings.now_fn())
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO abuse_reports (id, page_id, reason, note, created_at, status)
                VALUES (?, ?, ?, ?, ?, 'open')
                """,
                (report_id, page_id, normalized_reason, normalize_optional_text(note), created_at),
            )
            connection.commit()
        return {"id": report_id, "page_id": page_id, "reason": normalized_reason, "note": normalize_optional_text(note), "created_at": created_at, "status": "open"}

    def store_contact_message(
        self,
        *,
        message: str,
        email: Optional[str],
        page_url: Optional[str],
        ip_prefix: str,
        source_host: str,
    ) -> dict[str, Any]:
        record = {
            "id": str(uuid.uuid4()),
            "ts": utc_isoformat(self.settings.now_fn()),
            "ip_prefix": ip_prefix,
            "source_host": source_host,
            "message": message,
            "email": email,
            "page_url": page_url,
            "read": False,
        }
        self.settings.messages_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings.messages_path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json_dumps(record))
            handle.write("\n")
        return record

    def mark_message_read(self, message_id: str) -> Optional[dict[str, Any]]:
        if not self.settings.messages_path.exists():
            return None
        lines: list[str] = []
        updated_record: Optional[dict[str, Any]] = None
        with self.settings.messages_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    lines.append(stripped)
                    continue
                if record.get("id") == message_id:
                    record["read"] = True
                    updated_record = record
                lines.append(canonical_json_dumps(record))
        if updated_record is None:
            return None
        with self.settings.messages_path.open("w", encoding="utf-8") as handle:
            for entry in lines:
                handle.write(entry)
                handle.write("\n")
        return updated_record

    def mark_all_messages_read(self) -> int:
        if not self.settings.messages_path.exists():
            return 0
        lines: list[str] = []
        count = 0
        with self.settings.messages_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    lines.append(stripped)
                    continue
                if not record.get("read", False):
                    record["read"] = True
                    count += 1
                lines.append(canonical_json_dumps(record))
        if count == 0:
            return 0
        with self.settings.messages_path.open("w", encoding="utf-8") as handle:
            for entry in lines:
                handle.write(entry)
                handle.write("\n")
        return count

    def save_story_photo(
        self,
        *,
        page_id: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        media_type = content_type.strip().lower()
        extension = ALLOWED_STORY_PHOTO_TYPES.get(media_type)
        if extension is None:
            raise HTTPException(status_code=400, detail="Story photo must be JPEG, PNG, or WebP.")
        if len(content) > MAX_STORY_PHOTO_BYTES:
            raise HTTPException(status_code=400, detail="Story photo must be 200KB or smaller.")
        if not image_signature_is_valid(media_type, content):
            raise HTTPException(status_code=400, detail="Story photo payload is invalid.")

        filename = f"{page_id}{extension}"
        target_path = self.settings.photo_dir / filename
        target_path.write_bytes(content)
        now = utc_isoformat(self.settings.now_fn())
        relative_path = filename
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE pages
                SET story_photo_path = ?,
                    story_photo_media_type = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (relative_path, media_type, now, page_id),
            )
            connection.commit()
        page = self.get_page_by_id(page_id)
        assert page is not None
        return page

    def save_progress_photo(
        self,
        *,
        page_id: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        if page["tier"] != "tier3":
            raise HTTPException(status_code=403, detail="Only tier3 pages may add a progress photo.")
        if page["public_state"] != "active":
            raise HTTPException(status_code=409, detail="Progress photos can only be added while the campaign is active.")
        if page.get("progress_photo_path"):
            raise HTTPException(status_code=409, detail="A progress photo has already been uploaded for this page.")
        media_type = content_type.strip().lower()
        extension = ALLOWED_STORY_PHOTO_TYPES.get(media_type)
        if extension is None:
            raise HTTPException(status_code=400, detail="Progress photo must be JPEG, PNG, or WebP.")
        if len(content) > MAX_STORY_PHOTO_BYTES:
            raise HTTPException(status_code=400, detail="Progress photo must be 200KB or smaller.")
        if not image_signature_is_valid(media_type, content):
            raise HTTPException(status_code=400, detail="Progress photo payload is invalid.")

        filename = f"{page_id}-progress{extension}"
        target_path = self.settings.photo_dir / filename
        target_path.write_bytes(content)
        now = utc_isoformat(self.settings.now_fn())
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE pages
                SET progress_photo_path = ?,
                    progress_photo_media_type = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (filename, media_type, now, page_id),
            )
            connection.commit()
        updated = self.get_page_by_id(page_id)
        assert updated is not None
        return updated

    def story_photo_file(self, page_id: str) -> tuple[Path, str]:
        page = self.get_page_by_id(page_id)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Story photo not found.")
        relative_path = page.get("story_photo_path")
        media_type = page.get("story_photo_media_type")
        if not relative_path or not media_type:
            raise HTTPException(status_code=404, detail="Story photo not found.")
        target_path = self.settings.photo_dir / relative_path
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="Story photo not found.")
        return target_path, media_type

    def progress_photo_file(self, page_id: str) -> tuple[Path, str]:
        page = self.get_page_by_id(page_id)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Progress photo not found.")
        relative_path = page.get("progress_photo_path")
        media_type = page.get("progress_photo_media_type")
        if not relative_path or not media_type:
            raise HTTPException(status_code=404, detail="Progress photo not found.")
        target_path = self.settings.photo_dir / relative_path
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="Progress photo not found.")
        return target_path, media_type

    def _address_cache_path(self, address: str) -> Path:
        digest = hashlib.sha256(address.encode("utf-8")).hexdigest()
        return self.settings.transaction_cache_dir / f"{digest}.json"

    def _fetch_json(self, url: str) -> Any:
        if self.settings.fetch_json_fn is not None:
            return self.settings.fetch_json_fn(url)
        request = urllib_request.Request(
            url,
            headers={"User-Agent": "FundRegistry/0.2 (+https://fundregistry.org)"},
        )
        try:
            with urllib_request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            detail = exc.reason or "Transaction lookup failed."
            raise HTTPException(status_code=502, detail=f"Transaction lookup failed: {detail}") from exc
        except urllib_error.URLError as exc:
            raise HTTPException(status_code=502, detail="Transaction lookup failed.") from exc

    def _address_endpoint(self, address: str, suffix: str = "") -> str:
        base = self.settings.mempool_base_url.rstrip("/")
        encoded = urllib_parse.quote(address, safe="")
        return f"{base}/address/{encoded}{suffix}"

    def _normalize_transaction_rows(self, address: str, txs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            outputs = tx.get("vout") or []
            received_sat = 0
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                if output.get("scriptpubkey_address") == address:
                    received_sat += int(output.get("value") or 0)
            status = tx.get("status") or {}
            rows.append(
                {
                    "txid": tx.get("txid"),
                    "received_sat": received_sat,
                    "received_btc": f"{received_sat / 100_000_000:.8f}".rstrip("0").rstrip("."),
                    "confirmed": bool(status.get("confirmed")),
                    "confirmed_at": utc_isoformat(
                        dt.datetime.fromtimestamp(status["block_time"], tz=dt.timezone.utc)
                    )
                    if status.get("block_time")
                    else None,
                    "block_height": status.get("block_height"),
                }
            )
        return rows

    def _fetch_transactions_from_mempool(self, address: str, *, include_full_history: bool) -> dict[str, Any]:
        summary = self._fetch_json(self._address_endpoint(address))
        txs: list[dict[str, Any]] = []
        latest = self._fetch_json(self._address_endpoint(address, "/txs"))
        if isinstance(latest, list):
            txs.extend(latest)

        if include_full_history and txs:
            seen = {str(tx.get("txid")) for tx in txs if isinstance(tx, dict) and tx.get("txid")}
            cursor = str(txs[-1].get("txid") or "")
            while cursor:
                chain_rows = self._fetch_json(self._address_endpoint(address, f"/txs/chain/{cursor}"))
                if not isinstance(chain_rows, list) or not chain_rows:
                    break
                appended = 0
                for tx in chain_rows:
                    txid = str(tx.get("txid") or "")
                    if not txid or txid in seen:
                        continue
                    txs.append(tx)
                    seen.add(txid)
                    appended += 1
                if appended == 0:
                    break
                cursor = str(chain_rows[-1].get("txid") or "")

        return {
            "summary": summary if isinstance(summary, dict) else {},
            "transactions": self._normalize_transaction_rows(address, txs),
            "fetched_at": utc_isoformat(self.settings.now_fn()),
        }

    def _get_transactions_for_page(self, page: dict[str, Any]) -> dict[str, Any]:
        cache_path = self._address_cache_path(page["btc_address"])
        cached_payload: Optional[dict[str, Any]] = None
        now = self.settings.now_fn()
        if cache_path.exists():
            try:
                cached_payload = json.loads(cache_path.read_text())
            except json.JSONDecodeError:
                cached_payload = None
        if cached_payload is not None:
            fetched_at = parse_timestamp(cached_payload.get("fetched_at"))
            if fetched_at is not None:
                age = (now - fetched_at).total_seconds()
                if age <= self.settings.transaction_cache_ttl_seconds:
                    return self._transactions_payload_for_page(page, cached_payload)

        fresh_payload = self._fetch_transactions_from_mempool(
            page["btc_address"],
            include_full_history=is_paid_tier(page["tier"]),
        )
        cache_path.write_text(json.dumps(fresh_payload))
        return self._transactions_payload_for_page(page, fresh_payload)

    def get_transactions(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        return self._get_transactions_for_page(page)

    def _transactions_payload_for_page(self, page: dict[str, Any], raw_payload: dict[str, Any]) -> dict[str, Any]:
        summary = raw_payload.get("summary") or {}
        all_transactions = raw_payload.get("transactions") or []
        visible_transactions = all_transactions if is_paid_tier(page["tier"]) else all_transactions[:5]
        chain_stats = summary.get("chain_stats") or {}
        mempool_stats = summary.get("mempool_stats") or {}
        funded_total_sat = int(chain_stats.get("funded_txo_sum") or 0) + int(mempool_stats.get("funded_txo_sum") or 0)
        tx_count = int(chain_stats.get("tx_count") or 0) + int(mempool_stats.get("tx_count") or 0)
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "tier": page["tier"],
            "history_mode": "full" if is_paid_tier(page["tier"]) else "recent",
            "transactions": visible_transactions,
            "visible_count": len(visible_transactions),
            "total_count": tx_count,
            "total_received_sat": funded_total_sat,
            "total_received_btc": f"{funded_total_sat / 100_000_000:.8f}".rstrip("0").rstrip("."),
            "fetched_at": raw_payload.get("fetched_at"),
        }

    def _button_payload_for_page(self, page: dict[str, Any]) -> dict[str, Any]:
        state = button_state(page)
        code = page.get("verification_code")
        href = self.page_url(page["page_ref"])
        dot = dot_for_state(state)
        label = f"fundregistry.org · {code}"
        state_colors = {
            "dead":           ("#FAFAFA", "#D5D0C8", "#7A7570"),
            "compromised":    ("#FDE8E8", "#EF9A9A", "#C62828"),
            "aborted":        ("#FAFAFA", "#D5D0C8", "#7A7570"),
            "anchor_pending": ("#F4F0FF", "#D1C4E9", "#5E548E"),
            "expired":        ("#FFF3E0", "#FFCC80", "#E65100"),
        }
        fill, border, text_color = state_colors.get(state, ("#E8F5E9", "#A5D6A7", "#2E7D32"))

        html_snippet = (
            f'<a href="{html.escape(href)}" '
            'style="display:inline-flex;align-items:center;gap:6px;'
            f'padding:6px 14px;background:{fill};border:1px solid {border};'
            'border-radius:4px;font-family:system-ui,sans-serif;font-size:13px;'
            f'color:{text_color};text-decoration:none;white-space:nowrap;line-height:1.2;">'
            f'<span style="display:inline-block;width:12px;text-align:center;">{dot}</span>'
            f' {html.escape(label)}'
            "</a>"
        )
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "verification_code": code,
            "current_state": state,
            "title": page["title"],
            "label": label,
            "link_url": href,
            "html_snippet": html_snippet,
        }

    def button_payload(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Page not found.")
        return self._button_payload_for_page(page)

    def share_payload(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Page not found.")
        page_url = self.page_url(page["page_ref"])
        badge_url = self.badge_url(page["page_ref"]) if is_paid_tier(page["tier"]) and page["public_state"] != "deleted" else None
        button = self._button_payload_for_page(page)
        html_snippet = button["html_snippet"]
        compact_html_snippet = button["html_snippet"]
        markdown_snippet = f"[{button['label']}]({button['link_url']})"
        if badge_url:
            markdown_snippet = f"[![Wallet Verified]({badge_url})]({page_url})"
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "verification_code": page.get("verification_code"),
            "canonical_page_url": page_url,
            "button_state": button["current_state"],
            "button_link_url": button["link_url"],
            "badge_svg_url": badge_url,
            "markdown_snippet": markdown_snippet,
            "html_snippet": html_snippet,
            "compact_html_snippet": compact_html_snippet,
            "button_html_snippet": button["html_snippet"],
            "preview": {
                "title": page["title"],
                "tier": page["tier"],
                "status": page["public_state"],
                "valid_until": page["active_until"],
            },
        }

    def proof_payload(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        proof_record = self.get_latest_proof_record(page_id)
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "tier": page["tier"],
            "proof_status": page.get("proof_status"),
            "proof_record": proof_record,
        }

    def anchor_payload(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        events = self.get_page_events(page_id, public_only=True)
        anchor_events = [event for event in events if event.get("anchor_status")]
        return {
            "page_id": page["id"],
            "page_ref": page["page_ref"],
            "tier": page["tier"],
            "anchor_events": anchor_events,
            "latest_anchor_event": anchor_events[-1] if anchor_events else None,
        }

    def verify_payload(self, page_id: str) -> dict[str, Any]:
        page = self.get_page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found.")
        return self._verify_payload(page)

    def proof_bundle(self, page_id: str) -> dict[str, Any]:
        return self.verify_payload(page_id)


def button_state(page: dict[str, Any]) -> str:
    proof_state = page.get("proof_status")
    if proof_state:
        return proof_state
    if page["public_state"] in {"dead", "deleted", "tombstoned"}:
        return "dead"
    if page["public_state"] == "compromised":
        return "compromised"
    if page["public_state"] == "aborted":
        return "aborted"
    if page["public_state"] == "expired":
        return "expired"
    return "active"


def dot_for_state(state: str) -> str:
    if state == "expired":
        return "◆"
    if state == "compromised":
        return "!"
    if state == "aborted":
        return "■"
    if state == "dead":
        return "⊘"
    if state == "anchor_pending":
        return "◐"
    if state == "anchored":
        return "⬢"
    return "●"


def badge_dot_color(page: dict[str, Any]) -> str:
    if button_state(page) in {"active", "verified", "anchored"}:
        return "#2E7D32"
    if button_state(page) == "anchor_pending":
        return "#6B5B95"
    if button_state(page) == "compromised":
        return "#C62828"
    return "#9E9E9E"


def badge_state_label(page: dict[str, Any]) -> str:
    state = button_state(page)
    if state == "expired":
        return "Expired"
    if state == "compromised":
        return "Compromised"
    if state == "aborted":
        return "Aborted"
    if state == "anchor_pending":
        return "Anchor Pending"
    if state == "anchored":
        return "Bitcoin Anchored"
    if state == "verified":
        return "Wallet Verified"
    return "Unverified"


def render_badge_svg(page: dict[str, Any]) -> str:
    left_text = badge_state_label(page)
    state = button_state(page)
    if state in {"verified", "anchored", "anchor_pending"}:
        right_text = f"Valid until {format_short_date(page['active_until'])}"
    elif state == "aborted":
        right_text = "Historical"
    elif state == "compromised":
        right_text = "Do not fund"
    else:
        right_text = "Inactive"
    left_width = max(110, 12 + len(left_text) * 7)
    right_width = max(90, 12 + len(right_text) * 7)
    total_width = left_width + right_width
    if state in {"verified", "anchored"}:
        fill = "#E8F5E9"
        text_color = "#2E7D32"
    elif state == "anchor_pending":
        fill = "#F4F0FF"
        text_color = "#5E548E"
    elif state == "compromised":
        fill = "#FDE8E8"
        text_color = "#C62828"
    else:
        fill = "#FAFAFA"
        text_color = "#7A7570"
    dot_fill = badge_dot_color(page)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="28" role="img" '
        f'aria-label="{html.escape(left_text)} — {html.escape(right_text)}">'
        f'<rect width="{left_width}" height="28" rx="4" ry="4" fill="{fill}" stroke="#D5D0C8"/>'
        f'<rect x="{left_width}" width="{right_width}" height="28" rx="4" ry="4" fill="#FFFFFF" stroke="#D5D0C8"/>'
        f'<circle cx="12" cy="14" r="4" fill="{dot_fill}"/>'
        f'<text x="22" y="18" font-family="system-ui, sans-serif" font-size="11" fill="{text_color}">{html.escape(left_text)}</text>'
        f'<text x="{left_width + 8}" y="18" font-family="system-ui, sans-serif" font-size="11" fill="#6B6560">{html.escape(right_text)}</text>'
        "</svg>"
    )


def render_public_page(page: dict[str, Any], *, settings: FundRegistrySettings) -> HTMLResponse:
    title = f"{page['title']}{PAGE_TITLE_SUFFIX}"
    current_state = button_state(page)
    status_bar_class = "free" if page["tier"] == "free" else "active"
    if current_state == "expired":
        status_bar_class = "expired"
    if current_state in {"aborted", "dead"}:
        status_bar_class = "tombstoned"
    if current_state == "compromised":
        status_bar_class = "expired"

    if page["public_state"] == "tombstoned":
        body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/styles.css?v=7">
</head>
<body>
  {BETA_BANNER_HTML}
  <div class="container page">
    <header class="site-header">
      <a href="/" class="site-logo"><svg class="logo-mark" width="22" height="22" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="12" y="12" width="40" height="40" rx="5" stroke="currentColor" stroke-width="3.5"/><line x1="20" y1="24" x2="44" y2="24" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="32" x2="44" y2="32" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="40" x2="33" y2="40" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M35 44.5 L40 39 L45 44.5 L40 47.5 Z" fill="currentColor"/></svg>Fund Registry</a>
      <nav class="site-nav">
        <a href="/create">Create</a>
        <a href="/manage">Manage</a>
      </nav>
    </header>
    <div class="tombstone">
      <h1>Funding page retired</h1>
      <div class="tombstone-meta">{html.escape(page['title'])}</div>
      <div class="tombstone-meta mono">fundregistry.org/fund/{html.escape(page['page_ref'])}</div>
      <div class="tombstone-notice">This vanity slug has been permanently retired and is not available for reuse.</div>
    </div>
  </div>
{CONTACT_BUBBLE_HTML}
</body>
</html>
"""
        return HTMLResponse(body, status_code=410)

    store = FundRegistryStore(settings)
    button = store._button_payload_for_page(page)
    transactions_error = None
    transactions_payload = None
    try:
        transactions_payload = store._get_transactions_for_page(page)
    except HTTPException as exc:
        transactions_error = exc.detail

    meta_parts = []
    state = current_state
    if state in {"verified", "anchored"}:
        meta_parts.append('<span class="badge badge-verified">● Verified</span>')
    elif state == "anchor_pending":
        meta_parts.append('<span class="badge badge-tier">◐ Anchor pending</span>')
    elif state == "aborted":
        meta_parts.append('<span class="badge badge-disputed">■ Aborted</span>')
    elif state == "compromised":
        meta_parts.append('<span class="badge badge-disputed">! Compromised</span>')
    elif state == "expired":
        meta_parts.append('<span class="badge badge-expired">◆ Expired</span>')
    else:
        meta_parts.append('<span class="badge badge-free">○ Unverified</span>')
    meta_parts.append('<span class="meta-separator">·</span>')
    meta_parts.append(f'<span class="badge badge-tier">{html.escape(display_tier_label(page["tier"]))}</span>')
    meta_parts.append('<span class="meta-separator">·</span>')
    meta_parts.append(f'<span class="meta-item mono">{html.escape(page["verification_code"])}</span>')
    meta_parts.append('<span class="meta-separator">·</span>')
    meta_parts.append(f'<span class="meta-item mono">{html.escape(page.get("address_fingerprint") or "")}</span>')
    meta_parts.append('<span class="meta-separator">·</span>')
    if state in {"verified", "anchored", "anchor_pending"}:
        meta_parts.append(f'<span class="meta-item">Valid until {html.escape(format_long_date(page["active_until"]))}</span>')
    else:
        meta_parts.append(f'<span class="meta-item">{html.escape(page["public_state"].title())}</span>')
    meta_parts.append('<span class="meta-separator">·</span>')
    meta_parts.append('<a href="/how-it-works" style="font-size: 0.75rem; color: var(--text-3);">What does this mean?</a>')
    meta_html = "\n".join(meta_parts)

    story_photo_html = ""
    if page.get("story_photo_url"):
        story_photo_html = (
            f'<img src="{html.escape(page["story_photo_url"])}" alt="{html.escape(page["title"])}" '
            'style="width: 152px; max-width: 100%; aspect-ratio: 1 / 1; object-fit: cover; '
            'border-radius: 4px; border: 1px solid var(--border); margin-bottom: var(--space-md);">'
        )

    links_html = ""
    if page["links"]:
        items = []
        for link in page["links"]:
            items.append(
                f'<li><a href="{html.escape(link["url"])}" rel="noopener noreferrer" target="_blank">{html.escape(link["platform"])}</a></li>'
            )
        links_html = f"""
        <div class="section">
          <div class="section-title">Links</div>
          <ul>{''.join(items)}</ul>
        </div>
        """

    transaction_rows_html = ""
    transaction_summary_html = ""
    if transactions_payload:
        if is_paid_tier(page["tier"]):
            transaction_summary_html = f"""
            <div class="funding-summary" style="margin-bottom: var(--space-md);">
              <div class="funding-amount">{html.escape(transactions_payload["total_received_btc"])} BTC raised</div>
              <div class="funding-detail">{transactions_payload["total_count"]} transaction(s)</div>
            </div>
            """
        rendered_rows = []
        for tx in transactions_payload["transactions"]:
            rendered_rows.append(
                f"""
                <div class="update-item">
                  <div class="update-date mono">{html.escape(str(tx["txid"])[:16])}…</div>
                  <div class="update-body">{html.escape(tx["received_btc"])} BTC · {"confirmed" if tx["confirmed"] else "pending"}</div>
                </div>
                """
            )
        if rendered_rows:
            transaction_rows_html = "".join(rendered_rows)
        else:
            transaction_rows_html = '<div class="update-body">No transactions yet.</div>'
    elif transactions_error:
        transaction_rows_html = f'<div class="update-body">Transactions unavailable: {html.escape(str(transactions_error))}</div>'

    verification_html = ""
    if is_paid_tier(page["tier"]):
        proof_line = html.escape(
            proof_method_display_name(
                (page.get("proof_record") or {}).get("signature_method") or page["wallet_proof_method"] or "Pending proof"
            )
        )
        verified_on = html.escape(format_long_date(page["wallet_proof_verified_at"])) if page["wallet_proof_verified_at"] else "Pending"
        latest_anchor = page.get("latest_anchor_event")
        anchor_line = "No Bitcoin anchor"
        if latest_anchor and latest_anchor.get("anchor_txid"):
            receipt = latest_anchor.get("anchor_receipt")
            prefix = f"{receipt['format']} · " if isinstance(receipt, dict) and receipt.get("format") else ""
            anchor_line = f"{prefix}{latest_anchor['event_type']} · {str(latest_anchor['anchor_txid'])[:16]}…"
        verification_html = f"""
        <div class="section">
          <div class="section-title">Verification</div>
          <div style="font-size: 0.875rem; color: var(--text-2);">
            <div style="padding: 4px 0;">● Wallet control verified on {verified_on}</div>
            <div style="padding: 4px 0;">● Evidence: {proof_line}</div>
            <div style="padding: 4px 0;">● Address fingerprint: {html.escape(page.get("address_fingerprint") or "Unknown")}</div>
            <div style="padding: 4px 0;">● Anchor: {html.escape(anchor_line)}</div>
            <div style="padding: 4px 0;">● <a href="{html.escape(page['verify_url'])}">Open verification record</a></div>
          </div>
        </div>
        """

    updates_html = ""
    if page["updates"]:
        rendered = []
        for update in page["updates"]:
            rendered.append(
                f"""
                <div class="update-item">
                  <div class="update-date">{html.escape(format_long_date(update["created_at"]))}</div>
                  <div class="update-body">{html.escape(update["body"])}</div>
                </div>
                """
            )
        updates_html = f"""
        <div class="section">
          <div class="section-title">Updates</div>
          {''.join(rendered)}
        </div>
        """

    progress_photo_html = ""
    if page.get("progress_photo_url"):
        progress_photo_html = f"""
        <div class="section">
          <div class="section-title">Progress photo</div>
          <img src="{html.escape(page['progress_photo_url'])}" alt="{html.escape(page['title'])} progress" style="width: 152px; max-width: 100%; aspect-ratio: 1 / 1; object-fit: cover; border-radius: 4px; border: 1px solid var(--border);">
        </div>
        """

    event_rows = []
    for event in page.get("event_ledger") or []:
        label = event["event_type"].replace("_", " ").title()
        right = ""
        if event.get("anchor_txid"):
            right = f" · {str(event['anchor_txid'])[:16]}…"
        event_rows.append(
            f"""
            <div class="update-item">
              <div class="update-date">{html.escape(format_long_date(event["created_at"]))}</div>
              <div class="update-body">{html.escape(label)}{html.escape(right)}</div>
            </div>
            """
        )
    events_html = ""
    if event_rows:
        events_html = f"""
        <div class="section">
          <div class="section-title">Event ledger</div>
          {''.join(event_rows)}
        </div>
        """

    escaped_snippet = html.escape(button["html_snippet"])
    share_html = f"""
        <div class="section share-section">
          <div class="section-title">Verification button</div>
          <div class="share-preview">
            <div style="margin-bottom: 10px;">{button["html_snippet"]}</div>
            <div style="position:relative;">
              <pre class="embed-code" style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:10px 40px 10px 10px;font-family:var(--font-mono);font-size:0.6875rem;line-height:1.4;overflow-x:auto;white-space:pre-wrap;word-break:break-all;color:var(--text-2);margin:0;">{escaped_snippet}</pre>
              <button onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent).then(function(){{var b=event.target;b.textContent='Copied';setTimeout(function(){{b.textContent='Copy'}},1500)}})" style="position:absolute;top:6px;right:6px;padding:3px 8px;font-size:0.6875rem;background:var(--surface-elevated);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--text-2);">Copy</button>
            </div>
            <div style="font-size:0.6875rem;color:var(--text-3);margin-top:6px;">Paste this HTML on your website or link-in-bio to show your verification status.</div>
          </div>
        </div>
    """

    state_notice_html = ""
    if page["public_state"] == "aborted":
        state_notice_html = f"""
        <div class="warning" style="margin-top: var(--space-md);">
          <span class="warning-icon">!</span>
          <div>{html.escape(ABORT_STATEMENT)}</div>
        </div>
        """
    elif page["public_state"] == "compromised":
        state_notice_html = f"""
        <div class="warning" style="margin-top: var(--space-md);">
          <span class="warning-icon">!</span>
          <div>{html.escape(COMPROMISED_STATEMENT)}</div>
        </div>
        """
    elif page["public_state"] == "dead":
        state_notice_html = """
        <div class="warning" style="margin-top: var(--space-md);">
          <span class="warning-icon">!</span>
          <div>This Fund Registry page is no longer active and should not be treated as an active funding page.</div>
        </div>
        """

    overlay_html = ""
    if settings.payments_paused:
        funding_action_html = f"""
        <div class="section">
          <div class="section-title">Fund this campaign</div>
          <div class="qr-section" style="margin-top: var(--space-md); align-items: stretch;">
            <div
              aria-hidden="true"
              style="width: 140px; height: 140px; border-radius: var(--radius); border: 1px solid var(--border); background:
                linear-gradient(135deg, rgba(44,44,44,0.14) 0%, rgba(44,44,44,0.03) 100%),
                repeating-linear-gradient(0deg, rgba(44,44,44,0.08) 0 8px, rgba(255,255,255,0.24) 8px 16px);
                filter: blur(1.6px); display: block; flex-shrink: 0;"
            ></div>
            <div class="qr-info">
              <p style="margin-bottom: var(--space-sm);">{html.escape(PAYMENTS_PAUSED_MESSAGE)}</p>
              <div class="warning" style="margin: 0;">
                <span class="warning-icon">⚠</span>
                <div>QR codes and payment addresses are hidden until the updated payment UI is ready.</div>
              </div>
            </div>
          </div>
        </div>
        """
    elif settings.payment_details_redacted:
        funding_action_html = f"""
        <div class="section">
          <div class="section-title">Fund this campaign</div>
          <div class="qr-section" style="margin-top: var(--space-md); align-items: stretch;">
            <div
              aria-hidden="true"
              style="width: 140px; height: 140px; border-radius: var(--radius); border: 1px solid var(--border); background:
                linear-gradient(135deg, rgba(44,44,44,0.14) 0%, rgba(44,44,44,0.03) 100%),
                repeating-linear-gradient(0deg, rgba(44,44,44,0.08) 0 8px, rgba(255,255,255,0.24) 8px 16px);
                filter: blur(1.6px); display: block; flex-shrink: 0;"
            ></div>
            <div class="qr-info">
              <p style="margin-bottom: var(--space-sm);">{html.escape(PAYMENT_DETAILS_REDACTED_MESSAGE)}</p>
              <div class="warning" style="margin: 0;">
                <span class="warning-icon">⚠</span>
                <div>QR codes and payment addresses are hidden on the public page during invite-code testing.</div>
              </div>
            </div>
          </div>
        </div>
        """
    else:
        qr_image_uri = html.escape(render_qr_png_data_uri(bitcoin_uri(page["btc_address"])), quote=True)
        funding_action_html = f"""
            <div class="section">
              <div class="section-title">Fund this campaign</div>
              <div class="address-box">
                <span>{html.escape(page["btc_address"])}</span>
              </div>
              <div class="qr-section" style="margin-top: var(--space-md);">
                <img
                  src="{qr_image_uri}"
                  alt="{html.escape(f'Bitcoin QR code for {page["title"]}', quote=True)}"
                  width="140"
                  height="140"
                  style="width: 140px; height: 140px; background: #FFFFFF; border: 1px solid var(--border); border-radius: var(--radius); display: block; flex-shrink: 0;"
                >
                <div class="qr-info">
                  <p style="margin-bottom: var(--space-sm);">Scan or copy the address above. Bitcoin sent to this address goes directly to the listed wallet.</p>
                  <div class="warning" style="margin: 0;">
                    <span class="warning-icon">⚠</span>
                    <div>Transactions are irreversible. Verify the address before sending.</div>
                  </div>
                </div>
              </div>
            </div>
        """
    if page["public_state"] == "expired":
        overlay_html = f"""
        <div class="expired-overlay">
          <div class="expired-card">
            <h2>Expired</h2>
            <p>This funding page is no longer active.</p>
            <p>Funding actions are disabled until renewed.</p>
            <a class="btn btn-primary" href="/renew">Renew page</a>
          </div>
        </div>
        """
        funding_action_html = ""
    if page["public_state"] in {"aborted", "compromised", "dead"}:
        funding_action_html = ""

    body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/styles.css?v=7">
</head>
<body>
  {BETA_BANNER_HTML}
  <div class="container page">
    <header class="site-header">
      <a href="/" class="site-logo"><svg class="logo-mark" width="22" height="22" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="12" y="12" width="40" height="40" rx="5" stroke="currentColor" stroke-width="3.5"/><line x1="20" y1="24" x2="44" y2="24" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="32" x2="44" y2="32" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="40" x2="33" y2="40" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M35 44.5 L40 39 L45 44.5 L40 47.5 Z" fill="currentColor"/></svg>Fund Registry</a>
      <nav class="site-nav">
        <a href="/create">Create</a>
        <a href="/manage">Manage</a>
      </nav>
    </header>
    <div class="card" style="padding: 0; overflow: hidden;">
      <div class="status-bar {status_bar_class}"></div>
      <div style="padding: var(--space-lg);">
        <h1 style="margin-bottom: var(--space-sm);">{html.escape(page["title"])}</h1>
        <div class="page-meta">
          {meta_html}
        </div>
        {state_notice_html}
        {funding_action_html}
        <div class="tab-nav">
          <button class="active" onclick="switchTab(this,'tab-story')">Story</button>
          <button onclick="switchTab(this,'tab-links')">Links</button>
          <button onclick="switchTab(this,'tab-txns')">Transactions</button>
        </div>
        <div id="tab-story" class="tab-panel active">
          <div class="section" style="border-bottom:none;">
            {story_photo_html}
            <p>{html.escape(first_paragraph(page["description"]))}</p>
          </div>
          {progress_photo_html}
          {updates_html}
        </div>
        <div id="tab-links" class="tab-panel">
          {links_html if links_html else '<div class="section" style="border-bottom:none;"><p style="color:var(--text-3);font-size:0.875rem;">No links added.</p></div>'}
        </div>
        <div id="tab-txns" class="tab-panel">
          <div class="section" style="border-bottom:none;">
            {transaction_summary_html}
            {transaction_rows_html}
          </div>
        </div>
        {verification_html}
        {events_html}
        {share_html}
      </div>
    </div>
    <div class="trust-boundary">{html.escape(TRUST_BOUNDARY_COPY)} <a href="/how-it-works" style="color: var(--text-3);">Learn more</a></div>
    <footer class="site-footer">
      <a href="/">Fund Registry</a>
      <span class="meta-separator">·</span>
      <a href="/how-it-works">How it works</a>
      <span class="meta-separator">·</span>
      <a href="/terms.html">Terms</a>
      <span class="meta-separator">·</span>
      <a href="/terms.html#report">Report this page</a>
    </footer>
  </div>
  {overlay_html}
  <script>
  function switchTab(btn,id){{var nav=btn.parentElement;nav.querySelectorAll('button').forEach(function(b){{b.classList.remove('active')}});btn.classList.add('active');var card=nav.parentElement;card.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active')}});document.getElementById(id).classList.add('active')}}
  </script>
{CONTACT_BUBBLE_HTML}
</body>
</html>
"""
    return HTMLResponse(body)


def render_verify_page(page: dict[str, Any], verify_payload: dict[str, Any]) -> HTMLResponse:
    proof_record = verify_payload.get("proof_record")
    events = verify_payload.get("events") or []
    latest_anchor_event = verify_payload.get("latest_anchor_event")
    event_items = []
    for event in events:
        anchor_bits = ""
        if event.get("anchor_status"):
            anchor_bits = f" · {event['anchor_status']}"
        if event.get("anchor_txid"):
            anchor_bits += f" · txid {str(event['anchor_txid'])[:20]}…"
        if event.get("anchor_block_height"):
            anchor_bits += f" · block {event['anchor_block_height']}"
        event_items.append(
            f"""
            <div class="update-item">
              <div class="update-date">{html.escape(format_long_date(event["created_at"]))}</div>
              <div class="update-body">{html.escape(event["event_type"])}{html.escape(anchor_bits)}</div>
            </div>
            """
        )
    proof_block = '<div class="update-body">No wallet proof record is available for this page.</div>'
    if proof_record is not None:
        challenge = proof_record.get("challenge") if isinstance(proof_record, dict) else None
        challenge_block = ""
        if isinstance(challenge, dict):
            challenge_payload = challenge.get("challenge_payload")
            challenge_block = f"""
        <div class="section">
          <div class="section-title">Signed one-time challenge</div>
          <div style="font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.8; margin-bottom: var(--space-sm);">
            <div><strong>Challenge ID</strong>: {html.escape(challenge.get("id") or "Unknown")}</div>
            <div><strong>Challenge hash</strong>: {html.escape(challenge.get("challenge_hash") or "Unknown")}</div>
            <div><strong>Challenge expires</strong>: {html.escape(format_long_date(challenge.get("expires_at")))}</div>
          </div>
          <pre style="white-space: pre-wrap; word-break: break-word; font-size: 0.8125rem; color: var(--text-2);">{html.escape(json.dumps(challenge_payload, indent=2, sort_keys=True) if isinstance(challenge_payload, dict) else challenge.get("challenge_text") or "Unknown")}</pre>
        </div>
        """
        proof_block = f"""
        <div class="card" style="font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.8; margin-bottom: var(--space-lg);">
          <div><strong>Verification code</strong>: {html.escape(verify_payload["verification_code"] or "Unknown")}</div>
          <div><strong>Address fingerprint</strong>: {html.escape(verify_payload["address_fingerprint"] or "Unknown")}</div>
          <div><strong>Proof status</strong>: {html.escape(verify_payload["proof_status"])}</div>
          <div><strong>Signature method</strong>: {html.escape(proof_method_display_name(proof_record.get("signature_method") or "Unknown"))}</div>
          <div><strong>Payload hash</strong>: {html.escape(proof_record.get("payload_hash") or "Unknown")}</div>
          <div><strong>Verified at</strong>: {html.escape(format_long_date(proof_record.get("verified_at")))}</div>
        </div>
        <div class="section">
          <div class="section-title">Canonical proof payload</div>
          <pre style="white-space: pre-wrap; word-break: break-word; font-size: 0.8125rem; color: var(--text-2);">{html.escape(json.dumps(proof_record["payload"], indent=2, sort_keys=True))}</pre>
        </div>
        {challenge_block}
        """
    latest_anchor_block = ""
    latest_anchor_receipt = latest_anchor_event.get("anchor_receipt") if isinstance(latest_anchor_event, dict) else None
    if isinstance(latest_anchor_event, dict) and latest_anchor_event.get("anchor_status"):
        anchor_timestamp = latest_anchor_event.get("anchor_confirmed_at") or latest_anchor_event.get("anchor_broadcast_at")
        receipt_block = ""
        if isinstance(latest_anchor_receipt, dict):
            receipt_block = f"""
        <div class="section" style="margin-top: var(--space-md);">
          <div class="section-title">FRG1 receipt</div>
          <div style="font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.8; margin-bottom: var(--space-sm);">
            <div><strong>Event code</strong>: {html.escape(str(latest_anchor_receipt.get("event_code_hex") or latest_anchor_receipt.get("event_code") or "Unknown"))}</div>
            <div><strong>Hash algorithm</strong>: {html.escape(str(latest_anchor_receipt.get("hash_algorithm") or "Unknown"))} ({html.escape(str(latest_anchor_receipt.get("hash_algorithm_code_hex") or latest_anchor_receipt.get("hash_algorithm_code") or "Unknown"))})</div>
            <div><strong>Digest</strong>: {html.escape(str(latest_anchor_receipt.get("digest_hex") or "Unknown"))}</div>
          </div>
          <pre style="white-space: pre-wrap; word-break: break-word; font-size: 0.8125rem; color: var(--text-2);">{html.escape(str(latest_anchor_receipt.get("op_return_hex") or "Unknown"))}</pre>
        </div>
        """
        latest_anchor_block = f"""
      <div class="section">
        <div class="section-title">Latest Bitcoin anchor</div>
        <div class="card" style="font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.8;">
          <div><strong>Anchor status</strong>: {html.escape(str(latest_anchor_event.get("anchor_status") or "Unknown"))}</div>
          <div><strong>Event type</strong>: {html.escape(str(latest_anchor_event.get("event_type") or "Unknown"))}</div>
          <div><strong>Anchor txid</strong>: {html.escape(str(latest_anchor_event.get("anchor_txid") or "Pending"))}</div>
          <div><strong>Block height</strong>: {html.escape(str(latest_anchor_event.get("anchor_block_height") or "Pending"))}</div>
          <div><strong>Block hash</strong>: {html.escape(str(latest_anchor_event.get("anchor_block_hash") or "Pending"))}</div>
          <div><strong>Anchored at</strong>: {html.escape(format_long_date(anchor_timestamp))}</div>
          <div><strong>Receipt format</strong>: {html.escape(str(latest_anchor_receipt.get("format") if isinstance(latest_anchor_receipt, dict) else ANCHOR_RECEIPT_FORMAT))}</div>
        </div>
        {receipt_block}
      </div>
      """
    body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(page["title"])} Verification{PAGE_TITLE_SUFFIX}</title>
  <link rel="stylesheet" href="/styles.css?v=7">
</head>
<body>
  {BETA_BANNER_HTML}
  <div class="container page">
    <header class="site-header">
      <a href="/" class="site-logo"><svg class="logo-mark" width="22" height="22" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="12" y="12" width="40" height="40" rx="5" stroke="currentColor" stroke-width="3.5"/><line x1="20" y1="24" x2="44" y2="24" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="32" x2="44" y2="32" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="40" x2="33" y2="40" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M35 44.5 L40 39 L45 44.5 L40 47.5 Z" fill="currentColor"/></svg>Fund Registry</a>
      <nav class="site-nav">
        <a href="{html.escape(page['canonical_url'])}">Campaign page</a>
      </nav>
    </header>
    <div class="card">
      <h1 style="margin-bottom: var(--space-sm);">Verification record</h1>
      <p style="color: var(--text-2); margin-bottom: var(--space-lg);">
        This verifies control of the listed Bitcoin wallet, not identity or campaign claims.
      </p>
      <div class="section">
        <div class="section-title">Current state</div>
        <div class="update-body">{html.escape(verify_payload["current_funding_state"])}</div>
      </div>
      <div class="section">
        <div class="section-title">Funding destination</div>
        <div class="address-box"><span>{html.escape(verify_payload["btc_address"])}</span></div>
      </div>
      {proof_block}
      {latest_anchor_block}
      <div class="section">
        <div class="section-title">Lifecycle events</div>
        {''.join(event_items) or '<div class="update-body">No public events have been recorded yet.</div>'}
      </div>
      <div class="section">
        <div class="section-title">Proof bundle</div>
        <a class="btn btn-secondary" href="/v1/pages/{html.escape(page['id'])}/proof-bundle">Download JSON bundle</a>
      </div>
    </div>
    <div class="trust-boundary">{html.escape(verify_payload["disclosure"])}</div>
  </div>
{CONTACT_BUBBLE_HTML}
</body>
</html>
"""
    return HTMLResponse(body)


def render_lookup_error_page(query: str, detail: str, *, status_code: int) -> HTMLResponse:
    body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lookup Error{PAGE_TITLE_SUFFIX}</title>
  <link rel="stylesheet" href="/styles.css?v=7">
</head>
<body>
  {BETA_BANNER_HTML}
  <div class="container page">
    <header class="site-header">
      <a href="/" class="site-logo"><svg class="logo-mark" width="22" height="22" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="12" y="12" width="40" height="40" rx="5" stroke="currentColor" stroke-width="3.5"/><line x1="20" y1="24" x2="44" y2="24" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="32" x2="44" y2="32" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="40" x2="33" y2="40" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M35 44.5 L40 39 L45 44.5 L40 47.5 Z" fill="currentColor"/></svg>Fund Registry</a>
      <nav class="site-nav">
        <a href="/create">Create</a>
        <a href="/manage">Manage</a>
      </nav>
    </header>
    <div class="card">
      <h1 style="margin-bottom: var(--space-sm);">Lookup not found</h1>
      <p style="color: var(--text-2); margin-bottom: var(--space-lg);">
        Fund Registry could not resolve this lookup.
      </p>
      <div class="section">
        <div class="section-title">Query</div>
        <div class="address-box"><span>{html.escape(query or "Unknown")}</span></div>
      </div>
      <div class="warning" style="margin-top: var(--space-md);">
        <span class="warning-icon">i</span>
        <div>{html.escape(detail)}</div>
      </div>
      <form class="search-form" action="/lookup" method="get" style="margin-top: var(--space-lg);">
        <input type="text" class="search-input" name="q" value="{html.escape(query)}" placeholder="Enter verification code, address, or Fund Registry URL">
        <button type="submit" class="search-btn">Look up</button>
      </form>
    </div>
    <div class="trust-boundary">This search helps locate Fund Registry records. It does not verify campaign truth, identity, or donor safety.</div>
  </div>
{CONTACT_BUBBLE_HTML}
</body>
</html>
"""
    return HTMLResponse(body, status_code=status_code)


def render_address_reader_page(address_payload: dict[str, Any]) -> HTMLResponse:
    records = address_payload.get("records") or []
    summary = (
        f"{address_payload['record_count']} record(s) · "
        f"{address_payload['active_count']} active · "
        f"{address_payload['historical_count']} historical"
    )
    record_cards = []
    for record in records:
        anchor_line = "No Bitcoin anchor on record."
        if record.get("anchor_txid"):
            anchor_line = f"{record.get('anchor_status') or 'anchor'} · txid {str(record['anchor_txid'])[:20]}…"
            if record.get("anchor_block_height"):
                anchor_line += f" · block {record['anchor_block_height']}"
        history_line = (
            "Historical proof exists for this record."
            if record.get("historical_proof_exists")
            else "No historical proof record is attached to this record."
        )
        record_cards.append(
            f"""
            <div class="card" style="margin-bottom: var(--space-md);">
              <h2 style="margin-bottom: var(--space-sm);">{html.escape(record['title'])}</h2>
              <div class="page-meta">
                <span class="meta-item">{html.escape(record['tier'])}</span>
                <span class="meta-item">{html.escape(record['current_funding_state'])}</span>
                <span class="meta-item">{html.escape(record.get('proof_status') or 'unknown')}</span>
                <span class="meta-item mono">{html.escape(record.get('verification_code') or 'Unknown code')}</span>
              </div>
              <div class="section" style="margin-top: var(--space-md);">
                <div class="section-title">Registry evidence</div>
                <div class="update-item">
                  <div class="update-date">Address fingerprint</div>
                  <div class="update-body mono">{html.escape(record.get('address_fingerprint') or 'Unknown')}</div>
                </div>
                <div class="update-item">
                  <div class="update-date">Historical proof</div>
                  <div class="update-body">{html.escape(history_line)}</div>
                </div>
                <div class="update-item">
                  <div class="update-date">Bitcoin anchor</div>
                  <div class="update-body">{html.escape(anchor_line)}</div>
                </div>
              </div>
              <div style="display: flex; gap: var(--space-sm); flex-wrap: wrap; margin-top: var(--space-md);">
                <a class="btn btn-primary" href="{html.escape(record['canonical_url'])}">Open campaign</a>
                <a class="btn btn-secondary" href="{html.escape(record['verify_url'])}">Open verification</a>
                <a class="btn btn-secondary" href="/v1/pages/{html.escape(record['page_id'])}/proof-bundle">Download proof bundle</a>
              </div>
            </div>
            """
        )
    if not record_cards:
        record_cards.append(
            """
            <div class="card">
              <h2 style="margin-bottom: var(--space-sm);">No Fund Registry record found</h2>
              <p style="color: var(--text-2); margin-bottom: var(--space-sm);">
                No Fund Registry record was found for this exact Bitcoin address.
              </p>
              <p style="color: var(--text-2); margin: 0;">
                Absence from Fund Registry does not imply fraud. It only means we have no matching registry record for this exact address.
              </p>
            </div>
            """
        )
    body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Address Reader{PAGE_TITLE_SUFFIX}</title>
  <link rel="stylesheet" href="/styles.css?v=7">
</head>
<body>
  {BETA_BANNER_HTML}
  <div class="container page">
    <header class="site-header">
      <a href="/" class="site-logo"><svg class="logo-mark" width="22" height="22" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="12" y="12" width="40" height="40" rx="5" stroke="currentColor" stroke-width="3.5"/><line x1="20" y1="24" x2="44" y2="24" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="32" x2="44" y2="32" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><line x1="20" y1="40" x2="33" y2="40" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M35 44.5 L40 39 L45 44.5 L40 47.5 Z" fill="currentColor"/></svg>Fund Registry</a>
      <nav class="site-nav">
        <a href="/create">Create</a>
        <a href="/manage">Manage</a>
      </nav>
    </header>
    <div class="card" style="margin-bottom: var(--space-lg);">
      <h1 style="margin-bottom: var(--space-sm);">Address reader</h1>
      <p style="color: var(--text-2); margin-bottom: var(--space-lg);">
        Check whether this exact Bitcoin address appears in Fund Registry records and inspect the proof state tied to those records.
      </p>
      <div class="section">
        <div class="section-title">Bitcoin address</div>
        <div class="address-box"><span>{html.escape(address_payload['btc_address'])}</span></div>
      </div>
      <div class="section">
        <div class="section-title">Registry summary</div>
        <div class="update-body">{html.escape(summary)}</div>
      </div>
      <form class="search-form" action="/lookup" method="get" style="margin-top: var(--space-lg);">
        <input type="text" class="search-input" name="q" value="{html.escape(address_payload['btc_address'])}" placeholder="Enter verification code, address, or Fund Registry URL">
        <button type="submit" class="search-btn">Look up</button>
      </form>
    </div>
    {''.join(record_cards)}
    <div class="trust-boundary">{html.escape(address_payload['disclosure'])}</div>
  </div>
{CONTACT_BUBBLE_HTML}
</body>
</html>
"""
    return HTMLResponse(body)


def static_file_response(filename: str) -> FileResponse:
    if filename not in STATIC_HTML_FILES and filename not in STATIC_JS_FILES and filename != "styles.css":
        raise HTTPException(status_code=404, detail="Static asset not found.")
    return FileResponse(STATIC_DIR / filename)


def build_settings() -> FundRegistrySettings:
    cors_raw = os.environ.get("FUND_REGISTRY_CORS_ORIGINS", "")
    cors_origins = [value.strip() for value in cors_raw.split(",") if value.strip()]
    allowed_hosts_raw = os.environ.get("FUND_REGISTRY_ALLOWED_HOSTS", "")
    allowed_hosts = [value.strip() for value in allowed_hosts_raw.split(",") if value.strip()]
    static_dir = Path(os.environ.get("FUND_REGISTRY_STATIC_DIR", str(STATIC_DIR)))
    bitcoin_ssh_host = normalize_optional_text(os.environ.get("BITCOIN_SSH_HOST"))
    bitcoin_backend, bitcoin_backend_source = normalize_bitcoin_backend(
        os.environ.get("BITCOIN_BACKEND"),
        ssh_host=bitcoin_ssh_host,
    )
    return FundRegistrySettings(
        db_path=Path(os.environ.get("FUND_REGISTRY_DB_PATH", str(DEFAULT_DB_PATH))),
        static_dir=static_dir,
        photo_dir=Path(os.environ.get("FUND_REGISTRY_PHOTO_DIR", str(DATA_DIR / "story-photos"))),
        transaction_cache_dir=Path(
            os.environ.get("FUND_REGISTRY_TRANSACTION_CACHE_DIR", str(DATA_DIR / "tx-cache"))
        ),
        messages_path=Path(os.environ.get("FUND_REGISTRY_MESSAGES_PATH", str(DATA_DIR / "messages.jsonl"))),
        public_base_url=canonicalize_base_url(os.environ.get("FUND_REGISTRY_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL)),
        mempool_base_url=canonicalize_base_url(
            os.environ.get("FUND_REGISTRY_MEMPOOL_BASE_URL", DEFAULT_MEMPOOL_BASE_URL)
        ),
        transaction_cache_ttl_seconds=int(
            os.environ.get("FUND_REGISTRY_TRANSACTION_CACHE_TTL_SECONDS", str(DEFAULT_TX_CACHE_TTL_SECONDS))
        ),
        cors_origins=cors_origins,
        allowed_hosts=allowed_hosts or DEFAULT_ALLOWED_HOSTS.copy(),
        allow_dev_actions=env_flag("FUND_REGISTRY_ALLOW_DEV_ACTIONS", default=False),
        payment_mode=normalize_mode(
            os.environ.get("FUND_REGISTRY_PAYMENT_MODE", "disabled"),
            allowed={"disabled", "mock", "bitcoin-core"},
        ),
        proof_mode=normalize_mode(
            os.environ.get("FUND_REGISTRY_PROOF_MODE", "disabled"),
            allowed={"disabled", "mock", "bitcoin-message", "mixed"},
        ),
        anchor_mode=normalize_mode(
            os.environ.get("FUND_REGISTRY_ANCHOR_MODE", "disabled"),
            allowed={"disabled", "mock", "bitcoin-core"},
        ),
        sats_per_usd=int(os.environ.get("FUND_REGISTRY_SATS_PER_USD", str(DEFAULT_SATS_PER_USD))),
        tier2_amount_sats_override=parse_optional_positive_int(os.environ.get("FUND_REGISTRY_TIER2_AMOUNT_SATS")),
        tier3_amount_sats_override=parse_optional_positive_int(os.environ.get("FUND_REGISTRY_TIER3_AMOUNT_SATS")),
        request_timeout_seconds=float(
            os.environ.get("FUND_REGISTRY_REQUEST_TIMEOUT_SECONDS", str(DEFAULT_REQUEST_TIMEOUT_SECONDS))
        ),
        payments_paused=env_flag("FUND_REGISTRY_PAYMENTS_PAUSED", default=False),
        payment_details_redacted=env_flag("FUND_REGISTRY_PAYMENT_UI_REDACTED", default=False),
        messages_admin_token=normalize_optional_text(os.environ.get("FUND_REGISTRY_MESSAGES_ADMIN_TOKEN")),
        bitcoin_cli_path=os.environ.get("BITCOIN_CLI_PATH", "bitcoin-cli"),
        bitcoin_conf_path=os.environ.get("BITCOIN_CONF_PATH", ""),
        bitcoin_wallet_name=os.environ.get("BITCOIN_WALLET_NAME", DEFAULT_BITCOIN_WALLET_NAME),
        payment_wallet_name=os.environ.get(
            "FUND_REGISTRY_PAYMENT_WALLET_NAME",
            DEFAULT_PAYMENT_WALLET_NAME,
        ),
        bitcoin_backend=bitcoin_backend,
        bitcoin_backend_source=bitcoin_backend_source,
        bitcoin_ssh_host=bitcoin_ssh_host,
        payment_confirmation_target=int(
            os.environ.get(
                "FUND_REGISTRY_PAYMENT_CONFIRMATION_TARGET",
                str(PAYMENT_CONFIRMATION_TARGET),
            )
        ),
        payment_expiry_minutes=int(
            os.environ.get(
                "FUND_REGISTRY_PAYMENT_EXPIRY_MINUTES",
                str(INVOICE_EXPIRY_MINUTES),
            )
        ),
    )


def create_app(settings: Optional[FundRegistrySettings] = None) -> FastAPI:
    settings = settings or build_settings()
    settings.static_dir.mkdir(parents=True, exist_ok=True)
    if not settings.csp_policy or not settings.route_csp_policies:
        settings.csp_policy, settings.route_csp_policies = build_csp_policies(settings.static_dir)
    store = FundRegistryStore(settings)
    rate_limiter = InMemoryRateLimiter(RATE_LIMIT_RULES)
    # AI-first directive: /docs, /redoc, and /openapi.json are deliberate discovery surfaces.
    app = FastAPI(title="Fund Registry API", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.state.anchor_preflight_payload = store.anchor_preflight_payload
    app.state.payment_preflight_payload = store.payment_preflight_payload
    messages_admin_token = APIKeyHeader(
        name=MESSAGES_ADMIN_TOKEN_HEADER,
        scheme_name="FundRegistryMessagesAdminToken",
        description="Admin token required for contact-message read-state mutations.",
        auto_error=False,
    )
    if settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        client_ip = request_client_ip(request)
        rule = rate_limiter.match(request.method.upper(), request.url.path)
        if rule is not None:
            retry_after = rate_limiter.check(rule, client_ip)
            if retry_after is not None:
                security_log(
                    "rate_limit_exceeded",
                    outcome="blocked",
                    ip=client_ip,
                    method=request.method.upper(),
                    path=request.url.path,
                    rule=rule.name,
                    retry_after=retry_after,
                )
                return JSONResponse(
                    {"detail": "Too many requests. Please slow down and try again."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        is_https_request = request.url.scheme == "https" or forwarded_proto == "https"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
        response.headers.setdefault("X-Frame-Options", "DENY")
        corp_value = "same-origin"
        if request.url.path.startswith("/badge/") or request.url.path.startswith("/story-photo/") or request.url.path.startswith("/progress-photo/"):
            corp_value = "cross-origin"
        response.headers.setdefault("Cross-Origin-Resource-Policy", corp_value)
        if is_https_request:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        if content_type.startswith("text/html"):
            response.headers.setdefault(
                "Content-Security-Policy",
                settings.route_csp_policies.get(request.url.path, settings.csp_policy),
            )
        return response

    def authenticate_campaign_action(
        request: Request,
        payload: CampaignKeyPayload,
        *,
        action: str,
        expected_page_id: Optional[str] = None,
    ) -> dict[str, Any]:
        client_ip = request_client_ip(request)
        try:
            page = store.authenticate_campaign_key(payload)
        except HTTPException as exc:
            log_fields = {
                "event_type": "campaign_key_auth",
                "outcome": "failure",
                "action": action,
                "ip": client_ip,
                "path": request.url.path,
                "page_id": payload.page_id,
                "key_id": payload.key_id,
                "status_code": exc.status_code,
                "reason": str(exc.detail),
            }
            reason_code = getattr(exc, "reason_code", None)
            if reason_code:
                log_fields["reason_code"] = reason_code
            security_log(
                log_fields.pop("event_type"),
                **log_fields,
            )
            raise
        if expected_page_id is not None and page["id"] != expected_page_id:
            security_log(
                "campaign_key_auth",
                outcome="failure",
                action=action,
                ip=client_ip,
                path=request.url.path,
                page_id=payload.page_id,
                key_id=payload.key_id,
                status_code=403,
                reason="Campaign Key does not match this page.",
            )
            raise HTTPException(status_code=403, detail="Campaign Key does not match this page.")
        security_log(
            "campaign_key_auth",
            outcome="success",
            action=action,
            ip=client_ip,
            path=request.url.path,
            page_id=page["id"],
            key_id=payload.key_id,
        )
        return page

    def verify_challenge_action(
        request: Request,
        *,
        challenge_id: str,
        proof: str,
        page_id: Optional[str] = None,
        action: str,
    ) -> dict[str, Any]:
        client_ip = request_client_ip(request)
        try:
            payload = store.verify_challenge(challenge_id, proof)
        except HTTPException as exc:
            challenge = store.get_challenge_by_id(challenge_id)
            security_log(
                "proof_verify",
                outcome="failure",
                action=action,
                ip=client_ip,
                path=request.url.path,
                challenge_id=challenge_id,
                page_id=page_id or (challenge or {}).get("page_id"),
                status_code=exc.status_code,
                reason=str(exc.detail),
            )
            raise
        security_log(
            "proof_verify",
            outcome="success",
            action=action,
            ip=client_ip,
            path=request.url.path,
            challenge_id=challenge_id,
            page_id=payload["page"]["id"],
            proof_method=(payload.get("challenge") or {}).get("proof_method"),
        )
        return payload

    def require_messages_admin_token(
        request: Request,
        provided_token: Optional[str] = Security(messages_admin_token),
    ) -> None:
        client_ip = request_client_ip(request)
        expected = str(settings.messages_admin_token or "").strip()
        if not expected:
            security_log(
                "messages_admin_auth",
                outcome="disabled",
                ip=client_ip,
                method=request.method.upper(),
                path=request.url.path,
                status_code=503,
            )
            raise HTTPException(status_code=503, detail="Message admin controls are disabled.")
        presented = str(provided_token or "").strip()
        if not presented:
            security_log(
                "messages_admin_auth",
                outcome="failure",
                ip=client_ip,
                method=request.method.upper(),
                path=request.url.path,
                status_code=401,
                reason="missing_token",
            )
            raise HTTPException(status_code=401, detail="Admin token is required.")
        if not secrets.compare_digest(presented, expected):
            security_log(
                "messages_admin_auth",
                outcome="failure",
                ip=client_ip,
                method=request.method.upper(),
                path=request.url.path,
                status_code=403,
                reason="invalid_token",
            )
            raise HTTPException(status_code=403, detail="Invalid admin token.")
        security_log(
            "messages_admin_auth",
            outcome="success",
            ip=client_ip,
            method=request.method.upper(),
            path=request.url.path,
            status_code=200,
        )

    @app.get("/api/health")
    @app.get("/v1/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "fund-registry",
                "payment_mode": settings.payment_mode,
                "payments_paused": settings.payments_paused,
                "payment_details_redacted": settings.payment_details_redacted,
                "proof_mode": settings.proof_mode,
                "anchor_mode": settings.anchor_mode,
                "amounts": {
                    "sats_per_usd": settings.sats_per_usd,
                    "tier2_amount_sats_override": settings.tier2_amount_sats_override,
                    "tier3_amount_sats_override": settings.tier3_amount_sats_override,
                },
                "payment_backend": store.payment_backend_summary(),
                "anchor_backend": store.anchor_backend_summary(),
                "paid_activation_available": settings.allow_dev_actions
                or (settings.payment_mode != "disabled" and settings.proof_mode != "disabled"),
                "generated_at": utc_isoformat(settings.now_fn()),
            }
        )

    @app.get("/")
    def index() -> FileResponse:
        return static_file_response("index.html")

    @app.get("/lookup")
    def lookup(q: str) -> Response:
        query = normalize_search_query(q)
        if validate_bitcoin_address(query):
            return RedirectResponse(f"/address/{quote(query, safe='')}", status_code=302)
        try:
            result = store.search_page(query)
        except HTTPException as exc:
            if exc.status_code in {400, 404}:
                return render_lookup_error_page(query, str(exc.detail), status_code=exc.status_code)
            raise
        page = result["page"]
        redirect_url = page["canonical_url"]
        if result["resolved_by"] == "verification_code":
            redirect_url = page["verify_url"]
        return RedirectResponse(redirect_url, status_code=302)

    @app.get("/create")
    def create_page_view() -> FileResponse:
        return static_file_response("create.html")

    @app.get("/renew")
    def renew_view() -> FileResponse:
        return static_file_response("renew.html")

    @app.get("/how-verification-works")
    @app.get("/how-it-works.html")
    def how_it_works() -> FileResponse:
        return static_file_response("how-it-works.html")

    @app.get("/campaign-key")
    def campaign_key_view() -> FileResponse:
        return static_file_response("campaign-key.html")

    @app.get("/styles.css")
    def styles() -> FileResponse:
        return static_file_response("styles.css")

    @app.get("/robots.txt")
    def robots_txt() -> FileResponse:
        return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml")
    def sitemap_xml() -> FileResponse:
        return FileResponse(STATIC_DIR / "sitemap.xml", media_type="application/xml")

    @app.get("/assets/{filename}")
    def asset_file(filename: str) -> FileResponse:
        return static_file_response(filename)

    @app.get("/create.html")
    def create_html() -> FileResponse:
        return static_file_response("create.html")

    @app.get("/campaign-key.html")
    def campaign_key_html() -> FileResponse:
        return static_file_response("campaign-key.html")

    @app.get("/renew.html")
    def renew_html() -> FileResponse:
        return static_file_response("renew.html")

    @app.get("/fund-free.html")
    def fund_free_html() -> FileResponse:
        return static_file_response("fund-free.html")

    @app.get("/fund-badge.html")
    def fund_badge_html() -> FileResponse:
        return static_file_response("fund-badge.html")

    @app.get("/fund-vanity.html")
    def fund_vanity_html() -> FileResponse:
        return static_file_response("fund-vanity.html")

    @app.get("/fund-expired.html")
    def fund_expired_html() -> FileResponse:
        return static_file_response("fund-expired.html")

    @app.get("/fund-tombstone.html")
    def fund_tombstone_html() -> FileResponse:
        return static_file_response("fund-tombstone.html")

    @app.get("/terms.html")
    def terms_html() -> FileResponse:
        return static_file_response("terms.html")

    @app.get("/how-it-works.html")
    def how_it_works_html() -> FileResponse:
        return static_file_response("how-it-works.html")

    @app.get("/how-it-works")
    def how_it_works_view() -> FileResponse:
        return static_file_response("how-it-works.html")

    @app.get("/manage")
    def manage_view() -> FileResponse:
        return static_file_response("manage.html")

    @app.get("/manage.html")
    def manage_html() -> FileResponse:
        return static_file_response("manage.html")

    @app.get("/fund/{page_ref}")
    def public_page(page_ref: str) -> HTMLResponse:
        page = store.get_page_by_ref(page_ref)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Funding page not found.")
        return render_public_page(page, settings=settings)

    @app.get("/verify/{page_ref}")
    def public_verify_page(page_ref: str) -> HTMLResponse:
        page = store.get_page_by_ref(page_ref)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Verification record not found.")
        return render_verify_page(page, store._verify_payload(page))

    @app.get("/address/{btc_address}")
    def public_address_reader(btc_address: str) -> HTMLResponse:
        try:
            payload = store.address_records_payload(btc_address)
        except HTTPException as exc:
            if exc.status_code in {400, 404}:
                return render_lookup_error_page(btc_address, str(exc.detail), status_code=exc.status_code)
            raise
        return render_address_reader_page(payload)

    @app.get("/story-photo/{page_id}")
    def story_photo(page_id: str) -> FileResponse:
        target_path, media_type = store.story_photo_file(page_id)
        return FileResponse(target_path, media_type=media_type)

    @app.get("/progress-photo/{page_id}")
    def progress_photo(page_id: str) -> FileResponse:
        target_path, media_type = store.progress_photo_file(page_id)
        return FileResponse(target_path, media_type=media_type)

    @app.get("/badge/{page_ref}.svg")
    def badge_svg(page_ref: str) -> Response:
        page = store.get_page_by_ref(page_ref)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Badge not found.")
        if page["public_state"] == "tombstoned":
            return Response(status_code=410)
        if not is_paid_tier(page["tier"]):
            raise HTTPException(status_code=404, detail="Free pages do not expose badges.")
        return Response(
            render_badge_svg(page),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store, no-cache, max-age=0, must-revalidate"},
        )

    @app.post("/v1/pages")
    def create_page(request: CreatePageRequest) -> JSONResponse:
        payload = store.create_page(request)
        return JSONResponse(payload)

    @app.post("/v1/messages")
    async def submit_message(request: Request) -> JSONResponse:
        client_ip = request_client_ip(request)
        source_host = request_source_host(request)
        try:
            raw_body = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
        payload = normalize_message_request_payload(raw_body)
        if payload.website:
            security_log(
                "contact_message",
                outcome="honeypot",
                ip=client_ip,
                path=request.url.path,
                message_length=len(payload.message or ""),
                has_email=bool(payload.email),
                has_page_url=bool(payload.page_url),
                source_host=source_host,
            )
            return JSONResponse({"status": "ok"})
        try:
            store.store_contact_message(
                message=payload.message or "",
                email=payload.email,
                page_url=payload.page_url,
                ip_prefix=anonymize_stored_client_ip(client_ip),
                source_host=source_host,
            )
        except OSError as exc:
            security_log(
                "contact_message",
                outcome="failure",
                ip=client_ip,
                path=request.url.path,
                message_length=len(payload.message or ""),
                has_email=bool(payload.email),
                has_page_url=bool(payload.page_url),
                source_host=source_host,
                status_code=503,
                reason=type(exc).__name__,
            )
            raise HTTPException(status_code=503, detail="Unable to accept message right now.") from exc
        security_log(
            "contact_message",
            outcome="success",
            ip=client_ip,
            path=request.url.path,
            message_length=len(payload.message or ""),
            has_email=bool(payload.email),
            has_page_url=bool(payload.page_url),
            source_host=source_host,
        )
        return JSONResponse({"status": "ok"})

    @app.put("/v1/messages/read-all")
    def mark_all_messages_read(_authorized: None = Depends(require_messages_admin_token)) -> JSONResponse:
        count = store.mark_all_messages_read()
        return JSONResponse({"status": "ok", "marked": count})

    @app.put("/v1/messages/{message_id}/read")
    def mark_message_read(
        message_id: str,
        _authorized: None = Depends(require_messages_admin_token),
    ) -> JSONResponse:
        updated = store.mark_message_read(message_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        return JSONResponse(updated)

    @app.get("/v1/stats")
    def stats() -> JSONResponse:
        return JSONResponse(store.stats_payload(), headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/v1/search")
    def search_pages(q: str) -> JSONResponse:
        return JSONResponse(store.search_page(q))

    @app.get("/v1/addresses/{btc_address}/records")
    def address_records(btc_address: str) -> JSONResponse:
        return JSONResponse(store.address_records_payload(btc_address))

    @app.get("/v1/pages/{page_id}")
    def get_page(page_id: str) -> JSONResponse:
        page = store.get_page_by_id(page_id)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Page not found.")
        return JSONResponse(page)

    @app.get("/v1/pages/ref/{page_ref}")
    def get_page_by_ref(page_ref: str) -> JSONResponse:
        page = store.get_page_by_ref(page_ref)
        if page is None or page["public_state"] == "deleted":
            raise HTTPException(status_code=404, detail="Page not found.")
        return JSONResponse(page)

    @app.post("/v1/pages/manage")
    def manage_page(request: Request, body: ManagePageRequest) -> JSONResponse:
        page = authenticate_campaign_action(request, body.campaign_key, action="manage")
        return JSONResponse(store.manage_page_payload(page["id"]))

    @app.post("/v1/pages/{page_id}/links")
    def update_page_links(page_id: str, request: Request, body: PageLinksUpdateRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="links_update", expected_page_id=page_id)
        updated_page = store.update_links(page_id, body.links)
        return JSONResponse({"page": updated_page})

    @app.post("/v1/promo/validate")
    def validate_promo(request: Request, body: PromoCodeValidateRequest) -> JSONResponse:
        page = authenticate_campaign_action(request, body.campaign_key, action="promo_validate")
        return JSONResponse(
            store.validate_promo_code(
                page_id=page["id"],
                code=body.code,
                target_tier=body.target_tier,
                vanity_slug=body.vanity_slug,
            )
        )

    @app.post("/v1/pages/{page_id}/updates")
    def add_update(page_id: str, request: Request, body: PageUpdateRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="post_update", expected_page_id=page_id)
        update = store.add_update(page_id, body.body)
        return JSONResponse({"update": update, "page": store.get_page_by_id(page_id)})

    @app.post("/v1/pages/{page_id}/photo")
    def upload_story_photo(page_id: str, request: Request, body: StoryPhotoUploadRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="story_photo_upload", expected_page_id=page_id)
        updated_page = store.save_story_photo(
            page_id=page_id,
            content=decode_base64_bytes(body.image_base64),
            content_type=body.content_type,
        )
        return JSONResponse({"page": updated_page})

    @app.post("/v1/pages/{page_id}/progress-photo")
    def upload_progress_photo(page_id: str, request: Request, body: ProgressPhotoUploadRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="progress_photo_upload", expected_page_id=page_id)
        updated_page = store.save_progress_photo(
            page_id=page_id,
            content=decode_base64_bytes(body.image_base64),
            content_type=body.content_type,
        )
        return JSONResponse({"page": updated_page})

    @app.post("/v1/pages/{page_id}/promo/apply")
    def apply_promo(page_id: str, request: Request, body: PromoCodeApplyRequest) -> JSONResponse:
        page = authenticate_campaign_action(request, body.campaign_key, action="promo_apply", expected_page_id=page_id)
        try:
            payload = store.apply_promo_code(
                page_id=page_id,
                code=body.code,
                target_tier=body.target_tier,
                vanity_slug=body.vanity_slug,
            )
        except HTTPException as exc:
            security_log(
                "promo_apply",
                outcome="failure",
                ip=request_client_ip(request),
                path=request.url.path,
                page_id=page["id"],
                target_tier=body.target_tier,
                code_hash=sha256_hex(body.code.strip())[:12],
                status_code=exc.status_code,
                reason=str(exc.detail),
            )
            raise
        security_log(
            "promo_apply",
            outcome="success",
            ip=request_client_ip(request),
            path=request.url.path,
            page_id=page["id"],
            target_tier=body.target_tier,
            code_hash=sha256_hex(body.code.strip())[:12],
        )
        return JSONResponse(payload)

    @app.post("/v1/pages/{page_id}/upgrade")
    def upgrade_page(page_id: str, request: Request, body: UpgradePageRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="upgrade", expected_page_id=page_id)
        payment = store.create_payment_intent(
            page_id=page_id,
            purpose="upgrade",
            target_tier=body.target_tier,
            optional_vanity_slug=body.vanity_slug,
        )
        return JSONResponse({"payment_intent": payment, "page": store.get_page_by_id(page_id)})

    @app.post("/v1/pages/{page_id}/renew")
    def renew_page(page_id: str, request: Request, body: RenewPageRequest) -> JSONResponse:
        page = authenticate_campaign_action(request, body.campaign_key, action="renew", expected_page_id=page_id)
        if page["tier"] == "free":
            raise HTTPException(status_code=400, detail="Free pages cannot be renewed.")
        payment = store.create_payment_intent(
            page_id=page_id,
            purpose="renew",
            target_tier=page["tier"],
        )
        return JSONResponse({"payment_intent": payment, "page": store.get_page_by_id(page_id)})

    @app.post("/v1/pages/{page_id}/verify")
    def prepare_manage_verify(page_id: str, request: Request, body: PageProofPrepareRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="verify_prepare", expected_page_id=page_id)
        return JSONResponse(store.prepare_manage_verification(page_id))

    @app.post("/v1/pages/{page_id}/proof/prepare")
    def prepare_page_proof(page_id: str, request: Request, body: PageProofPrepareRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="proof_prepare", expected_page_id=page_id)
        return JSONResponse(store.prepare_proof(page_id))

    @app.post("/v1/pages/{page_id}/proof/verify")
    def verify_page_proof(page_id: str, request: Request, body: PageProofVerifyRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="proof_verify", expected_page_id=page_id)
        challenge = store.get_challenge_by_id(body.challenge_id)
        if challenge is None or challenge["page_id"] != page_id:
            security_log(
                "proof_verify",
                outcome="failure",
                action="proof_verify",
                ip=request_client_ip(request),
                path=request.url.path,
                challenge_id=body.challenge_id,
                page_id=page_id,
                status_code=404,
                reason="Proof challenge not found.",
            )
            raise HTTPException(status_code=404, detail="Proof challenge not found.")
        return JSONResponse(
            verify_challenge_action(
                request,
                challenge_id=body.challenge_id,
                proof=body.proof,
                page_id=page_id,
                action="proof_verify",
            )
        )

    @app.post("/v1/pages/{page_id}/archive")
    def archive_page(page_id: str, request: Request, body: ArchivePageRequest) -> JSONResponse:
        authenticate_campaign_action(request, body.campaign_key, action="archive", expected_page_id=page_id)
        archived = store.archive_page(page_id)
        return JSONResponse(archived)

    @app.post("/v1/pages/{page_id}/abort")
    def abort_page(page_id: str, request: Request, body: LifecyclePageRequest) -> JSONResponse:
        page = authenticate_campaign_action(request, body.campaign_key, action="abort", expected_page_id=page_id)
        try:
            payload = store.abort_page(page_id)
        except HTTPException as exc:
            security_log(
                "lifecycle_action",
                outcome="failure",
                action="aborted",
                ip=request_client_ip(request),
                path=request.url.path,
                page_id=page["id"],
                status_code=exc.status_code,
                reason=str(exc.detail),
            )
            raise
        security_log(
            "lifecycle_action",
            outcome="success",
            action="aborted",
            ip=request_client_ip(request),
            path=request.url.path,
            page_id=page["id"],
        )
        return JSONResponse(payload)

    @app.post("/v1/pages/{page_id}/compromise")
    def compromise_page(page_id: str, request: Request, body: LifecyclePageRequest) -> JSONResponse:
        page = authenticate_campaign_action(request, body.campaign_key, action="compromise", expected_page_id=page_id)
        try:
            payload = store.compromise_page(page_id)
        except HTTPException as exc:
            security_log(
                "lifecycle_action",
                outcome="failure",
                action="compromised",
                ip=request_client_ip(request),
                path=request.url.path,
                page_id=page["id"],
                status_code=exc.status_code,
                reason=str(exc.detail),
            )
            raise
        security_log(
            "lifecycle_action",
            outcome="success",
            action="compromised",
            ip=request_client_ip(request),
            path=request.url.path,
            page_id=page["id"],
        )
        return JSONResponse(payload)

    @app.post("/v1/pages/{page_id}/report")
    def report_page(page_id: str, request: Request, body: ReportPageRequest) -> JSONResponse:
        try:
            report = store.report_page(page_id, reason=body.reason, note=body.note)
        except HTTPException as exc:
            security_log(
                "report_submission",
                outcome="failure",
                ip=request_client_ip(request),
                path=request.url.path,
                page_id=page_id,
                reason_code=body.reason.strip().lower(),
                status_code=exc.status_code,
                reason=str(exc.detail),
            )
            raise
        security_log(
            "report_submission",
            outcome="success",
            ip=request_client_ip(request),
            path=request.url.path,
            page_id=page_id,
            reason_code=report["reason"],
            report_id=report["id"],
        )
        return JSONResponse(report)

    @app.get("/v1/pages/{page_id}/share")
    def share_payload(page_id: str) -> JSONResponse:
        return JSONResponse(store.share_payload(page_id))

    @app.get("/v1/pages/{page_id}/button")
    def button_payload(page_id: str) -> JSONResponse:
        return JSONResponse(store.button_payload(page_id))

    @app.get("/v1/pages/{page_id}/transactions")
    def page_transactions(page_id: str) -> JSONResponse:
        return JSONResponse(store.get_transactions(page_id))

    @app.get("/v1/pages/{page_id}/proof")
    def page_proof(page_id: str) -> JSONResponse:
        return JSONResponse(store.proof_payload(page_id))

    @app.get("/v1/pages/{page_id}/verify")
    def page_verify(page_id: str) -> JSONResponse:
        return JSONResponse(store.verify_payload(page_id))

    @app.get("/v1/pages/{page_id}/anchor")
    def page_anchor(page_id: str) -> JSONResponse:
        return JSONResponse(store.anchor_payload(page_id))

    @app.get("/v1/pages/{page_id}/proof-bundle")
    def page_proof_bundle(page_id: str) -> Response:
        bundle = store.proof_bundle(page_id)
        return Response(
            content=json.dumps(bundle, indent=2, sort_keys=True),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="fundregistry-proof-{page_id}.json"',
            },
        )

    @app.get("/v1/payments/{payment_id}")
    def get_payment(payment_id: str) -> JSONResponse:
        payment = store.get_payment_intent(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment intent not found.")
        return JSONResponse(payment)

    @app.post("/v1/proofs/{challenge_id}/verify")
    def verify_proof(challenge_id: str, request: Request, body: ProofVerifyRequest) -> JSONResponse:
        return JSONResponse(
            verify_challenge_action(
                request,
                challenge_id=challenge_id,
                proof=body.proof,
                action="proof_verify_global",
            )
        )

    @app.post("/v1/recover")
    def recover_page(request: Request, body: RecoverRequest) -> JSONResponse:
        client_ip = request_client_ip(request)
        try:
            payload = store.create_recovery_challenge(body.page_ref)
        except HTTPException as exc:
            security_log(
                "recovery_challenge",
                outcome="failure",
                ip=client_ip,
                path=request.url.path,
                page_ref=body.page_ref,
                status_code=exc.status_code,
                reason=str(exc.detail),
            )
            raise
        security_log(
            "recovery_challenge",
            outcome="success",
            ip=client_ip,
            path=request.url.path,
            page_ref=body.page_ref,
            page_id=payload.get("page_id"),
            challenge_id=payload.get("challenge_id"),
        )
        return JSONResponse(payload)

    if settings.allow_dev_actions:

        @app.post("/v1/dev/payments/{payment_id}/mark-paid")
        def dev_mark_paid(payment_id: str) -> JSONResponse:
            return JSONResponse(store.mark_payment_paid(payment_id))

    return app

app = None if env_flag("FUND_REGISTRY_DISABLE_AUTO_APP", False) else create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("FUND_REGISTRY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("FUND_REGISTRY_PORT", str(DEFAULT_PORT)))
    uvicorn.run(create_app(), host=host, port=port)
