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

This tree is already initialized as a `git` repository with an initial commit. Use SSH to push after the empty GitHub repository exists.

**Suggested “About” description (paste into GitHub):**

> Resolve employer domains from a company name, generate common corporate email patterns, and check deliverability via DNS MX and SMTP `RCPT TO` without sending message bodies. Includes a Chrome extension backed by a local Flask API.

**Option A — create an empty repo in the browser, then push**

1. On GitHub: **New repository** → name `Employee_Email_finder` (or your chosen name) → public → do **not** add README, `.gitignore`, or license (avoids merge conflicts).
2. In this folder:

```bash
git remote remove origin 2>/dev/null || true
git remote add origin git@github.com:Priyansh-03/Employee_Email_finder.git
git push -u origin main
```

Change `Priyansh-03` if your account or organization name differs.

**Option B — create the repo with the REST API, then push**

Requires a [personal access token](https://github.com/settings/tokens) with permission to create repositories.

```bash
export GH_TOKEN='your_token_here'
./scripts/create_github_repo.sh
git remote remove origin 2>/dev/null || true
git remote add origin git@github.com:Priyansh-03/Employee_Email_finder.git
git push -u origin main
```

Optional environment variables: `GITHUB_OWNER` (for the clone URL printed in the script message), `GITHUB_REPO` (must match the GitHub repo name, e.g. `Employee_Email_finder`), `GITHUB_REPO_DESCRIPTION`.

**Upstream repository:** [github.com/Priyansh-03/Employee_Email_finder](https://github.com/Priyansh-03/Employee_Email_finder)
