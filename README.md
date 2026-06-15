# Fund Registry

Fund Registry is a self-hostable registry for canonical Bitcoin funding pages.
It helps a creator or project publish one official Bitcoin receiving address,
optionally prove wallet control, and give supporters a page they can verify
before sending funds.

Fund Registry is not a crowdfunding platform, custodian, donor CRM, identity
verification service, or campaign-legitimacy checker.

Its narrow trust claim is:

- this page is the canonical page on the registry
- for paid proof states, the listed Bitcoin wallet participated in the proof flow
- the page has a visible lifecycle state such as active, expired, aborted,
  compromised, or dead

It does not prove who the creator is, whether a story is true, whether donating
is safe, or whether a beneficiary is legitimate.

## Features

- Public Bitcoin funding pages
- Campaign Key based page management
- Optional wallet-control proof
- Optional Bitcoin Core payment and anchor flows
- Public verification pages and badge/button payloads
- Page lifecycle states: active, expired, dead, aborted, compromised
- Static frontend plus FastAPI backend
- SQLite persistence for small/self-hosted deployments

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:43134
```

By default, payment, wallet proof, and Bitcoin anchoring are disabled. That is
intentional. Enable them only after configuring Bitcoin Core and running the
preflight checks.

## Configuration

See [ENV-VARS.md](ENV-VARS.md) and [.env.example](.env.example).

The most important defaults:

- `FUND_REGISTRY_PAYMENT_MODE=disabled`
- `FUND_REGISTRY_PROOF_MODE=disabled`
- `FUND_REGISTRY_ANCHOR_MODE=disabled`

Supported payment/proof/anchor modes are documented in the environment file.

## Bitcoin Core

Fund Registry can use `bitcoin-cli` for:

- payment receive addresses
- payment polling
- legacy Bitcoin signed-message verification
- OP_RETURN anchoring for tier3 proof events

Set these only when you deliberately want live Bitcoin Core integration:

```bash
BITCOIN_CLI_PATH=bitcoin-cli
BITCOIN_CONF_PATH=/path/to/bitcoin.conf
BITCOIN_WALLET_NAME=fund-registry-anchor
FUND_REGISTRY_PAYMENT_WALLET_NAME=fund-registry-payments
```

Run preflights before enabling live modes:

```bash
python payment_preflight.py
python anchor_preflight.py --allow-no-funds
```

## Development

```bash
python -W ignore::ResourceWarning -m unittest tests.test_fund_registry -q
python -m py_compile app.py bitcoin_address.py anchor_preflight.py payment_preflight.py promo_codes.py sweep_pages.py
```

## Security Notes

- Do not commit `data/`, SQLite databases, uploaded photos, message logs, or
  `.env` files.
- Treat Campaign Keys like passwords. The original secret cannot simply be
  revealed again later.
- Wallet proof proves control of the listed Bitcoin address at proof time. It
  does not prove identity or campaign truth.
- Keep dev actions disabled in production.
- Keep payment/proof/anchor modes disabled until your deployment is configured
  and smoke-tested.

Report security issues privately to `contact@satoshidata.ai`.

## License

MIT. See [LICENSE](LICENSE).
