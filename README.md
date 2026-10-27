# My Bank API

🇫🇷 [Lire en français](https://github.com/Patafoin/my-bank-api/blob/main/README.fr.md)

Self-hosted, headless personal finance backend, modelled on desktop money managers such as MS Comptes Bancaires. Its main client is the Home Assistant integration [My Bank](https://github.com/Patafoin/ha-my-bank).

Python 3.13 · FastAPI · SQLAlchemy 2 + Alembic · SQLite (WAL). About 60–100 MB of RAM.

## Installation

### Home Assistant app (add-on)

Requires Home Assistant OS or a Supervised installation (amd64 or aarch64); tested on Home Assistant OS 18.3 with Home Assistant 2026.10.0. Settings → **Apps** → **Install app** → ⋮ → **Repositories**, add `https://github.com/Patafoin/ha-addons`, then install **My Bank API** (older Home Assistant versions call apps *add-ons*). The app creates a token for Home Assistant and announces itself: the My Bank integration is set up with one click. See the app documentation.

### Docker

```bash
curl -O https://raw.githubusercontent.com/Patafoin/my-bank-api/main/compose.example.yaml
openssl rand -hex 32   # generates a token: paste it as MYBANK_API_TOKEN in the file, keep it for Home Assistant
docker compose -f compose.example.yaml up -d
curl http://localhost:9931/api/v1/health
```

Multi-architecture image (amd64, arm64): `ghcr.io/patafoin/my-bank-api:<version>` or `:latest`. Schema migrations are applied automatically at start-up.

**Keep the API on your local network.** Never expose it to the Internet. If Home Assistant runs on the same host with `network_mode: host`, publish the port on the loopback interface only: `"127.0.0.1:9931:8000"` instead of `"9931:8000"`, and use `http://127.0.0.1:9931` in the integration. Keep `9931:8000` when Home Assistant runs on another machine or in a container on another Docker network.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `MYBANK_API_TOKEN` | — | Token accepted by the API (created or updated at start-up, stored hashed). The simplest way to give Home Assistant access. Removing the variable revokes it at the next start |
| `MYBANK_DATA_DIR` | `/data` | Folder of the `my_bank.sqlite` database |
| `MYBANK_BACKUP_DIR` | `/backups` (`/data/backups` in the Home Assistant app) | Folder of the nightly snapshots |
| `MYBANK_BACKUP_HOUR` | `3` | Local hour of the nightly backup |
| `MYBANK_BACKUP_KEEP_DAILY` / `_MONTHLY` | `14` / `12` | Rotation |
| `MYBANK_JOBS_ENABLED` | `true` | Daily jobs (generation of scheduled transactions, backup) |
| `MYBANK_DOCS` | `false` | Interactive documentation `/docs`, `/redoc` and `/openapi.json`. Off by default because these pages are readable without a token (they describe the API, not your data). Set `true` to browse them |

The container drops root privileges by itself (user `mybank`, uid 1000); `user: "1000:1000"` in compose works as well.

## Command line

```bash
docker exec my-bank-api my-bank token create ha     # prints the token once
docker exec my-bank-api my-bank token list
docker exec my-bank-api my-bank token revoke ha
docker exec my-bank-api my-bank backup              # consistent SQLite snapshot now
```

Backups: a consistent snapshot (SQLite backup API) at start-up if there is none for the day, then every night; 14 daily + 1 per month for 12 months. Copy the `backups` folder, not the live database.

## Concepts

- **Amounts**: decimal strings in the API (`"-14.80"`, a comma is accepted on input), integer cents in the database.
- **Transaction state**: `0` not cleared, `1` cleared, `2` reconciled.
- **Accounts**: a currency (ISO 4217, e.g. EUR, USD, GBP; defaults to EUR), one of them can be `is_default` (preselected wherever an account is chosen; setting a new one clears the previous).
- **Three balances per account**: `total` (every transaction, including future-dated ones, unless `date` is given), `pointed` (states 1 and 2), `reconciled` (state 2). The opening balance is included in all three.
- **Reconciled transactions are locked**: amount, date, account, splits and deletion are refused (409) until the transaction is un-reconciled.
- **Splits**: every transaction has at least one line; their sum equals the amount.
- **Transfers**: two twin transactions (`transfer_peer_id`); amount and date follow from one leg to the other, the cleared state stays per leg. Refused (422) between two accounts of different currencies: the app never converts.
- **Categories**: unlimited tree, typed `income` / `expense` / `savings` (inherited from the root). **Classes**: a second classification axis, also a tree.
- **Scheduled transactions** (`/schedules`): a repetition rule (first date, every N days / weeks / months / quarters / years, ending never / on a date / after N occurrences) and a transaction template (payee, payment method, category, class, or a transfer to another account). Mode `periodic` (fixed amount) or `installments` (finite plan, one amount — and optionally one date — per occurrence, `PUT /schedules/{id}/occurrences`). Dates are computed from the first occurrence (31 January → 28/29 February → 31 March). Occurrences are generated at start-up, every night and on creation, when their date ≤ today + `lead_days`, never before `generate_from`; each one only once. An occurrence can be skipped, restored or entered early by hand.
- **Budgets** (`/budgets`): one budget per account and month range (no overlap), a categories × months grid (`PUT /budgets/{id}/lines`), copy to a new period, planned / actual / variance report. Planned amounts are positive in the natural direction of the category; a category's figures include its sub-categories; a positive variance is favourable.
- **Bulk move** (`POST /{accounts|categories|classes|payees}/{id}/move`, `{"target_id": …}`): every transaction and schedule of an item moves to another one. Accounts and payees expose a `transaction_count`.
- **Shared expenses** (`/shared-accounts`, independent from bank accounts): participants (at least two), expenses (who paid, positive amount, date, label). Each share = total ÷ participants, remaining cents going to the biggest payers; next payer = whoever paid least; settlement = at most n − 1 transfers. Closing (`PATCH {"closed": true}`) freezes the account; it can be reopened.
- **Attachments** (`PUT`/`DELETE /transactions/{id}/attachment`, `GET .../attachment/content`): one per transaction, either a link or a file (base64 on input, 10 MiB max, stored in the database); replacing one removes the previous one.

## API

Prefix `/api/v1`, header `Authorization: Bearer <token>` on everything but `/health`. Interactive documentation: `/docs` (descriptions in French), with `MYBANK_DOCS=true` (option **docs** in the Home Assistant app).

Error messages follow the `Accept-Language` header: `en`, `fr` or `de` (English for any other language, French when the header is absent). The Home Assistant integration sends the language of Home Assistant. Translations live in `src/my_bank_api/i18n_messages.py`, keyed by the French text passed to `tr()`; a test checks that every message has an English and a German version.

## Import from MS Comptes Bancaires

`POST /api/v1/import/ms` with the QIF exports and the payee CSV, base64-encoded; `dry_run` is `true` by default (the report is computed, nothing is saved). Only into empty accounts. Command-line equivalent:

```bash
docker cp payees.csv my-bank-api:/tmp/ && docker cp checking.qif my-bank-api:/tmp/
docker exec my-bank-api my-bank import-ms --payees /tmp/payees.csv "Checking=/tmp/checking.qif"            # dry run
docker exec my-bank-api my-bank import-ms --apply --payees /tmp/payees.csv "Checking=/tmp/checking.qif"    # for real
```

## Demo data

`scripts/demo_data.py` fills an **empty** instance with fictitious data (accounts, four months of transactions, schedules, a budget, shared expenses), in English, French or German. Standard library only:

```bash
python3 scripts/demo_data.py --url http://localhost:9931 --token <token> --lang en
```

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q             # fast tests
.venv/bin/python -m pytest -q -m slow -s  # load test, 100,000 transactions
.venv/bin/ruff check src tests scripts
```

New schema migration (after changing `models.py`):

```bash
.venv/bin/python -c "from alembic import command; from my_bank_api.migrate import alembic_config; \
command.upgrade(c := alembic_config('sqlite:////tmp/gen.sqlite'), 'head'); \
command.revision(c, message='description', autogenerate=True, rev_id='0004')"
```

Release: bump the version in `pyproject.toml` and `src/my_bank_api/__init__.py`, tag `vX.Y.Z`: GitHub Actions publishes the image; then set the same version in the app's `config.yaml` ([ha-addons](https://github.com/Patafoin/ha-addons)).

## License

MIT
