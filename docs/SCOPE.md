# Scope

Draft for correction. Nothing here is built yet.

## Cairn

Cairn stores the results of programs and finds them again. It is the base other tools
build on, so it changes rarely and knows none of them.

- **Data:** runs and other artifacts, their records, files and typed values, versions,
  aliases, dependencies, tags and other properties, heartbeats.
- **Writing:** the Python SDK, the outbox, the CLI and `/v1`.
- **Reading:** references, record queries, lists and search, lineage, live records,
  permanent URLs for an artifact's files and values.
- **Keeping:** export, import, check, garbage collection, migration.

Not in Cairn: reports, documents, comments, workspaces, layouts, templates, agents, and
any web page. A test fails when the schema or `/v1` changes unannounced.

## cairn-ui

A program that shows what Cairn holds, live. It talks to Cairn through `/v1` only, and
Cairn runs without it.

- Pages for projects, artifacts, lineage and files.
- The workspace: live curves, images, video, figures and viewers over runs.
- Its own storage for what it alone defines: workspaces, layouts, hidden runs. Agents
  edit workspaces through cairn-ui's own command.

Not in cairn-ui: runs and artifacts (it keeps no copy), reports, comments.

## Elsewhere

Ideas, arguments and reports are written in other tools (Pumice, Anthropic reports,
LaTeX, Markdown). They cite evidence by reference or URL. Neither part of Cairn knows
about them.
