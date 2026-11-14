"""Stateless FastAPI API; camera writes and dashboard reads have separate keys."""
import logging
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .config import Settings
from .models import QuoteRequest, ScanEvent
from .pricing import BANGKOK, calculate_fee
from .repository import SQLiteStore, StoreError, SupabaseStore, stamp

logger = logging.getLogger("parking")


def create_app(settings=None, store=None):
    settings = settings or Settings.from_env()
    store = store or (SQLiteStore(settings.demo_db_path) if settings.demo_mode else SupabaseStore(settings))
    app = FastAPI(title="Parking System API", version="1.0.0", description="เวลาไทย / ค่าจอดตามชั่วโมง / ค่าปรับรายวัน 02:00")
    app.state.store = store

    def require_key(actual, expected):
        if not actual or not secrets.compare_digest(actual, expected):
            raise HTTPException(401, "Invalid API key")

    def camera_key(x_api_key: str | None = Header(default=None)):
        require_key(x_api_key, settings.vision_api_key)

    def dashboard_key(x_api_key: str | None = Header(default=None)):
        require_key(x_api_key, settings.dashboard_api_key)

    @app.exception_handler(StoreError)
    async def store_error_handler(request, exc):
        from fastapi.responses import JSONResponse
        logger.warning("Database operation failed: status=%s", exc.status)
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "demo" if settings.demo_mode else "supabase"}

    @app.post("/api/v1/scans", dependencies=[Depends(camera_key)])
    def scan(event: ScanEvent):
        if event.confidence < settings.min_confidence:
            raise HTTPException(422, "OCR confidence below threshold; correct and confirm the plate")
        if event.occurred_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise HTTPException(422, "Scan timestamp is in the future; check camera clock")
        # Normalize timestamp so an idempotency comparison is independent of input offset.
        event.occurred_at = event.occurred_at.astimezone(timezone.utc)
        data = event.model_dump(mode="json")
        data["occurred_at"] = event.occurred_at.isoformat(timespec="microseconds")
        for attempt in range(3):
            active = store.active(event.plate, event.province)
            quote = None
            if active and event.direction != "entry":
                try:
                    quote = calculate_fee(stamp(active["entry_at"]), event.occurred_at, event.vehicle_type)
                except ValueError:
                    # Let the atomic RPC check replay/stale timestamp before rejecting.
                    quote = None
            try:
                return store.commit(data, active["id"] if active else None, quote, settings.cooldown_seconds)
            except StoreError as exc:
                if exc.message != "state_changed" or attempt == 2:
                    raise
        raise HTTPException(409, "Concurrent state change; retry same event_id")

    @app.post("/api/v1/quote", dependencies=[Depends(dashboard_key)])
    def quote(request: QuoteRequest):
        try:
            return calculate_fee(request.entry_at, request.exit_at, request.vehicle_type)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/sessions", dependencies=[Depends(dashboard_key)])
    def sessions(status: str = Query("all", pattern="^(all|active|completed)$"),
                 limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                 plate: str = Query("", max_length=32), start_date: date | None = None, end_date: date | None = None):
        if start_date and end_date and start_date > end_date:
            raise HTTPException(422, "start_date must not exceed end_date")
        return store.sessions(status=status, limit=limit, offset=offset, plate=plate.strip().upper(),
            start_date=start_date.isoformat() if start_date else None, end_date=end_date.isoformat() if end_date else None)

    @app.get("/api/v1/dashboard", dependencies=[Depends(dashboard_key)])
    def dashboard(start_date: date | None = None, end_date: date | None = None):
        end = end_date or datetime.now(BANGKOK).date()
        start = start_date or end - timedelta(days=29)
        if start > end or (end - start).days > 365:
            raise HTTPException(422, "Select a date range of at most 366 days")
        return store.dashboard(start.isoformat(), end.isoformat())

    return app
