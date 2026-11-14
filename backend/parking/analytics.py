"""Demo analytics, using the same local-date definitions as the SQL RPC."""
from datetime import date, datetime, timedelta

from .pricing import BANGKOK
from .repository import stamp


def local(value):
    return stamp(value).astimezone(BANGKOK)


def filter_sessions(rows, status, plate, start_date, end_date):
    def include(s):
        if status == "active" and s["exit_at"] or status == "completed" and not s["exit_at"]:
            return False
        if plate and plate.upper() not in s["plate"]:
            return False
        # Monitor date filter follows the latest transition: exit for completed, entry for active.
        day = local(s["exit_at"] or s["entry_at"]).date().isoformat()
        return (not start_date or day >= start_date) and (not end_date or day <= end_date)
    return sorted([s for s in rows if include(s)], key=lambda s: stamp(s["exit_at"] or s["entry_at"]), reverse=True)


def summarize(rows, start_date, end_date):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    days = {str(start + timedelta(days=i)): {"date": str(start + timedelta(days=i)), "entries": 0, "exits": 0, "revenue": 0, "parking_fee": 0, "fines": 0} for i in range((end - start).days + 1)}
    hourly = [{"hour": h, "entries": 0, "exits": 0} for h in range(24)]
    types = {t: {"vehicle_type": t, "entries": 0, "revenue": 0} for t in ("car", "motorcycle")}
    completed = []
    today = datetime.now(BANGKOK).date().isoformat()
    for s in rows:
        ent = local(s["entry_at"])
        if ent.date().isoformat() in days:
            days[str(ent.date())]["entries"] += 1
            hourly[ent.hour]["entries"] += 1
            types[s["vehicle_type"]]["entries"] += 1
        if s["exit_at"]:
            ex = local(s["exit_at"])
            if str(ex.date()) in days:
                completed.append(s)
                day = days[str(ex.date())]
                day["exits"] += 1
                day["revenue"] += s["total_amount"]
                day["parking_fee"] += s["parking_fee"]
                day["fines"] += s["fine_amount"]
                hourly[ex.hour]["exits"] += 1
                types[s["vehicle_type"]]["revenue"] += s["total_amount"]
    return {"timezone": "Asia/Bangkok", "start_date": start_date, "end_date": end_date,
        "metrics": {"revenue_total": sum(s["total_amount"] or 0 for s in rows),
            "revenue_today": sum(s["total_amount"] for s in rows if s["exit_at"] and str(local(s["exit_at"]).date()) == today),
            "revenue_period": sum(s["total_amount"] for s in completed),
            "parking_fee_period": sum(s["parking_fee"] for s in completed),
            "fines_period": sum(s["fine_amount"] for s in completed),
            "active_vehicles": sum(not s["exit_at"] for s in rows),
            "entries_period": sum(d["entries"] for d in days.values()),
            "exits_period": len(completed),
            "average_duration_minutes": round(sum(s["duration_seconds"] for s in completed) / len(completed) / 60, 2) if completed else 0},
        "daily": list(days.values()), "hourly": hourly, "vehicle_types": list(types.values())}
