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
import concurrent.futures
import threading
from urllib.parse import quote

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

def find_email(first_name, last_name, domains, progress_callback=None, cancel_event=None):
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
    
    base_permutations = generate_permutations(first_name, last_name, "example.com")
    
    # Build a flat list of tasks. Since valid_domains has .com first,
    # the queue naturally explores .com domain deeply first.
    tasks = []
    for domain in valid_domains:
        for base_email in base_permutations:
            local_part = base_email.split('@')[0]
            tasks.append((f"{local_part}@{domain}", domain_info[domain]['mx_record'], domain))
            
    total_tasks = len(tasks)
    tasks_left = total_tasks
    notify(f"\n[*] Generated {total_tasks} possible email combinations to test.")
    
    found_email = None
    stop_event = threading.Event()
    lock = threading.Lock()
    
    def check_email_worker(task):
        nonlocal found_email, tasks_left
        email, mx_record, domain = task
        
        if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
            return
            
        with lock:
            current_left = tasks_left
            tasks_left -= 1
            
        notify(f"[{current_left} left] Testing: {email}...", current_email=email)
        
        # Safe Parallelism: Add a slight random jitter (0.1s - 0.5s) before connecting
        # so 5 threads don't hit the exact same millisecond and trigger DDoS bans.
        time.sleep(random.uniform(0.1, 0.5))
        
        if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
            return
            
        is_valid = verify_email_smtp(email, mx_record, domain)
        
        if is_valid is True:
            with lock:
                if not stop_event.is_set() and not (cancel_event and cancel_event.is_set()):
                    found_email = email
                    stop_event.set()
                    notify("VALID! \u2705")
                    notify(f"\n\u2728 Found valid email: {email} \u2728")
        elif is_valid is None:
            notify("Blocked \u26d4")
        else:
            notify("Invalid \u274c")
            # Be polite to the server
            time.sleep(random.uniform(0.5, 1.5))

    # Run 5 processes in parallel.
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(check_email_worker, task) for task in tasks]
        
        for future in concurrent.futures.as_completed(futures):
            if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
                # Cancel remaining tasks in the queue
                for f in futures:
                    f.cancel()
                break
                
    if cancel_event and cancel_event.is_set():
        notify("\n[-] Search cancelled by user.")
        return None
        
    if not found_email:
        notify("\n[-] Could not find a valid email address.")
        
    return found_email

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find a person's corporate email address.")
    parser.add_argument("first_name", help="The first name of the target person")
    parser.add_argument("last_name", help="The last name of the target person")
    parser.add_argument("company", help="The name of the company they work for")
    
    args = parser.parse_args()
    
    target_first = args.first_name
    target_last = args.last_name
    company_name = args.company
    
    # 1. First, automatically find the correct domain for the company
    target_domains = find_company_domains(company_name)
    
    # 2. If no domain was found, fallback to manual domains
    if not target_domains:
        print("[-] Could not automatically determine the company domain.")
        domain_input = input("Please enter the primary domain manually (e.g., company.com): ")
        if not domain_input:
            print("[-] No domain provided. Exiting.")
            sys.exit(1)
        target_domains = [domain_input]
        
    # 3. Generate common TLD variations (e.g., .com -> .co, .in, etc.)
    target_domains = generate_domain_variations(target_domains)
        
    print(f"\n[*] Starting email search using domains: {target_domains}\n")
    
    # 4. Search for the email
    find_email(target_first, target_last, target_domains)
