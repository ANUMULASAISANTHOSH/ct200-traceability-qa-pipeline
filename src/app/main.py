"""ASGI application entrypoint."""

import uvicorn
from src.app.factory import create_app
from src.app.config import get_settings

settings = get_settings()
app = create_app(settings)

if __name__ == "__main__":
    uvicorn.run(
        "src.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
