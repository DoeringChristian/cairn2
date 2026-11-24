"""Workspace layouts: the cards, sections and run selection the browser's workspace shows.

A layout is version 1 JSON. The server checks it as the browser does, so a layout written
by an agent never breaks the page; unknown keys are refused so a misspelt setting fails."""

from .model import ValidationError, canonical

LAYOUT_LIMIT = 1024 * 1024
KINDS = ("scalar", "image", "video", "plotly", "viewer")
CHOICES = {
    "filterMode": ("all", "any"),
    "lineOrder": ("label", "minimum", "maximum"),
    "marks": ("lines", "markers", "lines+markers"),
    "mode": ("gallery", "compare", "sequence"),
    "matching": ("exact", "previous", "nearest"),
    "fit": ("contain", "cover"),
}
TEXT = ("id", "title", "section", "x", "y", "value", "axis", "caption")
FLAGS = ("logX", "logY", "linkAxes")
SIZES = {"width": (1, 12), "height": (280, 1000), "columns": (1, 6), "pageSize": (1, 24)}
CARD = {*TEXT, *FLAGS, *SIZES, *CHOICES, "kind", "filters", "split", "pointOrder"}
OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge", "contains", "exists")
CENTERS = ("none", "mean", "median", "min", "max")
BANDS = ("none", "minmax", "std", "sem")
LAYOUT = {"version", "id", "name", "search", "versions", "status", "selected", "sections", "cards"}


def _fail(message):
    raise ValidationError(f"Invalid workspace layout: {message}")


def _keys(value, required, optional=(), where="layout"):
    if not isinstance(value, dict):
        _fail(f"{where} must be an object")
    if missing := sorted(set(required) - set(value)):
        _fail(f"{where} lacks {', '.join(missing)}")
    if unknown := sorted(set(value) - set(required) - set(optional)):
        _fail(f"{where} has unknown keys {', '.join(unknown)}")


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _card(card, sections):
    _keys(card, CARD, ("aggregation",), "a card")
    where = f"card {card['id']!r}" if isinstance(card["id"], str) else "a card"
    if card["kind"] not in KINDS:
        _fail(f"{where} has kind {card['kind']!r}; kinds are {', '.join(KINDS)}")
    if card["section"] not in sections:
        _fail(f"{where} names no section of the layout")
    for key in TEXT:
        if not isinstance(card[key], str):
            _fail(f"{where}: {key} must be a string")
    for key in FLAGS:
        if not isinstance(card[key], bool):
            _fail(f"{where}: {key} must be true or false")
    for key, (low, high) in SIZES.items():
        if type(card[key]) is not int or not low <= card[key] <= high:
            _fail(f"{where}: {key} must be an integer from {low} to {high}")
    for key, choices in CHOICES.items():
        if card[key] not in choices:
            _fail(f"{where}: {key} is one of {', '.join(choices)}")
    if not _strings(card["split"]):
        _fail(f"{where}: split must be a list of fields")
    if not isinstance(card["filters"], list):
        _fail(f"{where}: filters must be a list")
    for item in card["filters"]:
        _keys(item, ("field", "op", "value"), where=f"a filter of {where}")
        if not isinstance(item["field"], str) or not isinstance(item["value"], str):
            _fail(f"{where}: a filter's field and value are strings")
        if item["op"] not in OPERATORS:
            _fail(f"{where}: a filter's op is one of {', '.join(OPERATORS)}")
    order = card["pointOrder"]
    _keys(order, ("field", "descending"), where=f"pointOrder of {where}")
    if not isinstance(order["field"], str) or not isinstance(order["descending"], bool):
        _fail(f"{where}: pointOrder is a field and descending true or false")
    if "aggregation" in card:
        aggregation = card["aggregation"]
        _keys(aggregation, ("center", "band", "showMembers"), where=f"aggregation of {where}")
        if (
            aggregation["center"] not in CENTERS
            or aggregation["band"] not in BANDS
            or not isinstance(aggregation["showMembers"], bool)
        ):
            _fail(f"{where}: aggregation center, band or showMembers is invalid")


def validate_layout(layout, name):
    """Check a layout as the workspace page does; its name must be the workspace's."""
    _keys(layout, LAYOUT, ("view",))
    if len(canonical(layout)) > LAYOUT_LIMIT:
        _fail("larger than 1 MiB")
    if layout["version"] != 1:
        _fail("version must be 1")
    if layout["name"] != name:
        _fail(f"its name {layout['name']!r} differs from the workspace {name!r}")
    for key in ("id", "search", "status"):
        if not isinstance(layout[key], str):
            _fail(f"{key} must be a string")
    if layout["versions"] not in ("latest", "all"):
        _fail("versions is latest or all")
    if layout["selected"] is not None and not _strings(layout["selected"]):
        _fail("selected is null or a list of run IDs")
    if layout.get("view", "together") not in ("together", "runs"):
        _fail("view is together or runs")
    sections = layout["sections"]
    if not isinstance(sections, list) or not sections:
        _fail("sections must be a nonempty list")
    for section in sections:
        _keys(section, ("id", "title", "collapsed"), where="a section")
        if not isinstance(section["id"], str) or not isinstance(section["title"], str):
            _fail("a section's id and title are strings")
        if not isinstance(section["collapsed"], bool):
            _fail("a section's collapsed is true or false")
    ids = [section["id"] for section in sections]
    if len(set(ids)) != len(ids):
        _fail("section IDs repeat")
    if not isinstance(layout["cards"], list):
        _fail("cards must be a list")
    for card in layout["cards"]:
        _card(card, ids)
    cards = [card["id"] for card in layout["cards"]]
    if len(set(cards)) != len(cards):
        _fail("card IDs repeat")
    return layout
