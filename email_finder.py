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
import math
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
    "first_dot_m_dot_last",
    "first_m_last",
    "f_m_last",
)

_learned_io_lock = threading.Lock()


def _iso_ts_for_sort(iso_s):
    """Parse stored ISO timestamps for numeric sort keys (newer = larger)."""
    if not iso_s or not isinstance(iso_s, str):
        return 0.0
    try:
        s = iso_s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError, OSError):
        return 0.0


class MxSessionCache:
    """Per-process cache of MX + catch-all precheck results (domain lower -> dict)."""

    __slots__ = ("_lock", "_data")

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def get_precheck(self, domain):
        with self._lock:
            return self._data.get(domain.lower())

    def set_precheck(self, domain, mx_record, is_catchall):
        with self._lock:
            self._data[domain.lower()] = {
                "mx_record": mx_record,
                "is_catchall": is_catchall,
            }


class DomainSmtpPacer:
    """Minimum spacing between SMTP probes to the same mail domain."""

    __slots__ = ("_lock", "_last", "min_interval")

    def __init__(self, min_interval=0.35):
        self._lock = threading.Lock()
        self._last = {}
        self.min_interval = min_interval

    def wait(self, domain):
        low = domain.lower()
        with self._lock:
            now = time.monotonic()
            prev = self._last.get(low, 0.0)
            delay = self.min_interval - (now - prev)
        if delay > 0:
            time.sleep(delay)
        with self._lock:
            self._last[low] = time.monotonic()


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
    
    print(f"Looking up company: {company_name}")
    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(cleaned_name)}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            domains = [item['domain'] for item in data if 'domain' in item]
            
            if domains:
                print(f"Domains found: {', '.join(domains)}")
                return domains
                
        # 2. If no domains found, try removing generic tech words (e.g. "Name Cloud Solutions" -> "Name Cloud")
        tech_words = [r'\bsolutions\b', r'\btechnologies\b', r'\bsoftware\b', r'\bsoftech\b', r'\btech\b', r'\bit\b']
        ultra_cleaned = cleaned_name
        for word in tech_words:
            ultra_cleaned = re.sub(word, '', ultra_cleaned, flags=re.IGNORECASE)
        ultra_cleaned = re.sub(r'\s+', ' ', ultra_cleaned).strip()
        
        if ultra_cleaned and ultra_cleaned != cleaned_name:
            print(f"Trying again without words like tech/software: {ultra_cleaned}")
            url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(ultra_cleaned)}"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                domains = [item['domain'] for item in data if 'domain' in item]
                
                if domains:
                    print(f"Domains found: {', '.join(domains)}")
                    return domains
                    
        # 3. Ultimate fallback: first token only (e.g. "Foo Bar Baz" -> "Foo")
        if ' ' in ultra_cleaned:
            first_word = ultra_cleaned.split(' ')[0]
            print(f"Trying again with first word only: {first_word}")
            url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(first_word)}"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                domains = [item['domain'] for item in data if 'domain' in item]
                
                if domains:
                    print(f"Domains found: {', '.join(domains)}")
                    return domains
                    
        print("No domains found from company name.")
    except Exception as e:
        print(f"Domain lookup failed: {e}")
        
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
            print(f"  Mail check error for {email}: {e}")
        return None

def generate_permutations(first_name, last_name, domain, middle_name=""):
    """Generate comprehensive email format permutations (expanded from 18 to 21).

    Always returns 21 entries in lockstep with PERMUTATION_PATTERN_KEYS, even
    when no middle name is given, so pattern indices stay stable regardless
    of whether the person has a middle name.
    """
    first = first_name.lower()
    last = last_name.lower()
    f = first[0]
    l = last[0]
    middle = (middle_name or "").strip().lower()

    perms = [
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
        f"{l}.{first}@{domain}",
    ]

    if middle:
        m = middle[0]
        perms += [
            f"{first}.{m}.{last}@{domain}",
            f"{first}{m}{last}@{domain}",
            f"{f}{m}{l}@{domain}",
        ]
    else:
        # Keep the list length constant (harmless duplicates of earlier entries)
        # so pattern-key indices remain valid whether or not a middle name exists.
        perms += [
            f"{first}.{last}@{domain}",
            f"{first}{last}@{domain}",
            f"{f}{last}@{domain}",
        ]

    return perms

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


def enrich_success_row(s):
    """Normalize one success row: hit_count, last_seen, optional decay_score (derived)."""
    if not isinstance(s, dict):
        return {}
    out = dict(s)
    try:
        hc = int(out.get("hit_count", 1))
    except (TypeError, ValueError):
        hc = 1
    if hc < 1:
        hc = 1
    out["hit_count"] = hc
    ls = out.get("last_seen") or out.get("updated_at") or ""
    out["last_seen"] = ls
    out.setdefault("updated_at", ls)
    try:
        iso = ls.replace("Z", "+00:00") if ls else ""
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(
            0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
        )
        out["decay_score"] = round(hc * math.exp(-age_days / 45.0), 4)
    except (ValueError, TypeError, OSError):
        out["decay_score"] = float(hc)
    return out


def normalized_successes_for_slug(store, slug):
    """Return enriched success dicts (supports legacy single-host shape)."""
    if not slug:
        return []
    row = store.get(slug)
    if not isinstance(row, dict):
        return []
    if isinstance(row.get("successes"), list):
        return [
            enrich_success_row(s) for s in row["successes"] if isinstance(s, dict)
        ]
    if row.get("preferred_domain"):
        return [
            enrich_success_row(
                {
                    "domain": row["preferred_domain"],
                    "pattern_key": row.get("pattern_key") or "unknown",
                    "updated_at": row.get("updated_at", ""),
                    "hit_count": 1,
                }
            )
        ]
    return []


def upsert_learned_success(slug, domain_host, pattern_key):
    """
    Same (domain, pattern_key): increment hit_count, refresh last_seen/updated_at.
    New pair: append row with hit_count=1. Backward compatible with rows missing hit_count.
    """
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
                s["hit_count"] = int(s.get("hit_count", 1)) + 1
                s["last_seen"] = now
                s["updated_at"] = now
                updated = True
                break
        if not updated:
            successes.append(
                {
                    "domain": domain_host,
                    "pattern_key": pattern_key,
                    "updated_at": now,
                    "last_seen": now,
                    "hit_count": 1,
                }
            )
        for s in successes:
            s.pop("decay_score", None)
        store[slug] = {"successes": successes}
        save_learned_patterns(store)


def infer_pattern_key(first_name, last_name, local_part, middle_name=""):
    if not local_part:
        return None
    target = local_part.strip().lower()
    keys = PERMUTATION_PATTERN_KEYS
    for key, email in zip(
        keys, generate_permutations(first_name, last_name, "x.com", middle_name)
    ):
        if email.split("@")[0].lower() == target:
            return key
    return None


def rank_patterns_for_domain(slug, domain):
    """
    Order pattern_key strings for a host: hit_count primary, recency secondary,
    then remaining templates in default PERMUTATION order.
    """
    if not slug or not domain:
        return list(PERMUTATION_PATTERN_KEYS)
    store = load_learned_patterns()
    successes = normalized_successes_for_slug(store, slug)
    dlow = domain.lower()
    rows = [
        s
        for s in successes
        if (s.get("domain") or "").lower() == dlow
        and s.get("pattern_key") in PERMUTATION_PATTERN_KEYS
    ]
    agg = {}
    for s in rows:
        pk = s["pattern_key"]
        hc = int(s.get("hit_count", 1))
        ls = s.get("last_seen") or s.get("updated_at") or ""
        if pk not in agg:
            agg[pk] = {"hit_count": 0, "last_seen": ""}
        agg[pk]["hit_count"] += hc
        if ls > agg[pk]["last_seen"]:
            agg[pk]["last_seen"] = ls
    ordered = sorted(
        agg.keys(),
        key=lambda pk: (
            -agg[pk]["hit_count"],
            -_iso_ts_for_sort(agg[pk]["last_seen"] or ""),
        ),
    )
    tail = [pk for pk in PERMUTATION_PATTERN_KEYS if pk not in ordered]
    return ordered + tail


def observed_pattern_keys_for_domain(slug, domain):
    """Distinct pattern keys that appear in stored successes for this host (recency order)."""
    if not slug or not domain:
        return []
    store = load_learned_patterns()
    successes = normalized_successes_for_slug(store, slug)
    dlow = domain.lower()
    out = []
    seen = set()
    for s in sorted(
        successes,
        key=lambda x: x.get("last_seen") or x.get("updated_at") or "",
        reverse=True,
    ):
        if (s.get("domain") or "").lower() != dlow:
            continue
        pk = s.get("pattern_key")
        if not pk or pk not in PERMUTATION_PATTERN_KEYS or pk in seen:
            continue
        seen.add(pk)
        out.append(pk)
    return out


def ordered_local_parts_with_preferred_keys(
    first_name, last_name, preferred_keys, middle_name=""
):
    """
    All permutation local-parts; those matching preferred_keys (in order) first,
    then the rest without duplicate local-part strings.
    """
    keys = PERMUTATION_PATTERN_KEYS
    template_emails = generate_permutations(first_name, last_name, "x.com", middle_name)
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


def local_part_for_pattern_key(first_name, last_name, pattern_key, middle_name=""):
    keys = PERMUTATION_PATTERN_KEYS
    if not pattern_key or pattern_key not in keys:
        return None
    idx = keys.index(pattern_key)
    email = generate_permutations(first_name, last_name, "x.com", middle_name)[idx]
    return email.split("@")[0]


def learned_domains_phase1(valid_domains, successes):
    """Hosts we have learned, intersected with valid_domains; newest last_seen first."""
    by_lower = {d.lower(): d for d in valid_domains}
    ordered = []
    seen_lower = set()
    for s in sorted(
        successes,
        key=lambda x: x.get("last_seen") or x.get("updated_at") or "",
        reverse=True,
    ):
        dom = (s.get("domain") or "").strip()
        if not dom:
            continue
        low = dom.lower()
        if low in by_lower and low not in seen_lower:
            ordered.append(by_lower[low])
            seen_lower.add(low)
    return ordered, seen_lower


def _slug_total_hits(successes):
    return sum(int(s.get("hit_count", 1)) for s in successes)


def compute_confidence(email, signals):
    """
    Heuristic 0–100 score plus an explainable breakdown dict.
    signals keys: pattern_key, pattern_hit_count, pattern_total_slug_hits,
    domain_source, name_match_score (0–1), smtp_verified (True/False/None).
    """
    breakdown = {}
    pk = signals.get("pattern_key")
    breakdown["pattern"] = pk
    ph = int(signals.get("pattern_hit_count", 0))
    pt = max(1, int(signals.get("pattern_total_slug_hits", 1)))
    pattern_freq = ph / pt
    breakdown["pattern_frequency_score"] = round(pattern_freq, 4)
    pattern_strength = min(1.0, pattern_freq * 1.6)
    breakdown["pattern_strength"] = round(pattern_strength, 4)
    dom_src = signals.get("domain_source", "unknown")
    breakdown["domain_source"] = dom_src
    dom_map = {"learned": 0.95, "clearbit": 0.82, "guessed": 0.62, "unknown": 0.55}
    domain_priority = dom_map.get(dom_src, 0.55)
    breakdown["domain_priority_score"] = domain_priority
    nm = float(signals.get("name_match_score", 1.0))
    breakdown["name_match_score"] = nm
    smtp = signals.get("smtp_verified")
    breakdown["smtp_verified"] = smtp
    if smtp is True:
        smtp_part = 1.0
    elif smtp is False:
        smtp_part = 0.2
    else:
        smtp_part = 0.72
    breakdown["smtp_signal"] = smtp_part
    raw = (
        38 * pattern_strength
        + 32 * domain_priority
        + 18 * nm
        + 12 * smtp_part
    )
    score = int(max(0, min(100, round(raw))))
    breakdown["blend"] = "mix of pattern use, domain trust, name fit, and mail check"
    return score, breakdown


def precheck_domains_for_mail(domains, notify_fn, cancel_event, mx_cache=None):
    """
    MX + catch-all gate. Returns (domain_info, valid_domains, mx_cache).
    notify_fn(msg, current_email=None) optional.
    """
    mx_cache = mx_cache if mx_cache is not None else MxSessionCache()

    def n(msg, cur=None):
        if notify_fn:
            notify_fn(msg, cur)

    domain_info = {}
    valid_domains = []
    for domain in domains:
        if cancel_event and cancel_event.is_set():
            break
        cached = mx_cache.get_precheck(domain)
        if cached is not None:
            mx_record = cached.get("mx_record")
            is_catchall = cached.get("is_catchall")
        else:
            mx_record = get_mx_record(domain)
            if not mx_record:
                mx_cache.set_precheck(domain, None, None)
                continue
            is_catchall = is_catch_all(domain, mx_record)
            mx_cache.set_precheck(domain, mx_record, is_catchall)

        if mx_record is None:
            continue

        if is_catchall is True:
            n(f"  skip {domain} (mail server accepts any address)")
        elif is_catchall is None:
            n(f"  skip {domain} (could not reach mail server)")
        else:
            domain_info[domain] = {"mx_record": mx_record, "is_catchall": is_catchall}
            valid_domains.append(domain)
            n(f"  ok {domain}")
    return domain_info, valid_domains, mx_cache


def predict_emails(
    first_name,
    last_name,
    domains,
    middle_name="",
    company_slug=None,
    domain_sources=None,
    progress_callback=None,
    cancel_event=None,
    mx_cache=None,
    smtp_pacer=None,
    confidence_threshold=75,
    max_smtp_attempts=3,
    top_patterns_per_domain=3,
    trust_high_confidence_without_smtp=False,
):
    """
    Rank a small set of candidate addresses, run lazy SMTP (up to max_smtp_attempts),
    return structured JSON-friendly dict. Does not replace full find_email search.
    """
    domain_sources = domain_sources or {}
    mx_cache = mx_cache if mx_cache is not None else MxSessionCache()
    pacer = smtp_pacer if smtp_pacer is not None else DomainSmtpPacer()

    def notify(msg, cur=None):
        if progress_callback:
            progress_callback(msg, cur)
        else:
            if cur is None:
                print(msg)
            else:
                print(msg, cur)

    def cancelled():
        return cancel_event and cancel_event.is_set()

    notify(
        f"\nSmart pass: best {top_patterns_per_domain} patterns per domain, then a few mail checks.",
        None,
    )
    domain_info, valid_domains, mx_cache = precheck_domains_for_mail(
        domains, notify, cancel_event, mx_cache
    )
    if cancelled():
        return {
            "status": "cancelled",
            "verified_email": None,
            "confidence_threshold": confidence_threshold,
            "best_pre_smtp_confidence": 0,
            "candidates": [],
        }
    if not valid_domains:
        return {
            "status": "not_found",
            "verified_email": None,
            "confidence_threshold": confidence_threshold,
            "best_pre_smtp_confidence": 0,
            "candidates": [],
        }

    slug_eff = company_slug or company_slug_from_first_domain(domains)
    learned_store = load_learned_patterns() if slug_eff else {}
    successes = (
        normalized_successes_for_slug(learned_store, slug_eff) if slug_eff else []
    )
    total_slug_hits = _slug_total_hits(successes) if successes else 1

    raw_candidates = []
    seen_email = set()
    for domain in valid_domains:
        if cancelled():
            break
        dlow = domain.lower()
        pats = rank_patterns_for_domain(slug_eff, domain)[:top_patterns_per_domain]
        for pk in pats:
            lp = local_part_for_pattern_key(first_name, last_name, pk, middle_name)
            if not lp:
                continue
            email = f"{lp}@{domain}"
            el = email.lower()
            if el in seen_email:
                continue
            seen_email.add(el)
            row_hits = 0
            for s in successes:
                if (
                    (s.get("domain") or "").lower() == dlow
                    and s.get("pattern_key") == pk
                ):
                    row_hits += int(s.get("hit_count", 1))
            src = domain_sources.get(dlow, "unknown")
            sig = {
                "pattern_key": pk,
                "pattern_hit_count": max(1, row_hits),
                "pattern_total_slug_hits": max(1, total_slug_hits),
                "domain_source": src,
                "name_match_score": 1.0,
                "smtp_verified": None,
            }
            conf, br = compute_confidence(email, sig)
            raw_candidates.append(
                {
                    "email": email,
                    "confidence": conf,
                    "signals": {
                        "pattern": pk,
                        "pattern_strength": br.get("pattern_strength"),
                        "domain_source": src,
                        "smtp_verified": False,
                        "breakdown": br,
                    },
                }
            )

    raw_candidates.sort(key=lambda x: -x["confidence"])
    top3 = raw_candidates[:3]
    best_pre = top3[0]["confidence"] if top3 else 0

    if not top3:
        return {
            "status": "not_found",
            "verified_email": None,
            "confidence_threshold": confidence_threshold,
            "best_pre_smtp_confidence": 0,
            "candidates": [],
        }

    if (
        trust_high_confidence_without_smtp
        and best_pre >= confidence_threshold
    ):
        top3[0]["signals"]["smtp_verified"] = False
        return {
            "status": "high_confidence",
            "verified_email": top3[0]["email"],
            "confidence_threshold": confidence_threshold,
            "best_pre_smtp_confidence": best_pre,
            "candidates": top3,
        }

    verified = None
    for i, cand in enumerate(top3):
        if i >= max_smtp_attempts or cancelled():
            break
        email = cand["email"]
        dom = email.split("@")[1]
        mx = domain_info[dom]["mx_record"]
        pacer.wait(dom)
        time.sleep(random.uniform(0.08, 0.28))
        ok = verify_email_smtp(email, mx, dom)
        pk = cand["signals"]["pattern"]
        row_hits = 0
        for s in successes:
            if (
                (s.get("domain") or "").lower() == dom.lower()
                and s.get("pattern_key") == pk
            ):
                row_hits += int(s.get("hit_count", 1))
        sc, br2 = compute_confidence(
            email,
            {
                "pattern_key": pk,
                "pattern_hit_count": max(1, row_hits),
                "pattern_total_slug_hits": max(1, total_slug_hits),
                "domain_source": cand["signals"]["domain_source"],
                "name_match_score": 1.0,
                "smtp_verified": True if ok is True else (False if ok is False else None),
            },
        )
        cand["confidence"] = sc
        cand["signals"]["smtp_verified"] = ok is True
        cand["signals"]["breakdown"] = br2
        if ok is True:
            verified = email
            break

    if verified:
        return {
            "status": "verified",
            "verified_email": verified,
            "confidence_threshold": confidence_threshold,
            "best_pre_smtp_confidence": best_pre,
            "candidates": top3,
        }

    return {
        "status": "low_confidence",
        "verified_email": None,
        "confidence_threshold": confidence_threshold,
        "best_pre_smtp_confidence": best_pre,
        "candidates": top3,
    }


def find_email(
    first_name,
    last_name,
    domains,
    progress_callback=None,
    cancel_event=None,
    company_slug=None,
    mx_cache=None,
    smtp_pacer=None,
    middle_name="",
):
    """Main function to find the valid email across multiple domains with safe parallelism."""
    
    def notify(msg, current_email=None):
        if current_email:
            print(msg, end=" ", flush=True)
        else:
            print(msg)
            
        if progress_callback:
            progress_callback(msg, current_email)

    notify(f"Looking for {first_name.capitalize()} {last_name.capitalize()}")
    
    notify("\nChecking domains (mail server setup)...")
    domain_info, valid_domains, _mx_used = precheck_domains_for_mail(
        domains, notify, cancel_event, mx_cache
    )
    if cancel_event and cancel_event.is_set():
        notify("Stopped (cancelled).")
        return None

    if not valid_domains:
        notify("No domain passed the mail check. Stopping.")
        return None
        
    notify(f"\nWill try {len(valid_domains)} domain(s).")

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
                rank_patterns_for_domain(slug, domain)
                if (use_learned_pattern_order and slug)
                else []
            )
            for local_part in ordered_local_parts_with_preferred_keys(
                first_name, last_name, pref_keys, middle_name
            ):
                email = f"{local_part}@{domain}"
                if email.lower() in skip:
                    continue
                out.append(
                    (email, domain_info[domain]["mx_record"], domain)
                )
        return out

    skip_email_lower = set()
    phase0_tasks = []
    phase0_keys_seen = []
    if successes and slug:
        if phase1_domains:
            domain_shoot_order = phase1_domains + [
                d for d in valid_domains if d.lower() not in phase1_lower
            ]
        else:
            domain_shoot_order = list(valid_domains)
        for domain in domain_shoot_order:
            for pk in rank_patterns_for_domain(slug, domain)[:3]:
                lp = local_part_for_pattern_key(first_name, last_name, pk, middle_name)
                if not lp:
                    continue
                em = f"{lp}@{domain}"
                low = em.lower()
                if low in skip_email_lower:
                    continue
                skip_email_lower.add(low)
                phase0_tasks.append(
                    (em, domain_info[domain]["mx_record"], domain)
                )
                if pk not in phase0_keys_seen:
                    phase0_keys_seen.append(pk)

    task_batches = []
    if phase0_tasks:
        keys_display = ", ".join(phase0_keys_seen) if phase0_keys_seen else "ranked"
        task_batches.append(
            (
                f"Quick try ({keys_display})",
                phase0_tasks,
            )
        )
        notify(
            f"\nQuick try: {len(phase0_tasks)} address(es) from saved patterns, then the full list."
        )

    if phase1_domains:
        phase2_domains = [
            d for d in valid_domains if d.lower() not in phase1_lower
        ]
        task_batches.extend(
            [
                (
                    "Known company domains",
                    build_tasks(
                        phase1_domains,
                        use_learned_pattern_order=True,
                        skip_email_lower=skip_email_lower,
                    ),
                ),
                (
                    "Other domains",
                    build_tasks(
                        phase2_domains,
                        False,
                        skip_email_lower=skip_email_lower,
                    ),
                ),
            ]
        )
        notify(
            f"\nKnown domains first: {', '.join(phase1_domains)}. "
            f"Then {len(phase2_domains)} more."
        )
        hints = []
        for d in phase1_domains:
            pks = observed_pattern_keys_for_domain(slug, d) if slug else []
            if pks:
                hints.append(f"{d}: {', '.join(pks)}")
        if hints:
            notify("Saved patterns: " + " | ".join(hints))
    else:
        task_batches.append(
            (
                "Full search",
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

        notify(f"{current_left} left | {email}", current_email=email)

        if smtp_pacer:
            smtp_pacer.wait(domain)
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
                    notify("match")
                    notify(f"\nFound: {email}")
        elif is_valid is None:
            notify("blocked")
        else:
            notify("no")
            time.sleep(random.uniform(0.5, 1.5))

    def run_batch(label, tasks):
        nonlocal tasks_left, found_email
        if not tasks:
            return
        tasks_left = len(tasks)
        notify(f"\n{label}: {len(tasks)} address(es) to try.")
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
        notify("\nStopped (cancelled).")
        return None

    if found_email and slug:
        lp = found_email.split("@")[0]
        dom = found_email.split("@")[1]
        pk = infer_pattern_key(first_name, last_name, lp, middle_name)
        if pk:
            upsert_learned_success(slug, dom, pk)

    if not found_email:
        notify("\nNo matching address found.")

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
    middle_name="",
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

    mx_session = MxSessionCache()
    smtp_pacer = DomainSmtpPacer()

    def cancelled():
        return cancel_event and cancel_event.is_set()

    _phase_progress(
        progress_callback,
        "Using saved company data if we have it...",
    )

    if learned_hosts:
        _phase_progress(
            progress_callback,
            (
                f"Step 1 — saved company match ({learned_slug}). "
                f"Trying these domains first: {', '.join(learned_hosts)}"
            ),
        )
        pass1_domains = generate_domain_variations(learned_hosts)
        _phase_progress(
            progress_callback,
            f"Step 1 — will check: {pass1_domains}",
        )
        if cancelled():
            return None
        src_map = {d.lower(): "learned" for d in pass1_domains}
        pred = predict_emails(
            first_name,
            last_name,
            pass1_domains,
            middle_name=middle_name,
            company_slug=learned_slug,
            domain_sources=src_map,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            mx_cache=mx_session,
            smtp_pacer=smtp_pacer,
        )
        verified = pred.get("verified_email")
        if verified:
            lp = verified.split("@")[0]
            dom = verified.split("@")[1]
            pk = infer_pattern_key(first_name, last_name, lp, middle_name)
            if pk and learned_slug:
                upsert_learned_success(learned_slug, dom, pk)
            return verified
        if cancelled():
            return None
        _phase_progress(
            progress_callback,
            "Step 1 did not find a sure match. Step 2 — wider company lookup and full search.",
        )
    else:
        _phase_progress(
            progress_callback,
            "No saved company match. Going straight to company lookup and full search.",
        )

    if cancelled():
        return None

    _phase_progress(
        progress_callback,
        f"Step 2 — looking up more domains for: {company}",
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
                f"No domain list from lookup. Guessing: {guessed_domain}",
            )
            target_domains = [guessed_domain]

    company_slug = learned_slug or company_slug_from_first_domain(target_domains)
    target_domains = generate_domain_variations(target_domains)
    _phase_progress(
        progress_callback,
        f"Step 2 — trying these domains: {target_domains}",
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
        mx_cache=mx_session,
        smtp_pacer=smtp_pacer,
        middle_name=middle_name,
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
        print("We could not guess the website domain.")
        domain_input = input(
            "Type the company website (example: company.com): "
        )
        if not domain_input:
            print("No domain typed. Exit.")
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
        print("\nNo email found.")
