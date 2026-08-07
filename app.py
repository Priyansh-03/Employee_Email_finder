# SPDX-License-Identifier: Apache-2.0
import csv
import io
import json
import os
import queue
import sys
import threading
import time

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from email_finder import (
    DomainSmtpPacer,
    MxSessionCache,
    company_slug_from_first_domain,
    find_company_domains,
    generate_domain_variations,
    learned_domains_for_slug,
    load_learned_patterns,
    match_learned_slug,
    merge_domain_lists_learned_first,
    predict_emails,
    two_pass_find_email,
)

app = Flask(__name__)
CORS(app)  # Allow requests from the Chrome extension

CSV_MAX_ROWS = 500
CSV_ROW_DELAY_SEC = 0.25


def _split_display_name(name):
    name = (name or "").strip()
    if not name:
        return "", ""
    parts = name.split()
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def _norm_header(h):
    return (h or "").strip().lower().lstrip("\ufeff")


def _build_predict_inputs(company):
    """Same domain list as /predict_emails (learned + Clearbit + variations)."""
    store = load_learned_patterns()
    learned_slug = match_learned_slug(company, store)
    learned_hosts = (
        learned_domains_for_slug(store, learned_slug) if learned_slug else []
    )
    if learned_slug and not learned_hosts:
        learned_slug = None
    clearbit = find_company_domains(company)
    merged = merge_domain_lists_learned_first(learned_hosts, clearbit)
    if not merged:
        merged = [f"{company.lower().replace(' ', '')}.com"]
    target_domains = generate_domain_variations(merged)
    learned_lower = {h.lower() for h in learned_hosts}
    clearbit_lower = {c.lower() for c in (clearbit or [])}
    domain_sources = {}
    for d in target_domains:
        lo = d.lower()
        if lo in learned_lower:
            domain_sources[lo] = "learned"
        elif lo in clearbit_lower:
            domain_sources[lo] = "clearbit"
        else:
            domain_sources[lo] = "unknown"
    company_slug = learned_slug or company_slug_from_first_domain(merged)
    return target_domains, domain_sources, company_slug


def _risk_tier(confidence):
    if confidence >= 80:
        return "LOW"
    if confidence >= 60:
        return "MEDIUM"
    return "HIGH"


def _decision_tier(confidence):
    if confidence >= 75:
        return "SAFE_TO_SEND"
    if confidence >= 50:
        return "TRY_WITH_CAUTION"
    return "AVOID"


def _short_reason(signals, confidence):
    parts = []
    if signals.get("smtp_verified"):
        parts.append("SMTP verified")
    ps = signals.get("pattern_strength")
    if ps is not None and ps >= 0.45:
        parts.append("High pattern frequency")
    elif ps is not None and ps > 0:
        parts.append("Common company pattern")
    ds = signals.get("domain_source")
    if ds == "learned":
        parts.append("Saved company domain")
    if not parts:
        if confidence >= 75:
            parts.append("High confidence match")
        elif confidence >= 50:
            parts.append("Moderate confidence")
        else:
            parts.append("Low confidence match")
    return "; ".join(parts[:3])


def _predict_row(first_name, last_name, company, mx_cache, smtp_pacer):
    target_domains, domain_sources, company_slug = _build_predict_inputs(company)
    silent = lambda *args, **kwargs: None
    return predict_emails(
        first_name,
        last_name,
        target_domains,
        company_slug=company_slug,
        domain_sources=domain_sources,
        progress_callback=silent,
        cancel_event=None,
        mx_cache=mx_cache,
        smtp_pacer=smtp_pacer,
    )


def _row_to_output(display_name, company, pred):
    verified = pred.get("verified_email")
    cands = pred.get("candidates") or []
    best = None
    if verified:
        for c in cands:
            if c.get("email") == verified:
                best = c
                break
        if not best and cands:
            best = cands[0]
    elif cands:
        best = cands[0]

    if not best:
        return {
            "Name": display_name,
            "Company": company,
            "Email": "",
            "Confidence": "",
            "Risk": "HIGH",
            "Decision": "NO_CONFIDENT_RESULT",
            "Reason": "No high-confidence email available",
        }

    email = best.get("email", "")
    conf = int(best.get("confidence", 0))
    sig = best.get("signals") or {}
    risk = _risk_tier(conf)
    decision = _decision_tier(conf)
    reason = _short_reason(sig, conf)
    return {
        "Name": display_name,
        "Company": company,
        "Email": email,
        "Confidence": str(conf),
        "Risk": risk,
        "Decision": decision,
        "Reason": reason,
    }


UPLOAD_PAGE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Email validation CSV</title></head>
<body>
<h1>Validate emails from CSV</h1>
<p>CSV must have columns: <strong>Name</strong>, <strong>Company</strong>. Max """ + str(
    CSV_MAX_ROWS
) + """ rows.</p>
<form method="post" action="/upload_csv" enctype="multipart/form-data">
<p><input type="file" name="file" accept=".csv,text/csv" required></p>
<p><button type="submit">Upload and download results</button></p>
</form>
<p>For one person at a time, use <code>POST /predict_emails</code> with JSON.</p>
</body></html>"""


@app.route("/", methods=["GET"])
def index():
    return Response(UPLOAD_PAGE_HTML, mimetype="text/html")


@app.route("/upload_csv", methods=["POST"])
def upload_csv():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"success": False, "message": "No file uploaded."}), 400

    raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return jsonify({"success": False, "message": "CSV has no header row."}), 400

    colmap = {_norm_header(h): h for h in reader.fieldnames}
    name_key = colmap.get("name")
    company_key = colmap.get("company")
    if not name_key or not company_key:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "CSV needs columns: Name, Company (any case).",
                }
            ),
            400,
        )

    rows_in = []
    for row in reader:
        name = (row.get(name_key) or "").strip()
        company = (row.get(company_key) or "").strip()
        if not name and not company:
            continue
        rows_in.append((name, company))

    if len(rows_in) > CSV_MAX_ROWS:
        return (
            jsonify(
                {
                    "success": False,
                    "message": f"Too many rows ({len(rows_in)}). Max is {CSV_MAX_ROWS}.",
                }
            ),
            400,
        )

    mx_cache = MxSessionCache()
    smtp_pacer = DomainSmtpPacer(min_interval=0.4)
    out_rows = []
    for display_name, company in rows_in:
        first, last = _split_display_name(display_name)
        if not company:
            out_rows.append(
                {
                    "Name": display_name,
                    "Company": "",
                    "Email": "",
                    "Confidence": "",
                    "Risk": "HIGH",
                    "Decision": "NO_CONFIDENT_RESULT",
                    "Reason": "Missing company",
                }
            )
            time.sleep(CSV_ROW_DELAY_SEC)
            continue
        if not first:
            out_rows.append(
                {
                    "Name": display_name,
                    "Company": company,
                    "Email": "",
                    "Confidence": "",
                    "Risk": "HIGH",
                    "Decision": "NO_CONFIDENT_RESULT",
                    "Reason": "Missing name",
                }
            )
            time.sleep(CSV_ROW_DELAY_SEC)
            continue

        try:
            pred = _predict_row(first, last, company, mx_cache, smtp_pacer)
            out_rows.append(_row_to_output(display_name, company, pred))
        except Exception as ex:
            out_rows.append(
                {
                    "Name": display_name,
                    "Company": company,
                    "Email": "",
                    "Confidence": "",
                    "Risk": "HIGH",
                    "Decision": "NO_CONFIDENT_RESULT",
                    "Reason": f"Error: {ex}",
                }
            )
        time.sleep(CSV_ROW_DELAY_SEC)

    buf = io.StringIO()
    fieldnames = [
        "Name",
        "Company",
        "Email",
        "Confidence",
        "Risk",
        "Decision",
        "Reason",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(out_rows)
    data = buf.getvalue().encode("utf-8-sig")

    return Response(
        data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=email_validation_results.csv",
        },
    )


@app.route('/find_email', methods=['POST'])
def api_find_email():
    data = request.json
    first_name = data.get('first_name')
    last_name = data.get('last_name')
    company = data.get('company')
    
    if not all([first_name, last_name, company]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    print(f"\nRequest: {first_name} {last_name} at {company}")

    def api_progress(msg, _current_email=None):
        print(msg)

    email = two_pass_find_email(
        first_name, last_name, company, progress_callback=api_progress
    )
    
    if email:
        print(f"Found: {email}")
        return jsonify({"success": True, "email": email})
    else:
        print("Not found.")
        return jsonify({"success": False, "message": "Not found."})


@app.route("/predict_emails", methods=["POST"])
def api_predict_emails():
    """
    Ranked top candidates with confidence scores and lazy SMTP (up to 3 probes).
    Does not replace /find_email; intended for SaaS-style consumption.
    """
    data = request.json or {}
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    company = data.get("company")
    if not all([first_name, last_name, company]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    store = load_learned_patterns()
    learned_slug = match_learned_slug(company, store)
    learned_hosts = (
        learned_domains_for_slug(store, learned_slug) if learned_slug else []
    )
    if learned_slug and not learned_hosts:
        learned_slug = None

    clearbit = find_company_domains(company)
    merged = merge_domain_lists_learned_first(learned_hosts, clearbit)
    if not merged:
        merged = [f"{company.lower().replace(' ', '')}.com"]
    target_domains = generate_domain_variations(merged)
    learned_lower = {h.lower() for h in learned_hosts}
    clearbit_lower = {c.lower() for c in (clearbit or [])}
    domain_sources = {}
    for d in target_domains:
        lo = d.lower()
        if lo in learned_lower:
            domain_sources[lo] = "learned"
        elif lo in clearbit_lower:
            domain_sources[lo] = "clearbit"
        else:
            domain_sources[lo] = "unknown"

    company_slug = learned_slug or company_slug_from_first_domain(merged)
    conf_th = int(data.get("confidence_threshold", 75))
    max_smtp = int(data.get("max_smtp_attempts", 3))
    result = predict_emails(
        first_name,
        last_name,
        target_domains,
        company_slug=company_slug,
        domain_sources=domain_sources,
        confidence_threshold=conf_th,
        max_smtp_attempts=max_smtp,
    )
    return jsonify({"success": True, **result})


# Global dictionary to hold cancellation events for active sessions
active_sessions = {}

@app.route('/stop_search', methods=['POST'])
def stop_search():
    session_id = request.json.get('session_id')
    if session_id and session_id in active_sessions:
        active_sessions[session_id].set()
        return jsonify({"success": True, "message": "Search cancelled."})
    return jsonify({"success": False, "message": "Session not found."}), 404

@app.route('/stream_find_email')
def stream_find_email():
    first_name = request.args.get('first_name')
    last_name = request.args.get('last_name')
    company = request.args.get('company')
    session_id = request.args.get('session_id')
    
    if not all([first_name, last_name, company, session_id]):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    def generate():
        q = queue.Queue()
        cancel_event = threading.Event()
        active_sessions[session_id] = cancel_event
        
        def progress_callback(msg, current_email=None):
            q.put({"type": "progress", "message": msg, "current_email": current_email})
            
        def worker():
            try:
                if cancel_event.is_set():
                    return

                email = two_pass_find_email(
                    first_name,
                    last_name,
                    company,
                    progress_callback,
                    cancel_event,
                )
                
                if cancel_event.is_set():
                    q.put({"type": "result", "success": False, "message": "Search stopped by user.", "cancelled": True})
                elif email:
                    q.put({"type": "result", "success": True, "email": email})
                else:
                    q.put({"type": "result", "success": False, "message": "Not found."})
            except Exception as e:
                q.put({"type": "result", "success": False, "message": str(e)})
            finally:
                q.put({"type": "done"})
                if session_id in active_sessions:
                    del active_sessions[session_id]
                
        threading.Thread(target=worker).start()
        
        while True:
            item = q.get()
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("type") == "done":
                break

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Email finder server: http://127.0.0.1:5000")
    print("Ready for the browser extension.")
    app.run(port=5000, debug=True)