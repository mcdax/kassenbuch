"""
Client for the Lidl Plus app's receipt API (unofficial, reverse engineered).

- Ticket list: GET /api/v2/{COUNTRY}/tickets?pageNumber=N (25 per page, newest first)
- Ticket detail: GET /api/v3/{COUNTRY}/tickets/{id}, needs an Accept-Language header

Only the bearer token is sent: the made-up app headers of older clients get requests stalled.
"""

from dataclasses import dataclass

import requests

from kassenbuch import auth

TICKETS_API = "https://tickets.lidlplus.com/api"
TIMEOUT = 20


class ApiError(RuntimeError):
    """A request to the receipt API failed."""


@dataclass
class TicketSummary:
    id: str
    has_html: bool


@dataclass
class TicketPage:
    tickets: list[TicketSummary]
    total_count: int
    page_size: int


class LidlPlusClient:
    def __init__(self, account: str):
        self.account = account
        self.session = requests.Session()
        self.country = "de"
        self._renew_token()

    def _renew_token(self) -> None:
        # AuthError propagates: without a token the whole run has to stop
        token, self.country = auth.get_access_token(self.account)
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, **kwargs) -> dict:
        url = f"{TICKETS_API}{path}"
        try:
            response = self.session.get(url, timeout=TIMEOUT, **kwargs)
            # Access tokens expire after 20 minutes
            if response.status_code == 401:
                self._renew_token()
                response = self.session.get(url, timeout=TIMEOUT, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            raise ApiError(f"HTTP {e.response.status_code} für {path}") from None
        except (requests.RequestException, ValueError) as e:
            raise ApiError(f"{type(e).__name__} für {path}") from None

    def tickets_page(self, page: int) -> TicketPage:
        data = self._get(f"/v2/{self.country.upper()}/tickets", params={"pageNumber": page})
        return TicketPage(
            tickets=[TicketSummary(t["id"], bool(t.get("isHtml") or t.get("hasHtmlDocument"))) for t in data["tickets"]],
            total_count=data["totalCount"],
            page_size=data["size"],
        )

    def ticket(self, ticket_id: str) -> dict:
        return self._get(f"/v3/{self.country.upper()}/tickets/{ticket_id}",
                         headers={"Accept-Language": self.country.lower()})
