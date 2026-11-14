"""กติกาค่าจอดจากป้าย: ปัดขึ้นทุกชั่วโมงและปรับทุกวันที่ข้าม 02:00 ไทย."""
from datetime import datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo("Asia/Bangkok")
POLICY_VERSION = "2026-11-ceil-daily-0200-v1"


def calculate_fee(entry_at: datetime, exit_at: datetime, vehicle_type: str) -> dict:
    if vehicle_type not in {"car", "motorcycle"}:
        raise ValueError("vehicle_type must be car or motorcycle")
    if any(dt.tzinfo is None or dt.utcoffset() is None for dt in (entry_at, exit_at)):
        raise ValueError("Times must include a timezone")
    seconds = (exit_at - entry_at).total_seconds()
    if seconds < 0:
        raise ValueError("Exit must not precede entry")
    # ใช้วินาทีจริงก่อนปัดชั่วโมง เพื่อไม่ให้ขอบเขต 3h + เศษวินาทีได้จอดฟรี
    hours = ceil(seconds / 3600)
    middle_hours = min(max(hours - 3, 0), 3)
    late_hours = max(hours - 6, 0)
    parking_fee = middle_hours * (30 if vehicle_type == "car" else 10) + late_hours * 50
    start, end = entry_at.astimezone(BANGKOK), exit_at.astimezone(BANGKOK)
    # นับขอบเขตเวลา 02:00 ที่อยู่ระหว่างเวลาเข้าและออกอย่างเคร่งครัด
    # ออกตรง 02:00 ไม่เสียค่าปรับ; เข้าตรง 02:00 ไม่นับเป็นค้างคืนวันนั้น
    boundary = datetime.combine(start.date(), time(2), BANGKOK)
    if boundary <= start:
        boundary += timedelta(days=1)
    overnight_count = max(0, ceil((end - boundary).total_seconds() / 86400))
    fine = overnight_count * (2000 if vehicle_type == "car" else 1000)
    return {
        "duration_seconds": ceil(seconds), "billable_hours": hours,
        "parking_fee": parking_fee, "overnight_count": overnight_count,
        "fine_amount": fine, "total_amount": parking_fee + fine,
        "policy_version": POLICY_VERSION,
    }
