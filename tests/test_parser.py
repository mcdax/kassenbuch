from pathlib import Path

import pytest

from kassenbuch.parser import cents, parse_receipt

FIXTURES = Path(__file__).parent / "fixtures"


def receipt_html(purchase_lines: list[tuple], summary: list[str] = ("zu zahlen  1,00",), vat: list[tuple] = ()) -> str:
    """
    Build a receipt in the API's markup: every printed line is one or more spans sharing an id.

    purchase_lines: (text, classes, data-attributes) per line
    vat: (tax type, percentage) per tax line
    """
    def spans(section, lines):
        out = []
        for n, (text, classes, data) in enumerate(lines, 1):
            attrs = "".join(f' data-{k}="{v}"' for k, v in data.items())
            out.append(f'<span id="{section}_line_{n}" class="{classes}"{attrs}>{text}</span>\n')
        return f'<span class="{section}">{"".join(out)}</span>'

    vat_lines = [(f"{t} {p} %", "", {"tax-type": t, "tax-percentage": p, "tax-base-amount": "1,00", "tax-amount": "0,07"})
                 for t, p in vat]
    return ("<html><body><pre>" + spans("purchase_list", purchase_lines)
            + spans("purchase_summary", [(s, "", {}) for s in summary]) + spans("vat_info", vat_lines)
            + "</pre></body></html>")


def article(name, amount, tax="A", **data):
    return (f"{name}      {amount} {tax}", "article css_bold",
            {"art-id": data.get("id", "0000001"), "art-description": name, "unit-price": data.get("unit_price", amount),
             "tax-type": tax, **({"art-quantity": data["quantity"]} if "quantity" in data else {})})


def test_real_receipt_adds_up():
    r = parse_receipt((FIXTURES / "receipt_anonymized.html").read_text())

    assert r.warnings == []
    assert r.total_cents == 10578 == r.computed_total_cents
    assert len(r.items) == 45
    assert [(p.method, p.amount_cents) for p in r.payments] == [("Kreditkarte", 10578)]
    assert {(t.tax_type, t.rate) for t in r.taxes} == {("A", 7.0), ("B", 19.0)}
    assert r.lidlplus_savings_cents == 219


def test_real_receipt_details():
    r = parse_receipt((FIXTURES / "receipt_anonymized.html").read_text())

    weighed = [i for i in r.items if i.unit == "kg"]
    assert [(i.quantity, i.unit_price_cents, i.amount_cents) for i in weighed][0] == (0.5, 598, 299)
    assert sum(i.is_deposit for i in r.items) == 2
    assert all(i.tax_rate in (7.0, 19.0) for i in r.items)
    assert sorted({d.kind for d in r.discounts}) == ["lidl_plus", "sticker"]
    # Every discount belongs to the article printed above it
    positions = {i.position for i in r.items}
    assert all(d.item_position in positions for d in r.discounts)
    assert r.discounts[0].item_position == 1


def test_weighed_and_multiple_articles():
    r = parse_receipt(receipt_html([
        article("Spitzkohl", "1,89", quantity="0,95", unit_price="1,99"),
        ("  0,950 kg x 1,99   EUR/kg", "article", {"art-id": "0000001"}),
        ("Spargel      3,69 x   2    7,38 A", "article css_bold",
         {"art-id": "0000002", "art-description": "Spargel", "unit-price": "3,69", "art-quantity": "2", "tax-type": "A"}),
    ], summary=["zu zahlen   9,27"], vat=[("A", "7")]))

    assert r.warnings == []
    assert [(i.name, i.quantity, i.unit, i.amount_cents, i.tax_rate) for i in r.items] == [
        ("Spitzkohl", 0.95, "kg", 189, 7.0),
        ("Spargel", 2.0, "stk", 738, 7.0),
    ]


def test_deposit_return():
    r = parse_receipt(receipt_html([
        article("Wasser", "2,00"),
        ("Pfandrückgabe                     -3,00 B", "", {}),
        ("  -12 x          0,25", "", {}),
    ], summary=["zu zahlen   -1,00"], vat=[("A", "7"), ("B", "19")]))

    assert r.warnings == []
    deposit = r.items[1]
    assert (deposit.name, deposit.quantity, deposit.unit_price_cents, deposit.amount_cents, deposit.is_deposit,
            deposit.tax_rate) == ("Pfandrückgabe", -12.0, 25, -300, True, 19.0)


def test_kg_in_name_is_not_a_weighed_article():
    r = parse_receipt(receipt_html([article("Zucker 1kg", "0,99")], summary=["zu zahlen   0,99"]))
    assert r.items[0].unit == "stk"


@pytest.mark.parametrize("line, classes, kind", [
    ("     Aktionsrabatt                -0,30", "", "promotion"),
    ("     Preisvorteil                 -0,30", "", "promotion"),
    ("     Lidl Plus Rabatt             -0,30", "discount", "lidl_plus"),
    ("     RABATT 30%                   -0,30", "discount", "sticker"),
    ("     Rabatt Mandeln               -0,30", "discount", "coupon"),
])
def test_discount_kinds(line, classes, kind):
    r = parse_receipt(receipt_html(
        [article("Mandeln", "2,99"), (line, classes, {"promotion-id": "P1"} if classes else {})],
        summary=["zu zahlen   2,69"]))

    assert r.warnings == []
    assert [(d.kind, d.amount_cents, d.item_position) for d in r.discounts] == [(kind, -30, 1)]


def test_summary_payments_and_savings():
    r = parse_receipt(receipt_html([article("Brot", "2,00")], summary=[
        "zu zahlen         2,00", "Bar               5,00", "Rückgeld         -3,00", "Gesamter Preisvorteil   0,50"]))

    assert r.total_cents == 200
    assert r.reported_savings_cents == 50
    assert [(p.method, p.amount_cents) for p in r.payments] == [("Bar", 500), ("Rückgeld", -300)]


def test_problems_become_warnings():
    assert "Kein Kassenbon-HTML" in parse_receipt("").warnings
    assert any("fehlt" in w for w in parse_receipt("<html><pre></pre></html>").warnings)

    r = parse_receipt(receipt_html([article("Brot", "2,00"), ("Irgendwas Neues", "", {})], summary=["zu zahlen 3,00"]))
    assert any("Unbekannte Zeile" in w for w in r.warnings)
    assert any("Summe passt nicht" in w for w in r.warnings)


@pytest.mark.parametrize("text, expected", [("1,89", 189), ("-0,76", -76), ("1.234,56", 123456), ("0,05", 5)])
def test_cents(text, expected):
    assert cents(text) == expected
