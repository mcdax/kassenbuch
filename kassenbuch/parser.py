"""
Parser for the receipt HTML ('htmlPrintedReceipt') of the Lidl Plus API.

The receipt is a <pre> of <span>s; all spans of one printed line share an id. Sections are
marked by classes (purchase_list, purchase_summary, vat_info, ...). Article lines carry
machine-readable attributes (data-art-id, data-art-description, data-unit-price,
data-art-quantity, data-tax-type), so prices and quantities don't have to be guessed from text.

A discount line (Aktionsrabatt, Lidl Plus Rabatt, RABATT 30%, coupons) belongs to the article
above it. Every receipt is checked: articles + discounts must add up to the amount paid.
"""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# Bump when parsing changes, so stored receipts can be parsed again ('kassenbuch reparse')
PARSER_VERSION = 2

MONEY = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
# Amount at the end of an article line, optionally followed by the tax letter
LINE_AMOUNT_RE = re.compile(rf"({MONEY})\s*(?:[A-Z]{{1,2}})?\s*$")
WEIGHT_RE = re.compile(r"(\d+,\d+)\s*kg")
# Line without article markup but with a tax letter, e.g. "Pfandrückgabe   -3,00 B"
PLAIN_ITEM_RE = re.compile(rf"^(\S.*?)\s+({MONEY})\s+([A-Z]{{1,2}})\s*$")
# Quantity line below such an item, e.g. "  -12 x          0,25"
QUANTITY_RE = re.compile(rf"^\s+(-?\d+)\s*x\s+({MONEY})\s*$")
DISCOUNT_RE = re.compile(rf"^\s*(.*?)\s+({MONEY})\s*$")
STICKER_RE = re.compile(r"RABATT\s*\d+\s*%", re.IGNORECASE)
SAVED_RE = re.compile(rf"({MONEY})\s*EUR gespart")


def cents(text: str) -> int:
    """'1.234,56' -> 123456, '-0,76' -> -76"""
    text = text.strip().replace(".", "")
    negative = text.startswith("-")
    euros, _, rest = text.lstrip("-").partition(",")
    value = int(euros or 0) * 100 + int((rest + "00")[:2])
    return -value if negative else value


def number(text: str) -> float:
    return float(text.strip().replace(".", "").replace(",", "."))


@dataclass
class Item:
    position: int
    article_id: str | None
    name: str
    quantity: float
    unit: str  # 'kg' or 'stk'
    unit_price_cents: int | None
    amount_cents: int  # line amount before discounts
    tax_type: str | None
    tax_rate: float | None = None
    is_deposit: bool = False


@dataclass
class Discount:
    position: int  # order of discount lines on the receipt
    item_position: int | None  # article the discount belongs to
    kind: str  # promotion | lidl_plus | coupon | sticker | other
    description: str
    amount_cents: int  # negative
    promotion_id: str | None = None


@dataclass
class Payment:
    method: str
    amount_cents: int


@dataclass
class Tax:
    tax_type: str
    rate: float
    gross_cents: int
    tax_cents: int


@dataclass
class ParsedReceipt:
    items: list[Item] = field(default_factory=list)
    discounts: list[Discount] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    taxes: list[Tax] = field(default_factory=list)
    total_cents: int | None = None  # "zu zahlen"
    reported_savings_cents: int | None = None  # "Gesamter Preisvorteil"
    lidlplus_savings_cents: int | None = None  # "Mit Lidl Plus ... EUR gespart"
    warnings: list[str] = field(default_factory=list)

    @property
    def computed_total_cents(self) -> int:
        return sum(i.amount_cents for i in self.items) + sum(d.amount_cents for d in self.discounts)


@dataclass
class _Line:
    text: str = ""
    classes: set = field(default_factory=set)
    data: dict = field(default_factory=dict)


def _lines(section) -> list[_Line]:
    """The section's spans merged into printed lines (spans of one line share an id)."""
    lines: dict[str, _Line] = {}
    for span in section.find_all("span"):
        line = lines.setdefault(span.get("id") or f"_{len(lines)}", _Line())
        line.text += "".join(span.find_all(string=True, recursive=False))
        line.classes |= set(span.get("class") or []) - {"css_bold"}
        line.data.update({k[5:]: v for k, v in span.attrs.items() if k.startswith("data-")})
    return list(lines.values())


def _parse_purchase_list(section, receipt: ParsedReceipt) -> None:
    item = None
    for line in _lines(section):
        text = line.text.rstrip()
        if not text.strip() or "currency" in line.classes:
            continue

        if "article" in line.classes:
            if text[:1].isspace():
                # Continuation of the current article, e.g. "  0,950 kg x 1,99   EUR/kg"
                if item and (weight := WEIGHT_RE.search(text)):
                    item.quantity, item.unit = number(weight.group(1)), "kg"
                continue
            amount = LINE_AMOUNT_RE.search(text)
            if not amount:
                receipt.warnings.append(f"Artikelzeile ohne Betrag: {text.strip()!r}")
                continue
            name = line.data.get("art-description") or text[:amount.start()].strip()
            quantity = line.data.get("art-quantity")
            item = Item(
                position=len(receipt.items) + 1,
                article_id=line.data.get("art-id"),
                name=name,
                quantity=number(quantity) if quantity else 1.0,
                # Weighed articles are recognised by their weight line, not by "kg" in the name
                unit="stk",
                unit_price_cents=cents(line.data["unit-price"]) if line.data.get("unit-price") else None,
                amount_cents=cents(amount.group(1)),
                tax_type=line.data.get("tax-type"),
                is_deposit=bool(re.match(r"(Pfand|Leergut)", name, re.IGNORECASE)),
            )
            receipt.items.append(item)
            continue

        if item and (quantity := QUANTITY_RE.match(text)):
            item.quantity, item.unit_price_cents = number(quantity.group(1)), cents(quantity.group(2))
            continue

        if plain := PLAIN_ITEM_RE.match(text):
            item = Item(
                position=len(receipt.items) + 1,
                article_id=None,
                name=plain.group(1),
                quantity=1.0,
                unit="stk",
                unit_price_cents=None,
                amount_cents=cents(plain.group(2)),
                tax_type=plain.group(3),
                is_deposit=bool(re.match(r"(Pfand|Leergut)", plain.group(1), re.IGNORECASE)),
            )
            receipt.items.append(item)
            continue

        match = DISCOUNT_RE.match(text)
        if not match:
            receipt.warnings.append(f"Unbekannte Zeile: {text.strip()!r}")
            continue
        description, amount = match.group(1).strip(), cents(match.group(2))
        if "discount" in line.classes:
            if description.lower().startswith("lidl plus"):
                kind = "lidl_plus"
            elif STICKER_RE.search(description):
                kind = "sticker"
            else:
                kind = "coupon"
        else:
            kind = "promotion" if amount < 0 else "other"
        if kind == "other":
            receipt.warnings.append(f"Unbekannte Zeile mit Betrag: {text.strip()!r}")
        receipt.discounts.append(Discount(
            position=len(receipt.discounts) + 1,
            item_position=item.position if item else None,
            kind=kind,
            description=description,
            amount_cents=amount,
            promotion_id=line.data.get("promotion-id"),
        ))


def _parse_summary(section, receipt: ParsedReceipt) -> None:
    for line in _lines(section):
        text = line.text.strip()
        amount = re.search(rf"({MONEY})\s*$", text)
        if not amount:
            continue
        if text.lower().startswith("zu zahlen"):
            receipt.total_cents = cents(amount.group(1))
        elif "Preisvorteil" in text:
            receipt.reported_savings_cents = cents(amount.group(1))
        elif line.data.get("tender-description"):
            receipt.payments.append(Payment(line.data["tender-description"], cents(amount.group(1))))
        else:
            # e.g. "Rückgeld" for cash payments
            receipt.payments.append(Payment(text[:amount.start()].strip(), cents(amount.group(1))))


def _parse_vat(section, receipt: ParsedReceipt) -> None:
    lidl_plus_box = False
    for line in _lines(section):
        if line.data.get("tax-type") and line.data.get("tax-percentage"):
            receipt.taxes.append(Tax(
                tax_type=line.data["tax-type"],
                rate=number(line.data["tax-percentage"]),
                gross_cents=cents(line.data.get("tax-base-amount", "0,00")),
                tax_cents=cents(line.data.get("tax-amount", "0,00")),
            ))
        elif "Mit Lidl Plus" in line.text:
            lidl_plus_box = True
        elif lidl_plus_box and (saved := SAVED_RE.search(line.text)):
            receipt.lidlplus_savings_cents = cents(saved.group(1))
            lidl_plus_box = False


def parse_receipt(html: str) -> ParsedReceipt:
    """Parse a receipt; problems end up in 'warnings' instead of raising."""
    receipt = ParsedReceipt()
    if not html:
        receipt.warnings.append("Kein Kassenbon-HTML")
        return receipt
    try:
        soup = BeautifulSoup(html, "html.parser")
        for name, parse in (("purchase_list", _parse_purchase_list), ("purchase_summary", _parse_summary),
                            ("vat_info", _parse_vat)):
            section = soup.find(class_=name)
            if section is None:
                receipt.warnings.append(f"Abschnitt '{name}' fehlt")
            else:
                parse(section, receipt)
    except Exception as e:  # a broken receipt must not stop the import
        receipt.warnings.append(f"Parser-Fehler: {type(e).__name__}: {e}")
        return receipt

    rates = {t.tax_type: t.rate for t in receipt.taxes}
    for item in receipt.items:
        item.tax_rate = rates.get(item.tax_type)

    if receipt.total_cents is None:
        receipt.warnings.append("Kein 'zu zahlen' gefunden")
    elif receipt.computed_total_cents != receipt.total_cents:
        receipt.warnings.append(f"Summe passt nicht: Artikel + Rabatte = {receipt.computed_total_cents / 100:.2f}, "
                                f"zu zahlen = {receipt.total_cents / 100:.2f}")
    return receipt
