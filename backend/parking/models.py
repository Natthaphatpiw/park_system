"""Validate and normalize input before touching the database."""
import re
import unicodedata
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


def normalize_plate(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).upper()
    value = value.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))
    value = re.sub(r"[\s-]+", "", value)
    # รองรับป้ายไทย/อังกฤษ แต่ไม่รับประโยคหรือข้อความจังหวัดแทนทะเบียน
    if not re.fullmatch(r"[0-9A-Zก-ฮ]{2,12}", value) or not re.search(r"[0-9]", value):
        raise ValueError("Invalid plate: use plate characters and digits, without province")
    return value


class ScanEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID = Field(default_factory=uuid4)
    plate: str = Field(min_length=2, max_length=32)
    province: str = Field(default="ไม่ระบุ", min_length=1, max_length=80)
    vehicle_type: Literal["car", "motorcycle"] = "car"
    direction: Literal["auto", "entry", "exit"] = "auto"
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    camera_id: str = Field(default="gate-1", min_length=1, max_length=80)
    confidence: float = Field(default=1, ge=0, le=1)

    @field_validator("plate")
    @classmethod
    def plate_format(cls, value):
        return normalize_plate(value)

    @field_validator("province", "camera_id")
    @classmethod
    def trim_text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Text must not be empty or contain control characters")
        return value


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_at: AwareDatetime
    exit_at: AwareDatetime
    vehicle_type: Literal["car", "motorcycle"] = "car"
