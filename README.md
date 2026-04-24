# Employee Email Finder

Given a person’s first name, last name, and employer name, this tool suggests a likely corporate email address. You can use it from the command line or from a small Chrome extension (the extension needs a small server running on your computer).

## What you need

- **Python 3.10+**
- **Google Chrome** (or Chromium), if you use the extension
- A network where outbound email checks are allowed (some home networks block this)

## How to use — Chrome extension

1. Open a terminal in this project folder.
2. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Start the backend (leave this terminal open):

   ```bash
   python app.py
   ```

4. In Chrome, go to `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose the `chrome_extension` folder inside this project.
5. Open the extension from the toolbar, enter **first name**, **last name**, and **company name**, then click **Find email**.
6. Use **Stop search** if you want to cancel a long run.

The extension only talks to your machine at `http://127.0.0.1:5000`; it does not send your inputs anywhere else by itself.

## How to use — command line

With the virtual environment activated (see step 2 above):

```bash
python email_finder.py <first_name> <last_name> "<company_name>"
```

Example shape (use your own values):

```bash
python email_finder.py given-name family-name "Company Legal Name Inc"
```

If the tool cannot infer a website domain from the company name, it will ask you to type one (such as `example.com`).

Successful lookups append a small local record under `learned_patterns.json` (pattern id and mail host only, not full addresses) so later searches can try known-good domains first.

## Responsible use

Only use this where you are allowed to (law, contract, and company policy). Do not use it to harass people or to break rules on unsolicited contact.

## License

Apache License 2.0 — see the `LICENSE` file.
