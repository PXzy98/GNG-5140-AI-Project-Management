import logging
import time
import uuid

from fastapi import Request

logger = logging.getLogger("work_pulse.audit")


async def audit_middleware(request: Request, call_next):
    """Log every request with method, path, status, and latency."""
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    logger.info(
        "REQ  [%s] %s %s",
        request_id,
        request.method,
        request.url.path,
    )
    response = await call_next(request)
    latency_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "RESP [%s] %s %s → %d  %.0fms",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
    )
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Latency-Ms"] = f"{latency_ms:.0f}"
    return response
