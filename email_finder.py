# SPDX-License-Identifier: Apache-2.0
import smtplib
import dns.resolver
import socket
import time
import random
import string
import requests
import argparse
import sys
import re
import os
import json
import tempfile
import concurrent.futures
import threading
from datetime import datetime, timezone
from urllib.parse import quote

# Must stay in lockstep with `generate_permutations` (same length and order).
PERMUTATION_PATTERN_KEYS = (
    "first_dot_last",
    "first_last",
    "first_underscore_last",
    "first_hyphen_last",
    "last_dot_first",
    "last_first",
    "first_only",
    "last_only",
    "f_initial_last",
    "f_dot_last",
    "f_underscore_last",
    "first_l_initial",
    "first_dot_l_initial",
    "first_underscore_l_initial",
    "last_f_initial",
    "last_dot_f_initial",
    "l_initial_first",
    "l_dot_first",
)

_learned_io_lock = threading.Lock()

def clean_company_name(name):
    """Remove common corporate suffixes to improve API search results."""
    # Remove common punctuation
    cleaned = re.sub(r'[.,()\[\]{}]', '', name)
    
    # List of common corporate entity suffixes
    legal_suffixes = [
        r'\bpvt\b', r'\bprivate\b', r'\bltd\b', r'\blimited\b',
        r'\binc\b', r'\bincorporated\b', r'\bllc\b', r'\bcorp\b', 
        r'\bcorporation\b', r'\bco\b', r'\bcompany\b', r'\bllp\b',
        r'\bplc\b', r'\bgmbh\b', r'\bsa\b', r'\bspa\b'
    ]
    
    for suffix in legal_suffixes:
        cleaned = re.sub(suffix, '', cleaned, flags=re.IGNORECASE)
        
    # Clean up extra spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def find_company_domains(company_name):
    """Resolve a company name to candidate domains via a public autocomplete HTTP API."""
    
    # 1. Strip legal suffixes (e.g. "Example Org Pvt Ltd" -> "Example Org")
    cleaned_name = clean_company_name(company_name)
    
    print(f"[*] Searching for official domain for company: '{cleaned_name}' (Original: '{company_name}')...")
    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(cleaned_name)}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            domains = [item['domain'] for item in data if 'domain' in item]
            
            if domains:
                print(f"[+] Found matching domains: {', '.join(domains)}")
                return domains
                
        # 2. If no domains found, try removing generic tech words (e.g. "Name Cloud Solutions" -> "Name Cloud")
        tech_words = [r'\bsolutions\b', r'\btechnologies\b', r'\bsoftware\b', r'\bsoftech\b', r'\btech\b', r'\bit\b']
        ultra_cleaned = cleaned_name
        for word in tech_words:
            ultra_cleaned = re.sub(word, '', ultra_cleaned, flags=re.IGNORECASE)
        ultra_cleaned = re.sub(r'\s+', ' ', ultra_cleaned).strip()
        
        if ultra_cleaned and ultra_cleaned != cleaned_name:
            print(f"[*] Retrying search without generic words: '{ultra_cleaned}'...")
            url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(ultra_cleaned)}"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                domains = [item['domain'] for item in data if 'domain' in item]
                
                if domains:
                    print(f"[+] Found matching domains: {', '.join(domains)}")
                    return domains
                    
        # 3. Ultimate fallback: first token only (e.g. "Foo Bar Baz" -> "Foo")
        if ' ' in ultra_cleaned:
            first_word = ultra_cleaned.split(' ')[0]
            print(f"[*] Retrying search with first word only: '{first_word}'...")
            url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(first_word)}"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                domains = [item['domain'] for item in data if 'domain' in item]
                
                if domains:
                    print(f"[+] Found matching domains: {', '.join(domains)}")
                    return domains
                    
        print("[-] No domains found automatically.")
    except Exception as e:
        print(f"[-] Error finding domain: {e}")
        
    return []

def get_mx_record(domain):
    """Get the MX record for a given domain."""
    try:
        records = dns.resolver.resolve(domain, 'MX')
        mx_record = records[0].exchange
        return str(mx_record)
    except Exception as e:
        # Could not resolve MX record
        return None

def is_catch_all(domain, mx_record):
    """Check if the domain has a catch-all email configuration."""
    random_string = ''.join(random.choices(string.ascii_lowercase + string.digits, k=15))
    fake_email = f"{random_string}@{domain}"
    
    return verify_email_smtp(fake_email, mx_record, domain, is_catch_all_check=True)

def verify_email_smtp(email, mx_record, domain, is_catch_all_check=False):
    """Verify an email address by communicating with the SMTP server."""
    # Reserved example domain (RFC 2606) — avoids impersonating a real mail provider in HELO
    host = 'mail.example.com'
    
    try:
        # Increase timeout slightly for slower corporate servers
        server = smtplib.SMTP(timeout=10)
        server.set_debuglevel(0)
        
        # Connect to the mail server
        server.connect(mx_record)
        server.helo(host)
        
        # Use a generic sender address
        server.mail("noreply@example.com")
        
        # Check the recipient
        code, message = server.rcpt(email)
        server.quit()
        
        # 250 means the email address is valid/accepted
        if code == 250:
            return True
        return False
        
    except Exception as e:
        # Connection errors, timeouts, etc.
        if not is_catch_all_check:
            print(f"  [!] SMTP Error for {email}: {e}")
        return None

def generate_permutations(first_name, last_name, domain):
    """Generate comprehensive email format permutations (expanded from 12 to 18)."""
    first = first_name.lower()
    last = last_name.lower()
    f = first[0]
    l = last[0]
    
    return [
        f"{first}.{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}_{last}@{domain}",
        f"{first}-{last}@{domain}",
        f"{last}.{first}@{domain}",
        f"{last}{first}@{domain}",
        f"{first}@{domain}",
        f"{last}@{domain}",
        f"{f}{last}@{domain}",
        f"{f}.{last}@{domain}",
        f"{f}_{last}@{domain}",
        f"{first}{l}@{domain}",
        f"{first}.{l}@{domain}",
        f"{first}_{l}@{domain}",
        f"{last}{f}@{domain}",
        f"{last}.{f}@{domain}",
        f"{l}{first}@{domain}",
        f"{l}.{first}@{domain}"
    ]

def generate_domain_variations(domains):
    """Generate common TLD variations for a given list of domains."""
    variations = []
    
    # Words that indicate a domain is likely for marketing/hiring, not employee emails
    spammy_keywords = ['jobs', 'blog', 'careers', 'news', 'shop', 'store', 'support', 'help']
    
    # Filter out domains that contain spammy keywords
    filtered_domains = []
    for d in domains:
        base_name = d.split('.')[0].lower()
        if not any(keyword in base_name for keyword in spammy_keywords):
            filtered_domains.append(d)
            
    # Keep original domains first
    for d in filtered_domains:
        if d not in variations:
            variations.append(d)
            
    common_tlds = ['.com', '.co', '.in', '.io', '.net', '.org', '.ai']
    
    for domain in filtered_domains:
        if '.' in domain:
            # Extract the leftmost label as brand slug (e.g. 'exampleorg' from 'exampleorg.co.uk')
            base_name = domain.split('.')[0]
            
            for tld in common_tlds:
                new_domain = f"{base_name}{tld}"
                if new_domain not in variations:
                    variations.append(new_domain)
                    
    # Ensure .com domains are ALWAYS prioritized first
    coms = [d for d in variations if d.endswith('.com')]
    others = [d for d in variations if not d.endswith('.com')]
    return coms + others


def company_slug_from_first_domain(domains):
    """Leftmost label of the first domain (stable brand key)."""
    if not domains:
        return None
    first = domains[0].strip()
    if not first or "." not in first:
        return None
    return first.split(".")[0].lower()


def normalized_company_match_key(company_name):
    """Alphanumeric brand key for matching company input to learned JSON slugs."""
    c = clean_company_name(company_name or "")
    return re.sub(r"[^a-z0-9]", "", c.lower())


def match_learned_slug(company_name, store):
    """
    Pick a learned JSON key (slug) for this company name, if any.
    Prefers longest slug where slug.startswith(norm); else longest where norm.startswith(slug).
    """
    norm = normalized_company_match_key(company_name)
    if not norm or not isinstance(store, dict) or not store:
        return None
    best = None
    best_len = -1
    for slug in store:
        if not isinstance(slug, str):
            continue
        s = slug.lower()
        if s.startswith(norm) and len(s) > best_len:
            best_len = len(s)
            best = slug
    if best:
        return best
    best = None
    best_len = -1
    for slug in store:
        if not isinstance(slug, str):
            continue
        s = slug.lower()
        if len(s) >= 3 and norm.startswith(s) and len(s) > best_len:
            best_len = len(s)
            best = slug
    return best


def learned_domains_for_slug(store, slug):
    """Unique hostnames from successes for slug, newest rows first."""
    if not slug:
        return []
    sucs = normalized_successes_for_slug(store, slug)
    out = []
    seen = set()
    for s in sorted(
        sucs, key=lambda x: x.get("updated_at") or "", reverse=True
    ):
        d = (s.get("domain") or "").strip()
        if not d:
            continue
        low = d.lower()
        if low not in seen:
            seen.add(low)
            out.append(d)
    return out


def merge_domain_lists_learned_first(learned_doms, other_doms):
    """Dedupe case-insensitively; `learned_doms` order preserved, then others."""
    seen = set()
    out = []
    for d in learned_doms or []:
        d = (d or "").strip()
        low = d.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(d)
    for d in other_doms or []:
        d = (d or "").strip()
        low = d.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(d)
    return out


def learned_patterns_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_patterns.json")


def load_learned_patterns():
    path = learned_patterns_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_learned_patterns(store):
    path = learned_patterns_path()
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(
        prefix="learned_patterns.", suffix=".tmp", dir=directory or "."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def normalized_successes_for_slug(store, slug):
    """Return a list of success dicts for slug (supports legacy single-host shape)."""
    if not slug:
        return []
    row = store.get(slug)
    if not isinstance(row, dict):
        return []
    if isinstance(row.get("successes"), list):
        return [dict(s) for s in row["successes"] if isinstance(s, dict)]
    if row.get("preferred_domain"):
        return [
            {
                "domain": row["preferred_domain"],
                "pattern_key": row.get("pattern_key") or "unknown",
                "updated_at": row.get("updated_at", ""),
            }
        ]
    return []


def upsert_learned_success(slug, domain_host, pattern_key):
    """Append new (domain, pattern_key) rows; same pair only bumps updated_at."""
    if not slug or not domain_host or not pattern_key:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _learned_io_lock:
        store = load_learned_patterns()
        successes = normalized_successes_for_slug(store, slug)
        dlow = domain_host.lower()
        updated = False
        for s in successes:
            if (s.get("domain") or "").lower() == dlow and s.get("pattern_key") == pattern_key:
                s["updated_at"] = now
                updated = True
                break
        if not updated:
            successes.append(
                {"domain": domain_host, "pattern_key": pattern_key, "updated_at": now}
            )
        # Optional future cap: trim oldest rows if len(successes) exceeds N.
        store[slug] = {"successes": successes}
        save_learned_patterns(store)


def infer_pattern_key(first_name, last_name, local_part):
    if not local_part:
        return None
    target = local_part.strip().lower()
    keys = PERMUTATION_PATTERN_KEYS
    for key, email in zip(keys, generate_permutations(first_name, last_name, "x.com")):
        if email.split("@")[0].lower() == target:
            return key
    return None


def learned_pattern_keys_for_domain(successes, domain):
    """
    Distinct pattern_key values for this host, newest row first (case-insensitive domain).
    """
    dlow = domain.lower()
    keys_allowed = PERMUTATION_PATTERN_KEYS
    out = []
    seen = set()
    for s in sorted(
        successes, key=lambda x: x.get("updated_at") or "", reverse=True
    ):
        if (s.get("domain") or "").lower() != dlow:
            continue
        pk = s.get("pattern_key")
        if not pk or pk not in keys_allowed or pk in seen:
            continue
        seen.add(pk)
        out.append(pk)
    return out


def ordered_local_parts_with_preferred_keys(first_name, last_name, preferred_keys):
    """
    All permutation local-parts; those matching preferred_keys (in order) first,
    then the rest without duplicate local-part strings.
    """
    keys = PERMUTATION_PATTERN_KEYS
    template_emails = generate_permutations(first_name, last_name, "x.com")
    local_parts = [e.split("@")[0] for e in template_emails]
    preferred_keys = preferred_keys or []
    seen_lp = set()
    ordered = []
    for pk in preferred_keys:
        if pk not in keys:
            continue
        lp = local_parts[keys.index(pk)]
        low = lp.lower()
        if low not in seen_lp:
            seen_lp.add(low)
            ordered.append(lp)
    for lp in local_parts:
        low = lp.lower()
        if low not in seen_lp:
            seen_lp.add(low)
            ordered.append(lp)
    return ordered


def local_part_for_pattern_key(first_name, last_name, pattern_key):
    keys = PERMUTATION_PATTERN_KEYS
    if not pattern_key or pattern_key not in keys:
        return None
    idx = keys.index(pattern_key)
    email = generate_permutations(first_name, last_name, "x.com")[idx]
    return email.split("@")[0]


def company_wide_learned_pattern_keys(successes, valid_domains):
    """
    Distinct pattern keys to probe on every host in Phase 0 (newest rows first).
    Prefers keys from successes whose domain is still in valid_domains, then any other.
    """
    if not successes or not valid_domains:
        return []
    keys_allowed = PERMUTATION_PATTERN_KEYS
    vlow = {d.lower() for d in valid_domains}
    ordered = []
    seen = set()
    for s in sorted(
        successes, key=lambda x: x.get("updated_at") or "", reverse=True
    ):
        pk = s.get("pattern_key")
        if not pk or pk not in keys_allowed or pk in seen:
            continue
        dom = (s.get("domain") or "").strip()
        if dom and dom.lower() in vlow:
            seen.add(pk)
            ordered.append(pk)
    for s in sorted(
        successes, key=lambda x: x.get("updated_at") or "", reverse=True
    ):
        pk = s.get("pattern_key")
        if not pk or pk not in keys_allowed or pk in seen:
            continue
        if (s.get("domain") or "").strip():
            seen.add(pk)
            ordered.append(pk)
    return ordered


def learned_domains_phase1(valid_domains, successes):
    """Hosts we have learned, intersected with valid_domains; newest updated_at first."""
    by_lower = {d.lower(): d for d in valid_domains}
    ordered = []
    seen_lower = set()
    for s in sorted(
        successes, key=lambda x: x.get("updated_at") or "", reverse=True
    ):
        dom = (s.get("domain") or "").strip()
        if not dom:
            continue
        low = dom.lower()
        if low in by_lower and low not in seen_lower:
            ordered.append(by_lower[low])
            seen_lower.add(low)
    return ordered, seen_lower


def find_email(
    first_name,
    last_name,
    domains,
    progress_callback=None,
    cancel_event=None,
    company_slug=None,
):
    """Main function to find the valid email across multiple domains with safe parallelism."""
    
    def notify(msg, current_email=None):
        if current_email:
            print(msg, end=" ", flush=True)
        else:
            print(msg)
            
        if progress_callback:
            progress_callback(msg, current_email)

    notify(f"Searching for {first_name.capitalize()} {last_name.capitalize()}...")
    
    domain_info = {}
    valid_domains = []
    
    notify("\n[*] Pre-checking mail servers for all domains to remove time-wasters...")
    
    # We do not skip third-party MX gateways by name: from some networks they accept SMTP probes.
    
    for domain in domains:
        if cancel_event and cancel_event.is_set():
            notify("[-] Search cancelled by user.")
            return None
            
        mx_record = get_mx_record(domain)
        if not mx_record:
            continue
            
        is_catchall = is_catch_all(domain, mx_record)
        
        if is_catchall is True:
            notify(f"  [!] {domain}: Valid MX, but is a CATCH-ALL (skipping)")
        elif is_catchall is None:
            notify(f"  [\u26d4] {domain}: Server blocked connection during pre-check (skipping)")
        else:
            domain_info[domain] = {
                'mx_record': mx_record,
                'is_catchall': is_catchall
            }
            valid_domains.append(domain)
            notify(f"  [+] {domain}: Valid MX ({mx_record}), NOT a catch-all")
            
    if not valid_domains:
        notify("[-] No valid, non-catch-all domains found to test.")
        return None
        
    notify(f"\n[*] Proceeding with {len(valid_domains)} valid domains.")

    slug = company_slug or company_slug_from_first_domain(domains)
    learned_store = load_learned_patterns() if slug else {}
    successes = normalized_successes_for_slug(learned_store, slug) if slug else []
    phase1_domains, phase1_lower = learned_domains_phase1(valid_domains, successes)

    def build_tasks(
        domain_subset,
        use_learned_pattern_order=False,
        skip_email_lower=None,
    ):
        skip = skip_email_lower or set()
        out = []
        for domain in domain_subset:
            pref_keys = (
                learned_pattern_keys_for_domain(successes, domain)
                if use_learned_pattern_order
                else []
            )
            for local_part in ordered_local_parts_with_preferred_keys(
                first_name, last_name, pref_keys
            ):
                email = f"{local_part}@{domain}"
                if email.lower() in skip:
                    continue
                out.append(
                    (email, domain_info[domain]["mx_record"], domain)
                )
        return out

    company_patterns = (
        company_wide_learned_pattern_keys(successes, valid_domains)
        if successes
        else []
    )
    skip_email_lower = set()
    phase0_tasks = []
    if company_patterns:
        if phase1_domains:
            domain_shoot_order = phase1_domains + [
                d for d in valid_domains if d.lower() not in phase1_lower
            ]
        else:
            domain_shoot_order = list(valid_domains)
        for pk in company_patterns:
            lp = local_part_for_pattern_key(first_name, last_name, pk)
            if not lp:
                continue
            for domain in domain_shoot_order:
                em = f"{lp}@{domain}"
                low = em.lower()
                if low in skip_email_lower:
                    continue
                skip_email_lower.add(low)
                phase0_tasks.append(
                    (em, domain_info[domain]["mx_record"], domain)
                )

    task_batches = []
    if phase0_tasks:
        keys_display = ", ".join(company_patterns)
        task_batches.append(
            (
                f"Phase 0 (learned patterns [{keys_display}] on each host)",
                phase0_tasks,
            )
        )
        notify(
            f"\n[*] Learned pattern(s) [{keys_display}] on each valid host first "
            f"({len(phase0_tasks)} probe(s)), then full search."
        )

    if phase1_domains:
        phase2_domains = [
            d for d in valid_domains if d.lower() not in phase1_lower
        ]
        task_batches.extend(
            [
                (
                    "Phase 1 (learned hosts, saved pattern first)",
                    build_tasks(
                        phase1_domains,
                        use_learned_pattern_order=True,
                        skip_email_lower=skip_email_lower,
                    ),
                ),
                (
                    "Phase 2 (other domains)",
                    build_tasks(
                        phase2_domains,
                        False,
                        skip_email_lower=skip_email_lower,
                    ),
                ),
            ]
        )
        notify(
            f"\n[*] Learned hosts in this run: {', '.join(phase1_domains)} "
            f"— saved local-part pattern tried first on each, then {len(phase2_domains)} other domain(s)."
        )
        hints = []
        for d in phase1_domains:
            pks = learned_pattern_keys_for_domain(successes, d)
            if pks:
                hints.append(f"{d} → {', '.join(pks)}")
        if hints:
            notify("[*] Learned pattern order: " + "; ".join(hints))
    else:
        task_batches.append(
            (
                "Search",
                build_tasks(
                    valid_domains,
                    False,
                    skip_email_lower=skip_email_lower,
                ),
            )
        )

    found_email = None
    stop_event = threading.Event()
    lock = threading.Lock()
    tasks_left = 0

    def check_email_worker(task):
        nonlocal found_email, tasks_left
        email, mx_record, domain = task

        if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
            return

        with lock:
            current_left = tasks_left
            tasks_left -= 1

        notify(f"[{current_left} left] Testing: {email}...", current_email=email)

        time.sleep(random.uniform(0.1, 0.5))

        if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
            return

        is_valid = verify_email_smtp(email, mx_record, domain)

        if is_valid is True:
            with lock:
                if not stop_event.is_set() and not (
                    cancel_event and cancel_event.is_set()
                ):
                    found_email = email
                    stop_event.set()
                    notify("VALID! \u2705")
                    notify(f"\n\u2728 Found valid email: {email} \u2728")
        elif is_valid is None:
            notify("Blocked \u26d4")
        else:
            notify("Invalid \u274c")
            time.sleep(random.uniform(0.5, 1.5))

    def run_batch(label, tasks):
        nonlocal tasks_left, found_email
        if not tasks:
            return
        tasks_left = len(tasks)
        notify(f"\n[*] {label}: {len(tasks)} combination(s) to test.")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(check_email_worker, t) for t in tasks]
            for _ in concurrent.futures.as_completed(futures):
                if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
                    for f in futures:
                        f.cancel()
                    break

    for label, batch in task_batches:
        if cancel_event and cancel_event.is_set():
            break
        if found_email:
            break
        run_batch(label, batch)

    if cancel_event and cancel_event.is_set():
        notify("\n[-] Search cancelled by user.")
        return None

    if found_email and slug:
        lp = found_email.split("@")[0]
        dom = found_email.split("@")[1]
        pk = infer_pattern_key(first_name, last_name, lp)
        if pk:
            upsert_learned_success(slug, dom, pk)

    if not found_email:
        notify("\n[-] Could not find a valid email address.")

    return found_email


def _phase_progress(progress_callback, message):
    if progress_callback:
        progress_callback(message, None)
    else:
        print(message)


def two_pass_find_email(
    first_name,
    last_name,
    company,
    progress_callback=None,
    cancel_event=None,
    pass2_empty_resolver=None,
):
    """
    Pass 1: if learned_patterns matches the company, search using TLD variations
    built only from stored hosts (no Clearbit yet). Pass 2: if no email, run
    Clearbit, merge learned hosts first, expand TLDs, search again.
    """
    store = load_learned_patterns()
    learned_slug = match_learned_slug(company, store)
    learned_hosts = (
        learned_domains_for_slug(store, learned_slug) if learned_slug else []
    )
    if learned_slug and not learned_hosts:
        learned_slug = None

    def cancelled():
        return cancel_event and cancel_event.is_set()

    _phase_progress(
        progress_callback,
        "[*] Checking learned_patterns.json for this company…",
    )

    if learned_hosts:
        _phase_progress(
            progress_callback,
            (
                f"[*] Pass 1 — matched learned key '{learned_slug}'; "
                f"searching using saved hosts only: {', '.join(learned_hosts)}"
            ),
        )
        pass1_domains = generate_domain_variations(learned_hosts)
        _phase_progress(
            progress_callback,
            f"[*] Pass 1 — candidate domains: {pass1_domains}",
        )
        if cancelled():
            return None
        email = find_email(
            first_name,
            last_name,
            pass1_domains,
            progress_callback,
            cancel_event,
            company_slug=learned_slug,
        )
        if email:
            return email
        if cancelled():
            return None
        _phase_progress(
            progress_callback,
            "[*] Pass 1 found no email; running Clearbit and full domain search (pass 2).",
        )
    else:
        _phase_progress(
            progress_callback,
            "[*] No learned slug match; using Clearbit and full domain list (pass 2 only).",
        )

    if cancelled():
        return None

    _phase_progress(
        progress_callback,
        f"[*] Pass 2 — fetching domain suggestions for '{company}'…",
    )
    clearbit = find_company_domains(company)
    target_domains = merge_domain_lists_learned_first(learned_hosts, clearbit)

    if not target_domains:
        if pass2_empty_resolver:
            target_domains = pass2_empty_resolver(company, learned_hosts) or []
        if not target_domains:
            guessed_domain = f"{company.lower().replace(' ', '')}.com"
            _phase_progress(
                progress_callback,
                f"[API] No domain found automatically. Guessing: {guessed_domain}",
            )
            target_domains = [guessed_domain]

    company_slug = learned_slug or company_slug_from_first_domain(target_domains)
    target_domains = generate_domain_variations(target_domains)
    _phase_progress(
        progress_callback,
        f"[*] Pass 2 — starting email search using domains: {target_domains}",
    )
    if cancelled():
        return None
    return find_email(
        first_name,
        last_name,
        target_domains,
        progress_callback,
        cancel_event,
        company_slug=company_slug,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find a person's corporate email address.")
    parser.add_argument("first_name", help="The first name of the target person")
    parser.add_argument("last_name", help="The last name of the target person")
    parser.add_argument("company", help="The name of the company they work for")
    
    args = parser.parse_args()
    
    target_first = args.first_name
    target_last = args.last_name
    company_name = args.company

    def cli_pass2_empty(company, learned_hosts):
        print("[-] Could not automatically determine the company domain.")
        domain_input = input(
            "Please enter the primary domain manually (e.g., company.com): "
        )
        if not domain_input:
            print("[-] No domain provided. Exiting.")
            sys.exit(1)
        return merge_domain_lists_learned_first(
            learned_hosts, [domain_input.strip()]
        )

    email = two_pass_find_email(
        target_first,
        target_last,
        company_name,
        pass2_empty_resolver=cli_pass2_empty,
    )
    if not email:
        print("\n[-] Could not find a valid email address.")
