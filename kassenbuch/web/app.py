"""
Web interface: overview, receipts (incl. the original receipt for printing/PDF), products and
price history. With WEB_PASSWORD set, every page needs a login.
"""

import hmac
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from kassenbuch import db, queries
from kassenbuch.web import charts

HERE = Path(__file__).parent
CSP = ("default-src 'self'; img-src 'self' data:; frame-src 'self'; form-action 'self'; "
       "frame-ancestors 'self'; base-uri 'none'")
# The original receipt brings inline styles; the only script allowed is our print button
RECEIPT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'self'; frame-ancestors 'self'"
PRINT_BUTTON = """
<div class="kb-print" style="position:fixed;top:12px;right:12px;font-family:sans-serif">
  <button id="kb-print" type="button" style="padding:8px 14px;font-size:14px;cursor:pointer">
    Drucken / als PDF speichern</button>
</div>
<style>@media print { .kb-print { display: none } }</style>
<script src="/static/print.js"></script>
"""


class LoginRequired(Exception):
    pass


def require_login(request: Request) -> None:
    if os.environ.get("WEB_PASSWORD") and not request.session.get("logged_in"):
        raise LoginRequired()


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _safe_next(target: str | None) -> str:
    """Only redirect to paths on this site (no open redirect)."""
    return target if target and target.startswith("/") and not target.startswith("//") else "/"


def _date(value: str, with_time: bool = False) -> str:
    parsed = datetime.fromisoformat(value)
    return parsed.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def _quantity(value: float, unit: str) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".") if unit == "kg" else f"{value:g}"
    return f"{text.replace('.', ',')} {unit}"


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    conn = db.connect()
    secret = os.environ.get("WEB_SECRET") or db.app_setting(conn, "session_secret", lambda: secrets.token_hex(32))
    conn.close()
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="strict", max_age=30 * 24 * 3600,
                       https_only=os.environ.get("WEB_HTTPS_ONLY", "false").lower() == "true")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(euro=charts.euro, date=_date, quantity=_quantity)
    templates.env.globals.update(kinds=queries.DISCOUNT_KINDS, login_enabled=lambda: bool(os.environ.get("WEB_PASSWORD")))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login?" + urlencode({"next": request.url.path}), status_code=303)

    def selection(request: Request, conn) -> tuple[str | None, queries.Period]:
        """Account (None = all) and period chosen via ?account=...&period=..."""
        account = request.query_params.get("account")
        return (account if account in queries.accounts(conn) else None,
                queries.period(request.query_params.get("period")))

    def render(request: Request, conn, template: str, **context) -> HTMLResponse:
        """Render with the account/period selection shared by all pages."""
        account, period = selection(request, conn)

        def link(path: str, **params) -> str:
            merged = {"account": account, "period": period.key if period.key != "12m" else None} | params
            query = urlencode({k: v for k, v in merged.items() if v})
            return path + ("?" + query if query else "")

        return templates.TemplateResponse(request, template, {
            "accounts": queries.accounts(conn), "account": account, "period": period,
            "years": queries.years(conn), "link": link, **context})

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/", failed: bool = False):
        return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "failed": failed})

    @app.post("/login")
    def login(request: Request, password: str = Form(...), next: str = Form("/")):
        expected = os.environ.get("WEB_PASSWORD", "")
        if expected and hmac.compare_digest(password.encode(), expected.encode()):
            request.session["logged_in"] = True
            return RedirectResponse(_safe_next(next), status_code=303)
        time.sleep(1)  # slows down guessing
        return RedirectResponse("/login?" + urlencode({"next": _safe_next(next), "failed": 1}), status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def overview(request: Request, conn=Depends(get_conn)):
        account, period = selection(request, conn)
        o = queries.overview(conn, account, period)
        months = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
        chart = charts.bar_chart([(f"{months[int(m[5:]) - 1]} {m[2:4]}", cents, f"{months[int(m[5:]) - 1]} {m[:4]}: "
                                   f"{charts.euro(cents)}") for m, cents in o.monthly])
        return render(request, conn, "overview.html", o=o, chart=chart)

    @app.get("/receipts", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def receipts(request: Request, conn=Depends(get_conn)):
        account, period = selection(request, conn)
        return render(request, conn, "receipts.html", receipts=queries.receipts(conn, account, period))

    @app.get("/receipts/{receipt_id}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def receipt(request: Request, receipt_id: str, conn=Depends(get_conn)):
        data = queries.receipt(conn, receipt_id)
        if data is None:
            return HTMLResponse("Kassenbon nicht gefunden", status_code=404)
        return render(request, conn, "receipt.html", **data)

    @app.get("/receipts/{receipt_id}/original", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def receipt_original(receipt_id: str, embed: bool = False, conn=Depends(get_conn)):
        row = conn.execute("SELECT html FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
        if row is None or not row["html"]:
            return HTMLResponse("Kein Original vorhanden", status_code=404)
        html = row["html"]
        if not embed:
            html = html.replace("</body>", PRINT_BUTTON + "</body>") if "</body>" in html else html + PRINT_BUTTON
        return HTMLResponse(html, headers={"Content-Security-Policy": RECEIPT_CSP})

    @app.get("/products", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def products(request: Request, q: str = "", sort: str = "spent", conn=Depends(get_conn)):
        account, period = selection(request, conn)
        items = queries.products(conn, account, period, search=q.strip())
        keys = {"spent": lambda p: -p["spent_cents"], "count": lambda p: (-p["purchases"], -p["spent_cents"]),
                "name": lambda p: p["name"].lower(), "price": lambda p: -(p["price_change"] or 0)}
        items.sort(key=keys.get(sort, keys["spent"]))
        return render(request, conn, "products.html", products=items, q=q, sort=sort)

    @app.get("/products/{article_id}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def product(request: Request, article_id: str, conn=Depends(get_conn)):
        account, _ = selection(request, conn)
        data = queries.product(conn, article_id, account)
        if data is None:
            return HTMLResponse("Produkt nicht gefunden", status_code=404)
        unit = "kg" if data["unit"] == "kg" else "Stück"
        regular = [(p["purchased_at"], p["unit_price_cents"], f"{_date(p['purchased_at'])}: "
                    f"{charts.euro(p['unit_price_cents'])}/{unit} (Normalpreis)")
                   for p in data["purchases"] if p["unit_price_cents"]]
        paid = [(p["purchased_at"], p["paid_unit_cents"], f"{_date(p['purchased_at'])}: "
                 f"{charts.euro(p['paid_unit_cents'])}/{unit} bezahlt")
                for p in data["purchases"] if p["paid_unit_cents"] and p["paid_unit_cents"] != p["unit_price_cents"]]
        # Discounted purchases are single events, so they get points but no connecting line
        chart = charts.line_chart([("regular", regular, True), ("paid", paid, False)])
        return render(request, conn, "product.html", product=data, chart=chart, unit=unit,
                      has_discounts=bool(paid))

    return app
