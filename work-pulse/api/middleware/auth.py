from fastapi import Request


async def auth_middleware(request: Request, call_next):
    """Pass-through auth stub for prototype."""
    response = await call_next(request)
    return response
