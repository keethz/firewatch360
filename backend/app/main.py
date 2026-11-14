from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.services.harmonize import Harmonizer
from app.services.impact import LiveCache, StaticLayers
from app.services.nrt import NrtClient
from app.services.store import HotspotStore


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="FIREWATCH 360 API", version="0.5.0")
    app.state.settings = settings
    app.state.store = HotspotStore(settings)
    app.state.harmonizer = Harmonizer(app.state.store)
    app.state.impact_static = StaticLayers(settings.data_dir / "processed" / "impact")
    app.state.impact_live = LiveCache(settings.data_dir / "cache" / "impact")
    app.state.nrt = NrtClient(settings.firms_map_key, settings.data_dir / "cache" / "nrt")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
