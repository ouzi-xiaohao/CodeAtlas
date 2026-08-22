from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import get_settings
from app.container import build_container
from app.observability import configure_observability
from app.services.seed import seed_demo


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.container = build_container(settings)
    if not settings.enable_external_services:
        await seed_demo(app.state.container.retriever, app.state.container.graph)
    yield
    await app.state.container.audit.close()
    graph_driver = getattr(app.state.container.graph, "driver", None)
    if graph_driver is not None:
        await graph_driver.close()
    qdrant_client = getattr(app.state.container.retriever, "client", None)
    if qdrant_client is not None:
        await qdrant_client.close()


app = FastAPI(
    title="CodeAtlas API",
    version="0.1.0",
    description="研发知识图谱与变更影响分析平台",
    lifespan=lifespan,
)
configure_observability(app, settings)
app.include_router(router, prefix=settings.api_prefix)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    return FileResponse(static_dir / "index.html")
