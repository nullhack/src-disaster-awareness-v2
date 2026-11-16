# Disaster Awareness v2

An incident reference with a change feed. Static pages are generated from a Git-backed canonical record store and deployed to GitHub Pages.

## What is different from v1

- **The incident is the primary object.** The home page is a change feed, the incident page is canonical and shareable, and coverage is a subpage instead of a drawer.
- **Write model and read model are separate.** The `data` branch holds canonical records and append-only links. The site is a generated read model: a small index, slim day files, one JSON file per incident, and one coverage file per incident.
- **No 3.8 MB daily digest.** A day file is a few KB of incident references. Full coverage is fetched only when a visitor opens it.
- **Trends are generated counts, not a map.** The home page has a compact pulse; `/trends/` has accessible charts with table alternatives. They describe the tracked record, not disaster frequency.
- **GitHub stays the host.** GitHub Pages serves the static site; the `data` branch is the audit trail; GitHub Issues are the submission and correction queue.

## Repository layout

| Path | Holds |
| --- | --- |
| `src/disaster_v2/` | migration, source adapters, ingestion/search/log pipeline, store, ranking, build, and static renderer |
| `web/assets/` | hand-written CSS and vanilla JavaScript |
| `tests/` | migration, build, pipeline, and contract tests |
| `.github/workflows/` | validation, scheduled data updates, submissions, and Pages deployment |
| `config.toml` | site URL, repo, build defaults, OpenRouter model, and ingestion thresholds |
| `site/` | generated output, ignored by Git |
| `data/` | `data` branch worktree, ignored by Git |

The `data` branch is an orphan branch. It contains:

```text
records/incidents/<id>.yaml
records/reports/<id>.yaml
records/news/<id>.yaml
records/logs/<incident>/<date>.yaml
links/<yyyy-mm>.jsonl
events/<yyyy-mm>.jsonl
migration-report.json
```

Records are immutable evidence where possible. Relationships are explicit edges in `links/`, not file locations. A merge should add an alias or `merged_into` pointer, never delete the old record.

## Local development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run pyright src

# Build from a data worktree
uv run disaster-v2-build --data data --out site --config config.toml
```

Open `site/index.html` through a local web server, not `file://`, because the site uses a `<base>` path for GitHub Pages.

## Ingestion pipeline

The v2 pipeline uses the same source adapters and decision logic as v1:

- **Official reports** — USGS (M5.5+ for news monitoring), GDACS (Orange/Red for news monitoring), WHO disease outbreak news, and ERCC daily maps.
- **News search** — DuckDuckGo candidates are filtered by the DSPy relevance classifier. Reports searched in the last 3 days are skipped; active incidents are repolled. Selected articles are fetched and normalized.
- **Logs** — unsummarized incident news is summarized only when it passes the minimum-news threshold and the DSPy update check. All news in the batch is linked to the log for provenance.
- **Submissions** — GitHub issues are fetched, classified, and either imported or sent to `submission-review` for a maintainer decision.

Run locally with an OpenRouter key:

```bash
export OPENROUTER_API_KEY="..."

uv run disaster-v2-ingest --data data --config config.toml -v
uv run disaster-v2-search-news --data data --config config.toml -v
uv run disaster-v2-search-news --data data --config config.toml --repoll -v
uv run disaster-v2-generate-logs --data data --config config.toml -v
```

Or run the full scheduled pipeline:

```bash
uv run disaster-v2-pipeline --data data --config config.toml -v
```

`disaster-v2-submissions` processes the GitHub issue queue and requires the `gh` CLI with repository issue access.

The model is configured once in `config.toml`:

```toml
[openrouter]
model = "openrouter/deepseek/deepseek-v4.1-flash-1017"
```

For GitHub Actions, add `OPENROUTER_API_KEY` as a repository secret. Scheduled updates still ingest official reports when the secret is absent; news search and log generation are skipped until it is set.

## Migrating a v1 data tree

The migration reads the old tree and writes a new canonical tree. It does not modify the source tree.

```bash
uv run disaster-v2-migrate \
  --source /path/to/src-disaster-awareness/data \
  --target data \
  --as-of 2026-11-16
```

Use `--force` to replace an existing `records/` and `links/` tree. The migration report records counts and warnings, including duplicate news IDs preserved under a content-addressed suffix.

## Deploy

`.github/workflows/pages.yml` checks out `main` and the `data` branch, builds `site/`, uploads it as a Pages artifact, and deploys it. Enable **Settings → Pages → Build and deployment → GitHub Actions** once for the repository.

Data workflows call `pages.yml` as a reusable workflow after they push to `data`:

- `update-data` runs twice a day: official ingestion, news search, repoll, then log generation.
- `process-submissions` runs three times a day and when a maintainer adds `submission-approved` to an issue.

Both write to the `data` branch under the shared `data-update` concurrency group, so they cannot race each other.

The site URL is configured in `config.toml`:

```toml
[site]
base_url = "https://nullhack.github.io/src-disaster-awareness-v2"
repo = "nullhack/src-disaster-awareness-v2"
```

## Limitations

- Migration preserves the old grouping; it does not re-identify or split legacy over-merged incidents.
- News coverage is collected automatically and can contain unrelated or syndicated items.
- This is not an official warning service.
