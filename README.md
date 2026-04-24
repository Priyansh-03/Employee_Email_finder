# Employee Email Finder

**Short description (for GitHub “About”):** Resolve employer domains from a company name, generate common corporate email patterns, and check deliverability via DNS MX and SMTP `RCPT TO`—without sending email bodies. Ships with a Chrome extension UI backed by a local Flask service.

## What it does

- **Domain discovery:** Normalizes the company string, queries a public company-autocomplete HTTP API, filters obvious non-corporate hostnames (for example hiring or blog sub-brands), and expands common TLD variants with **`.com` tried first**.
- **Permutations:** Builds a set of typical `local-part` patterns (first/last, initials, separators, and order variants).
- **Validation:** For each candidate, resolves MX, detects many catch-all domains, and opens SMTP to port 25 to ask whether the mailbox is accepted—then disconnects before data transfer.
- **Parallelism:** Up to five worker threads with jitter and politeness delays to reduce rate limits; optional cancel from the extension.
- **Chrome extension:** Popup talks to `http://127.0.0.1:5000` (streaming progress and stop). **You must run the Python backend on the same machine.**

## Requirements

- Python 3.10+
- Network access to outbound TCP 25 (often blocked on residential ISPs; a VPS or office network may work better)
- Chrome (or Chromium) for the extension

## Quick start

```bash
cd employee-email-finder
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then in Chrome: `chrome://extensions` → Developer mode → **Load unpacked** → select the `chrome_extension` folder.

CLI usage:

```bash
source venv/bin/activate
python email_finder.py <first_name> <last_name> "<company_name>"
```

If automatic domain lookup fails, the CLI prompts for a primary domain (for example `example.com`).

## Responsible use

Use only where you have a lawful basis (contract, legitimate interest where allowed, or consent). Many jurisdictions restrict automated contact discovery or unsolicited email. This tool performs **technical mailbox probes**; misuse can violate provider terms, anti-spam law, or computer-misuse rules. You are responsible for compliance.

## License

Licensed under the **Apache License, Version 2.0**. See [`LICENSE`](LICENSE).

## Publishing this repository to GitHub

SSH push works once the empty repository exists on GitHub.

**Option A — GitHub web UI**

1. Create a new public repository named `employee-email-finder` (no README, no `.gitignore`, no license—keep it empty to avoid merge conflicts).
2. From this project directory:

```bash
git init
git branch -M main
git add .
git commit -m "Initial commit: Employee Email Finder"
git remote add origin git@github.com:Priyansh-03/employee-email-finder.git
git push -u origin main
```

**Option B — API with a token**

```bash
export GH_TOKEN='your_github_pat_with_repo_scope'
./scripts/create_github_repo.sh
git remote add origin git@github.com:Priyansh-03/employee-email-finder.git   # if not already set
git push -u origin main
```

Set `GITHUB_OWNER` if the owner is not `Priyansh-03`.
