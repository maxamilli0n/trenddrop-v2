from datetime import timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo always in 3.9+
    ZoneInfo = None  # type: ignore


if ZoneInfo:
    NYC_TZ = ZoneInfo("America/New_York")
else:
    NYC_TZ = timezone.utc

