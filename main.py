from app.core.settings import get_settings

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.api_debug and settings.app_env == "development",
        log_level=settings.log_level.lower(),
    )
