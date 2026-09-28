from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="Auditor de websites: auditorias locais com IA gratuita.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, no_browser: bool = False) -> None:
    """Arranca o servidor web e abre o browser em http://127.0.0.1:8000."""
    import webbrowser

    import uvicorn

    from auditor.server import app as fastapi_app

    url = f"http://{host}:{port}"
    if not no_browser:
        webbrowser.open(url)
    uvicorn.run(fastapi_app, host=host, port=port)


@app.command()
def audit(
    url: str,
    max_pages: int = 25,
    only: str = typer.Option("", help="Lista de passos do pipeline a correr, ex.: crawl,tracking"),
    dry_run: bool = False,
    resume: bool = False,
    mock: bool = False,
    fast: bool = typer.Option(False, help="Limita a 5 páginas, para testar rapidamente com modelos pequenos/lentos"),
) -> None:
    """Corre uma auditoria a partir da linha de comandos."""
    from auditor.pipeline import run_audit_sync

    run_audit_sync(
        url,
        max_pages=max_pages,
        only=[s.strip() for s in only.split(",") if s.strip()],
        dry_run=dry_run,
        resume=resume,
        mock=mock,
        fast=fast,
    )


if __name__ == "__main__":
    app()
