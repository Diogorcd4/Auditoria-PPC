from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DEMO_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo_audit.json"

app = FastAPI(title="Auditor")


@app.get("/api/demo")
def get_demo_audit() -> JSONResponse:
    """Serve the fixture that powers the #/demo route. No LLM, no network, no Ollama needed."""
    if not DEMO_FIXTURE_PATH.exists():
        raise HTTPException(status_code=404, detail="Fixture de demonstração não encontrada.")
    data = json.loads(DEMO_FIXTURE_PATH.read_text(encoding="utf-8"))
    return JSONResponse(data)


# Routing is hash-based (#/demo, #/history...), so the server only ever needs to serve
# static assets; the browser path stays "/" and the fragment is handled in app.js.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
