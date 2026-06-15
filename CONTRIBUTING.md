# Contributing

Contributions are welcome.

Please keep changes aligned with Fund Registry's narrow trust boundary:

- Do not imply identity verification.
- Do not imply campaign legitimacy verification.
- Do not imply custody, refunds, or donor safety guarantees.
- Keep wallet-control proof separate from campaign truth.

Before opening a pull request, run:

```bash
python -W ignore::ResourceWarning -m unittest tests.test_fund_registry -q
python -m py_compile app.py bitcoin_address.py anchor_preflight.py payment_preflight.py promo_codes.py sweep_pages.py
```

For security issues, email `contact@satoshidata.ai` instead of opening a public
issue.
