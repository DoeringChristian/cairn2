"""Report documents: a ProseMirror tree the browser edits, read and written as Markdown.

Every block carries a stable `id`, so comments and collaborators can point at it. An agent
block appears in Markdown as `<?cairn agent ID?>`; its request and answer live elsewhere.
"""

import json
import re
import secrets
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin

from .model import ValidationError

BLOCKS = {
    "paragraph": "inline",
    "heading": "inline",
    "codeBlock": "text",
    "bulletList": "listItem",
    "orderedList": "listItem",
    "listItem": "blocks",
    "blockquote": "blocks",
    "callout": "blocks",
    "table": "tableRow",
    "tableRow": "cells",
    "tableHeader": "blocks",
    "tableCell": "blocks",
    "horizontalRule": None,
    "mathBlock": "text",
    "figure": None,
    "view": None,
    "agent": None,
}
INLINE = {"text", "hardBreak"}
MARKS = {"bold", "italic", "strike", "code", "math", "link"}
CALLOUTS = {"note", "tip", "warning", "important"}
AGENT = re.compile(r'<\?cairn agent ([A-Za-z][\w-]{0,63})(?: "((?:[^"\\]|\\.)*)")?\?>')
SLOT = re.compile(r"[A-Za-z][\w-]{0,63}")


def node_id():
    return secrets.token_hex(4)


def empty():
    return {"type": "doc", "content": [{"type": "paragraph", "attrs": {"id": "start"}}]}


def dumps(document):
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def loads(source):
    """The document a body part holds, validated."""
    try:
        document = json.loads(source)
    except ValueError:
        raise ValidationError("A report body is a JSON document") from None
    validate(document)
    return document


def validate(document):
    if not isinstance(document, dict) or document.get("type") != "doc":
        raise ValidationError("A report document has type doc")
    content = document.get("content")
    if not isinstance(content, list) or not content:
        raise ValidationError("A report document holds at least one block")
    for block in content:
        _block(block, 1)


def _attrs(node):
    attrs = node.get("attrs", {})
    if not isinstance(attrs, dict) or not all(
        isinstance(value, (str, int, float, bool)) or value is None for value in attrs.values()
    ):
        raise ValidationError(f"{node['type']} attributes are JSON scalars")
    return attrs


def _block(node, depth):
    if depth > 64:
        raise ValidationError("Report documents nest at most 64 levels")
    if not isinstance(node, dict) or node.get("type") not in BLOCKS:
        raise ValidationError(
            f"Unknown report block: {node.get('type') if isinstance(node, dict) else node!r}"
        )
    attrs, kind, content = _attrs(node), BLOCKS[node["type"]], node.get("content", [])
    if not isinstance(content, list):
        raise ValidationError(f"{node['type']} content is a list")
    if node["type"] == "agent" and not SLOT.fullmatch(str(attrs.get("slot", ""))):
        raise ValidationError("An agent block needs an identifier of 1–64 characters")
    if kind is None and content:
        raise ValidationError(f"{node['type']} has no content")
    for child in content:
        if kind == "inline":
            _inline(child)
        elif kind == "text":
            if not isinstance(child, dict) or child.get("type") != "text":
                raise ValidationError(f"A {node['type']} holds text")
        else:
            allowed = {"listItem": {"listItem"}, "tableRow": {"tableRow"}}.get(kind)
            allowed = allowed or ({"tableHeader", "tableCell"} if kind == "cells" else None)
            if allowed and (not isinstance(child, dict) or child.get("type") not in allowed):
                raise ValidationError(f"{node['type']} holds {', '.join(sorted(allowed))}")
            _block(child, depth + 1)


def _inline(node):
    if not isinstance(node, dict) or node.get("type") not in INLINE:
        raise ValidationError("Unknown inline report content")
    _attrs(node)
    if node["type"] == "text" and not isinstance(node.get("text"), str):
        raise ValidationError("Text nodes hold text")
    for mark in node.get("marks", []):
        if not isinstance(mark, dict) or mark.get("type") not in MARKS:
            raise ValidationError("Unknown text mark")
        _attrs(mark)


_md = (
    MarkdownIt("commonmark", {"html": True})
    .enable(["table", "strikethrough"])
    .use(dollarmath_plugin, double_inline=True)
    .use(container_plugin, "div", validate=lambda params, *args: params.strip().startswith("{"))
)
_ATTRIBUTE = re.compile(r"([#.])([^\s{}\"'=]+)")


def from_markdown(text, previous=None):
    """A document from Markdown, including `::: {.callout-*}` callouts and ```view blocks.

    Top-level blocks whose Markdown is unchanged from the previous document keep their ids."""
    content = _blocks(SyntaxTreeNode(_md.parse(text)).children)
    if previous:
        kept = {}
        for block in previous.get("content", []):
            kept.setdefault(_markdown(block), []).append(block.get("attrs", {}).get("id"))
        for block in content:
            ids = kept.get(_markdown(block))
            if ids and ids[0]:
                block["attrs"]["id"] = ids.pop(0)
    return {"type": "doc", "content": content} if content else empty()


def _with_id(node):
    node.setdefault("attrs", {})["id"] = node_id()
    return node


def _blocks(nodes):
    out = []
    for node in nodes:
        out.extend(_convert(node))
    return out


def _convert(node):
    kind = node.type
    if kind == "paragraph":
        children = node.children[0].children if node.children else []
        images = [child for child in children if child.type == "image"]
        if len(images) == 1 and all(
            child is images[0] or (child.type == "text" and not child.content.strip())
            for child in children
        ):
            image = images[0]
            return [
                _with_id(
                    {
                        "type": "figure",
                        "attrs": {"src": image.attrs.get("src", ""), "caption": _plain(image)},
                    }
                )
            ]
        return [_with_id({"type": "paragraph", "content": _inlines(children)})]
    if kind == "heading":
        level = min(int(node.tag[1]), 4)
        return [
            _with_id(
                {
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": _inlines(node.children[0].children),
                }
            )
        ]
    if kind in ("fence", "code_block"):
        info = (node.info or "").strip()
        if info == "view":
            return [_with_id({"type": "view", "attrs": {"source": node.content.rstrip("\n")}})]
        code = node.content.rstrip("\n")
        return [
            _with_id(
                {
                    "type": "codeBlock",
                    "attrs": {"language": info.split()[0] if info else None},
                    "content": [{"type": "text", "text": code}] if code else [],
                }
            )
        ]
    if kind == "math_block":
        tex = node.content.strip()
        return [
            _with_id(
                {"type": "mathBlock", "content": [{"type": "text", "text": tex}] if tex else []}
            )
        ]
    if kind == "hr":
        return [_with_id({"type": "horizontalRule"})]
    if kind == "blockquote":
        return [
            _with_id(
                {
                    "type": "blockquote",
                    "content": _blocks(node.children) or [_with_id({"type": "paragraph"})],
                }
            )
        ]
    if kind in ("bullet_list", "ordered_list"):
        items = [
            _with_id(
                {
                    "type": "listItem",
                    "content": _blocks(item.children) or [_with_id({"type": "paragraph"})],
                }
            )
            for item in node.children
        ]
        if kind == "bullet_list":
            return [_with_id({"type": "bulletList", "content": items})]
        return [
            _with_id(
                {
                    "type": "orderedList",
                    "attrs": {"start": int(node.attrs.get("start", 1))},
                    "content": items,
                }
            )
        ]
    if kind == "table":
        rows = []
        for section in node.children:
            for row in section.children:
                cells = [
                    _with_id(
                        {
                            "type": "tableHeader" if cell.type == "th" else "tableCell",
                            "content": [
                                _with_id(
                                    {
                                        "type": "paragraph",
                                        "content": _inlines(
                                            cell.children[0].children if cell.children else []
                                        ),
                                    }
                                )
                            ],
                        }
                    )
                    for cell in row.children
                ]
                rows.append(_with_id({"type": "tableRow", "content": cells}))
        return [_with_id({"type": "table", "content": rows})]
    if kind == "container_div":
        classes, ident = [], None
        for sign, value in _ATTRIBUTE.findall(node.info or ""):
            if sign == ".":
                classes.append(value)
            else:
                ident = value
        inner = _blocks(node.children)
        if "agent" in classes and ident and SLOT.fullmatch(ident):
            # Agent blocks of schema 8 kept their request in the body; it stays above the block.
            return [*inner, _with_id({"type": "agent", "attrs": {"slot": ident}})]
        kind = next(
            (name[8:] for name in classes if name.startswith("callout-") and name[8:] in CALLOUTS),
            "note",
        )
        return [
            _with_id(
                {
                    "type": "callout",
                    "attrs": {"kind": kind},
                    "content": inner or [_with_id({"type": "paragraph"})],
                }
            )
        ]
    if kind == "html_block":
        found = AGENT.fullmatch(node.content.strip())
        if found:
            attrs = {"slot": found.group(1)}
            if found.group(2):
                attrs["prompt"] = json.loads(f'"{found.group(2)}"')
            return [_with_id({"type": "agent", "attrs": attrs})]
        return [
            _with_id(
                {"type": "paragraph", "content": [{"type": "text", "text": node.content.strip()}]}
            )
        ]
    text = node.content if node.content else ""
    return (
        [_with_id({"type": "paragraph", "content": [{"type": "text", "text": text}]})]
        if text.strip()
        else []
    )


def _plain(node):
    return "".join(
        child.content if child.type == "text" else _plain(child) for child in node.children
    )


def _inlines(nodes, marks=()):
    out = []
    for node in nodes:
        kind = node.type
        if kind == "text" or kind == "html_inline":
            if node.content:
                out.append(
                    {
                        "type": "text",
                        "text": node.content,
                        **({"marks": list(marks)} if marks else {}),
                    }
                )
        elif kind == "softbreak":
            out.append({"type": "text", "text": " ", **({"marks": list(marks)} if marks else {})})
        elif kind == "hardbreak":
            out.append({"type": "hardBreak"})
        elif kind == "code_inline":
            out.append({"type": "text", "text": node.content, "marks": [*marks, {"type": "code"}]})
        elif kind == "math_inline" or kind == "math_inline_double":
            out.append({"type": "text", "text": node.content, "marks": [*marks, {"type": "math"}]})
        elif kind == "image":
            out.append({"type": "text", "text": f"![{_plain(node)}]({node.attrs.get('src', '')})"})
        else:
            mark = {
                "strong": {"type": "bold"},
                "em": {"type": "italic"},
                "s": {"type": "strike"},
            }.get(kind)
            if kind == "link":
                mark = {"type": "link", "attrs": {"href": node.attrs.get("href", "")}}
            out.extend(_inlines(node.children, (*marks, mark) if mark else marks))
    merged = []
    for node in out:
        last = merged[-1] if merged else None
        if (
            last
            and node["type"] == last["type"] == "text"
            and node.get("marks") == last.get("marks")
        ):
            last["text"] += node["text"]
        else:
            merged.append(node)
    return merged


def to_markdown(document):
    """Markdown for agents and the command line; structured blocks become placeholders."""
    blocks = (_markdown(block) for block in document.get("content", []))
    return "\n\n".join(text for text in blocks if text.strip()).strip() + "\n"


def _markdown(node):
    kind, attrs, content = node["type"], node.get("attrs", {}), node.get("content", [])
    if kind == "paragraph":
        return _text(content)
    if kind == "heading":
        return "#" * int(attrs.get("level", 1)) + " " + _text(content)
    if kind == "codeBlock":
        code = "".join(child.get("text", "") for child in content)
        fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", code)), default=0) + 1)
        return f"{fence}{attrs.get('language') or ''}\n{code}\n{fence}"
    if kind == "view":
        return f"```view\n{attrs.get('source', '')}\n```"
    if kind == "mathBlock":
        return "$$\n" + "".join(child.get("text", "") for child in content) + "\n$$"
    if kind == "horizontalRule":
        return "---"
    if kind == "figure":
        return f"![{attrs.get('caption') or ''}]({attrs.get('src', '')})"
    if kind == "agent":
        prompt = (
            f" {json.dumps(attrs['prompt'], ensure_ascii=False)}" if attrs.get("prompt") else ""
        )
        return f"<?cairn agent {attrs.get('slot')}{prompt}?>"
    if kind == "blockquote":
        return "\n".join("> " + line if line else ">" for line in _children(content).split("\n"))
    if kind == "callout":
        return f"::: {{.callout-{attrs.get('kind', 'note')}}}\n{_children(content)}\n:::"
    if kind in ("bulletList", "orderedList"):
        start, items = int(attrs.get("start", 1)), []
        for index, item in enumerate(content):
            marker = "- " if kind == "bulletList" else f"{start + index}. "
            lines = _children(item.get("content", [])).split("\n")
            items.append(
                "\n".join(
                    [
                        marker + lines[0],
                        *((" " * len(marker) + line if line else "") for line in lines[1:]),
                    ]
                )
            )
        return "\n".join(items)
    if kind == "table":
        rows = [
            [
                _text(
                    [
                        inline
                        for block in cell.get("content", [])
                        for inline in block.get("content", [])
                    ]
                ).replace("|", "\\|")
                for cell in row.get("content", [])
            ]
            for row in content
        ]
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows[1:]])
    return _children(content)


def _children(blocks):
    # A list follows the paragraph it belongs to directly, so lists stay tight.
    out = ""
    for index, block in enumerate(blocks):
        if index:
            out += "\n" if block["type"] in ("bulletList", "orderedList") else "\n\n"
        out += _markdown(block)
    return out


# Only what Markdown would read as syntax: underscores inside words and plain brackets stay as typed.
_ESCAPE = re.compile(
    r"([\\`*$]|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])|\[(?=[^\]]*\]\()|<(?=[A-Za-z/!?]))"
)


def _text(nodes):
    out = []
    for node in nodes:
        if node["type"] == "hardBreak":
            out.append("\\\n")
            continue
        marks = {mark["type"]: mark for mark in node.get("marks", [])}
        text = node.get("text", "")
        if "math" in marks:
            text = f"${text}$"
        elif "code" in marks:
            text = f"`{text}`" if "`" not in text else f"`` {text} ``"
        else:
            text = _ESCAPE.sub(r"\\\1", text)
        for name, wrap in (("strike", "~~"), ("italic", "*"), ("bold", "**")):
            if name in marks:
                text = f"{wrap}{text}{wrap}"
        if "link" in marks:
            text = f"[{text}]({marks['link'].get('attrs', {}).get('href', '')})"
        out.append(text)
    return "".join(out)


def agents(document):
    """The agent slots a document places, in reading order."""
    found = []

    def visit(node):
        if node.get("type") == "agent":
            found.append(node["attrs"]["slot"])
        for child in node.get("content", []):
            visit(child)

    visit(document)
    return found


TEMPLATES = Path(__file__).parent / "templates"


def templates():
    """The built-in report templates, each a name, a title and its document."""
    found = []
    for path in sorted(TEMPLATES.glob("*.md")):
        text = path.read_text()
        title, _, rest = text.partition("\n")
        found.append(
            {
                "name": path.stem,
                "title": title.removeprefix("<!-- ").removesuffix(" -->"),
                "document": from_markdown(rest),
            }
        )
    return found
