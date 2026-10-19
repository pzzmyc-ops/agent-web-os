import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fm.backend.terminal import router

_PREVIEW = Path(__file__).resolve().parents[1] / "frontend" / "terminal-preview.html"
_VENDOR = Path(__file__).resolve().parents[1] / "frontend" / "vendor" / "xterm"
_PORT = 18765


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.get("/home")
    def home():
        return {"path": str(Path.home())}

    @app.get("/dirs")
    def list_dirs(path: str = Query()):
        folder = os.path.abspath(path)
        if not os.path.isdir(folder):
            raise HTTPException(status_code=400, detail=f"not a directory: {path}")
        names = sorted(
            (name for name in os.listdir(folder) if os.path.isdir(os.path.join(folder, name))),
            key=str.lower,
        )
        parent = os.path.dirname(folder)
        if os.path.normcase(parent) == os.path.normcase(folder):
            parent = ""
        return {"path": folder, "parent": parent, "dirs": names}

    @app.get("/")
    def preview():
        return FileResponse(_PREVIEW)

    app.mount("/vendor/xterm", StaticFiles(directory=str(_VENDOR)), name="xterm")
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=_PORT, log_level="info")


if __name__ == "__main__":
    main()
