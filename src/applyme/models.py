"""Pydantic v2 models — the shared contracts (see docs/ARCHITECTURE.md §4)."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl


class SubmitMode(StrEnum):
    DRY_RUN = "dry-run"
    SANDBOX = "sandbox"
    REAL = "real"


class WorkExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")  # webhook_url etc. are rejected, never carried
    full_name: str
    email: EmailStr
    phone: str
    location: str
    city: str
    state: str
    country: str
    links: dict[str, str] = Field(default_factory=dict)
    work_authorized: bool
    requires_sponsorship: bool
    willing_to_relocate: bool
    expected_salary: int | None = None
    expected_salary_currency: str = "USD"
    total_experience_years: int | None = None
    work_experience: list[WorkExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    resume_path: Path


class Vacancy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    posting_id: str
    url: HttpUrl

    @property
    def apply_url(self) -> str:
        return f"{str(self.url).rstrip('/')}/apply"


class FieldRef(BaseModel):
    input_name: str
    field_type: str
    required: bool
    selector: str | None = None
    current_value: str | None = None


CardFieldType = Literal["multiple-choice", "multiple-select", "dropdown", "text", "textarea"]


class CardField(BaseModel):
    field_index: int
    field_type: CardFieldType
    text: str
    required: bool
    options: list[str] = Field(default_factory=list)
    input_name: str


class Card(BaseModel):
    card_id: str
    fields: list[CardField]


class FormSpec(BaseModel):
    standard_fields: dict[str, FieldRef]
    cards: list[Card]
    sitekey: str
    account_id: str
    posting_id: str
    rqdata: str | None = None


Status = Literal["SUCCESS", "FAILED", "CAPTCHA_BLOCKED", "DRY_RUN_READY", "DUPLICATE_SUSPECTED", "RETRYABLE_ERROR"]


class ApplyResult(BaseModel):
    posting_url: str
    company: str
    posting_id: str
    status: Status
    reason: str = ""
    final_url: str | None = None
    http_status: int | None = None
    flagged_fields: list[str] = Field(default_factory=list)
    solver_used: Literal["none", "capsolver", "twocaptcha"] = "none"
    solve_ms: int | None = None
    silent_pass: bool | None = None  # did the invisible hCaptcha self-pass? (the unattended KPI; None until a submit)
    captcha_outcome: Literal["silent_pass", "challenge_rendered", "blocked"] | None = None
    ip_fraud_score: int | None = None  # IPQualityScore egress-IP fraud_score at run time (pre-flight), if measured
    rng_seed: int
    cf_ray: str | None = None
    attempts: int = 1
    confirmation_email_url: str | None = None
    screenshot_paths: list[str] = Field(default_factory=list)
    html_snapshot_path: str | None = None
    har_path: str | None = None
    started_at: datetime
    finished_at: datetime

    @property
    def result_string(self) -> str:
        match self.status:
            case "SUCCESS":
                return "success"
            case "CAPTCHA_BLOCKED":
                return "captcha blocked"
            case "FAILED":
                return f"failed:{self.reason}" if self.reason else "failed"
            case "RETRYABLE_ERROR":
                return f"error:{self.reason}" if self.reason else "error"
            case "DUPLICATE_SUSPECTED":
                return "duplicate"
            case _:
                return self.status.lower()
