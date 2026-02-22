"""
Client Health Dashboard - Data aggregation from ClickUp, Grain, and Gmail.

Tracks Red/Yellow/Green health flags for all active client accounts.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

OPERATIONS_SPACE_ID = "90139881259"
RECURRING_CLIENTS_FOLDER_ID = "901313653969"

EXCLUDED_FOLDERS = [
    "Client Template", "2-Day AI POCs", "Internal Projects", "Recurring Clients"
]

# ACTIVE_CLIENTS is now dynamically fetched from ClickUp Active Accounts list.
# Only accounts with status "engaged" or "new account" are included.

# Only "engaged" accounts appear on the dashboard
ACTIVE_ACCOUNT_STATUSES = {"engaged"}

# TTL constant kept for sub-caches (active accounts, sentiment)
CLIENT_HEALTH_CACHE_TTL = 1800  # 30 minutes

# ============================================================================
# Grain API
# ============================================================================

def load_grain_api_key():
    """Load Grain API key from env var or .env.grain file."""
    key = os.environ.get("GRAIN_API_TOKEN") or os.environ.get("GRAIN_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.grain")
    try:
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith("GRAIN_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except Exception as e:
        logger.error(f"Error loading Grain API key: {e}")
    return None


def grain_request(endpoint, params=None):
    """Make a request to Grain API."""
    grain_key = load_grain_api_key()
    if not grain_key:
        logger.warning("No Grain API key found")
        return {}

    url = f"https://api.grain.com/_/public-api{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{query}"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {grain_key}",
        "Accept": "application/json"
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        logger.error(f"Grain API error: {e.code} for {endpoint}")
        try:
            logger.error(f"Response: {e.read().decode()[:300]}")
        except:
            pass
        return {}
    except Exception as e:
        logger.error(f"Grain API error: {e}")
        return {}


PULSE_TEAM_NAMES = [
    "jake", "sean", "bartosz", "luke", "sam", "razvan", "adri", "walter",
    "jake shumaker", "sean miller", "bartosz stoppel", "luke shumaker",
    "samarth gohel", "walter miller",
]


def fetch_grain_recordings():
    """Fetch all recordings from Grain with pagination."""
    all_recordings = []
    cursor = None

    for _ in range(10):  # Max 10 pages (1000 recordings)
        params = {"limit": "100"}
        if cursor:
            params["cursor"] = cursor

        data = grain_request("/recordings", params)
        recordings = data.get("recordings", data.get("data", []))
        if not recordings:
            break

        all_recordings.extend(recordings)

        # Check for pagination cursor
        cursor = data.get("cursor", data.get("nextCursor", data.get("next_cursor")))
        if not cursor:
            break

    logger.info(f"Fetched {len(all_recordings)} Grain recordings")

    # Enrich with detail endpoint + transcript speakers for external call detection
    # Only fetch details for recent recordings (last 90 days) to limit API calls
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    enriched = 0
    for rec in all_recordings:
        start_str = rec.get("start_datetime", "")
        if start_str:
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if start_dt < cutoff:
                    continue
            except Exception:
                continue

        rec_id = rec.get("id", "")
        if rec_id and enriched < 100:  # Limit to 100 detail fetches per refresh
            # Step 1: Fetch individual recording detail for attendee data
            detail = grain_request(f"/recordings/{rec_id}")
            if detail:
                attendees = detail.get("attendees", [])
                if attendees:
                    rec["_attendees"] = attendees
                    logger.debug(f"Grain rec {rec_id}: got {len(attendees)} attendees from detail endpoint")
                    enriched += 1
                    continue

                # Step 2: Try transcript text URL for speaker names
                transcript_url = detail.get("transcript_txt_url", "")
                if transcript_url:
                    speakers = _fetch_speakers_from_transcript_txt(transcript_url)
                    if speakers:
                        rec["_speakers"] = speakers
                        logger.debug(f"Grain rec {rec_id}: got {len(speakers)} speakers from transcript_txt_url")
                        enriched += 1
                        continue

            # Step 3: Fall back to transcript API endpoint
            speakers = fetch_transcript_speakers(rec_id)
            if speakers:
                rec["_speakers"] = speakers
                logger.debug(f"Grain rec {rec_id}: got {len(speakers)} speakers from transcript API")
                enriched += 1

    # Filter to external calls only
    # Known internal call title patterns to always exclude
    INTERNAL_PATTERNS = [
        "standup", "stand-up", "stand up", "daily sync", "team sync",
        "check-in", "check in", "checkin", "internal", "1:1", "1-on-1",
        "one on one", "quickbooks", "quick books", "pulse team",
        "sprint planning", "sprint review", "retro", "retrospective",
        "all hands", "all-hands", "team meeting",
    ]

    external_recordings = []
    for rec in all_recordings:
        title = (rec.get("title") or rec.get("name") or "").lower()

        # Skip obviously internal calls by title
        if any(pat in title for pat in INTERNAL_PATTERNS):
            # Check if title also mentions a known Pulse team member pair (e.g. "Sam/Jake check-in")
            pulse_names_in_title = sum(1 for name in PULSE_TEAM_NAMES if name in title)
            if pulse_names_in_title >= 1 and not any(
                kw in title for kw in _get_client_keywords()
            ):
                logger.debug(f"Skipping internal call by title: {title}")
                continue

        ext = is_external_call(rec)
        if ext is True:
            external_recordings.append(rec)
        elif ext is None:
            # Unknown — exclude by default to avoid internal call noise
            logger.debug(f"Excluding unknown call (no external signal): {title}")
        # ext is False = internal only, skip

    logger.info(f"After external filter: {len(external_recordings)} external calls (from {len(all_recordings)} total)")
    return external_recordings


def _get_client_keywords():
    """Get all client keywords from mappings for matching."""
    try:
        from client_mappings import load_mappings
        mappings = load_mappings()
        keywords = []
        for client_data in mappings.get("email_domains", {}).values():
            keywords.extend(kw.lower() for kw in client_data.get("keywords", []) if len(kw) > 2)
        return keywords
    except Exception:
        return []


def _fetch_speakers_from_transcript_txt(transcript_url):
    """Download transcript text and extract unique speaker names."""
    try:
        req = urllib.request.Request(transcript_url, headers={"Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")[:50000]  # Cap at 50KB
            speakers = set()
            for line in text.split("\n"):
                line = line.strip()
                # Common transcript format: "Speaker Name: text" or "Speaker Name (HH:MM:SS)"
                if ":" in line:
                    potential_name = line.split(":", 1)[0].strip()
                    # Heuristic: speaker names are short (1-4 words), no digits
                    words = potential_name.split()
                    if 1 <= len(words) <= 4 and not any(c.isdigit() for c in potential_name):
                        speakers.add(potential_name)
            return list(speakers) if speakers else []
    except Exception as e:
        logger.debug(f"Could not fetch transcript txt: {e}")
        return []


def is_external_call(recording):
    """Check if a recording has external (non-Pulse) attendees.

    Uses multiple approaches in order:
    1. Check attendees from detail endpoint for non-pulsemarketing.co emails
    2. Check owners for non-pulsemarketing.co emails
    3. Check transcript speakers for non-Pulse names
    4. Fallback heuristic: title/notes contain client name + multiple participants

    Returns True if external, False if internal-only, None if unknown.
    """
    approach = None

    # 1. Check attendees from detail endpoint
    attendees = recording.get("_attendees", [])
    if attendees:
        for att in attendees:
            email = ""
            if isinstance(att, str):
                email = att
            elif isinstance(att, dict):
                email = att.get("email", "") or att.get("emailAddress", "")
            if email and not email.lower().endswith("@pulsemarketing.co"):
                approach = "attendees_detail"
                logger.debug(f"External call detected via {approach}: {recording.get('id','')}")
                return True
        # Had attendees but all Pulse — internal
        return False

    # 2. Check owners for non-Pulse emails
    owners = recording.get("owners", [])
    has_external_owner = any(
        o and not o.lower().endswith("@pulsemarketing.co")
        for o in owners if isinstance(o, str)
    )
    if has_external_owner:
        approach = "owners_email"
        logger.debug(f"External call detected via {approach}: {recording.get('id','')}")
        return True

    # 3. Check transcript speakers for non-Pulse names
    speakers = recording.get("_speakers", [])
    if speakers:
        for s in speakers:
            name = s if isinstance(s, str) else s.get("name", "") if isinstance(s, dict) else ""
            name_lower = name.lower().strip()
            # Grain marks external attendees with /EXT suffix
            if "/EXT" in name or "/ext" in name:
                approach = "speaker_ext_tag"
                logger.debug(f"External call detected via {approach}: {recording.get('id','')}")
                return True
            # Check if speaker name matches any known Pulse team member
            if name_lower and not any(pn in name_lower for pn in PULSE_TEAM_NAMES):
                approach = "speaker_name_mismatch"
                logger.debug(f"External call detected via {approach} (speaker: {name}): {recording.get('id','')}")
                return True
        # All speakers match Pulse team — internal
        return False

    # 4. Fallback heuristic: title/notes contain what looks like a client meeting + multiple owners
    title = (recording.get("title") or recording.get("name") or "").lower()
    notes = (recording.get("intelligence_notes_md") or "").lower()
    searchable = title + " " + notes
    owner_count = len(owners) if owners else 0

    # Load client names for heuristic matching
    try:
        from client_mappings import load_mappings
        mappings = load_mappings()
        all_client_keywords = []
        for client_data in mappings.get("email_domains", {}).values():
            all_client_keywords.extend(kw.lower() for kw in client_data.get("keywords", []))

        has_client_mention = any(kw in searchable for kw in all_client_keywords if len(kw) > 2)
        if has_client_mention and owner_count > 1:
            approach = "title_heuristic"
            logger.debug(f"External call detected via {approach}: {recording.get('id','')}")
            return True
    except Exception:
        pass

    return None  # Unknown — no signal available


def fetch_transcript_speakers(recording_id):
    """Fetch speaker names from a Grain transcript. Returns list of speaker name strings."""
    grain_key = load_grain_api_key()
    if not grain_key:
        return []

    url = f"https://api.grain.com/_/public-api/recordings/{recording_id}/transcript"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {grain_key}",
        "Accept": "application/json"
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            speakers = set()
            if isinstance(data, list):
                for entry in data[:200]:  # First 200 entries is enough
                    if isinstance(entry, dict) and entry.get("speaker"):
                        speakers.add(entry["speaker"])
            elif isinstance(data, dict) and "segments" in data:
                for seg in data["segments"][:200]:
                    if isinstance(seg, dict) and seg.get("speaker"):
                        speakers.add(seg["speaker"])
            return list(speakers)
    except Exception as e:
        logger.debug(f"Could not fetch transcript for {recording_id}: {e}")
        return []


def match_client_to_recording(recording, client_names):
    """Match a recording to a client.
    Priority: 1) manual grain_matches, 2) client mapping keywords in title/notes,
    3) client name in title/notes, 4) external speaker email domains.
    Returns client name or None.
    """
    from client_mappings import load_mappings

    # Check manual match first
    rec_id = recording.get("id") or recording.get("recording_id") or ""
    if rec_id:
        mappings = load_mappings()
        manual_match = mappings.get("grain_matches", {}).get(rec_id)
        if manual_match:
            if manual_match == "_hidden":
                return None  # User explicitly hid this recording
            return manual_match

    title = (recording.get("title") or recording.get("name") or "").lower()
    notes = (recording.get("intelligence_notes_md") or "").lower()
    searchable = title + " " + notes

    # Check client mapping keywords first (more specific)
    if rec_id:
        mappings = load_mappings()
        email_domains = mappings.get("email_domains", {})
        for client in client_names:
            mapping = email_domains.get(client, {})
            keywords = mapping.get("keywords", [])
            for kw in keywords:
                if kw.lower() in searchable:
                    return client

    # Fall back to client name matching
    for client in client_names:
        client_lower = client.lower()
        if client_lower in searchable:
            return client
        words = client_lower.split()
        if len(words) > 1 and all(w in searchable for w in words):
            return client

    return None


# ============================================================================
# Gmail API
# ============================================================================

PULSE_TEAM_EMAILS = [
    "jake@pulsemarketing.co",
    "sean@pulsemarketing.co",
    "bartosz@pulsemarketing.co",
    "luke@pulsemarketing.co",
    "sam@pulsemarketing.co",
    "razvan@pulsemarketing.co",
    "adri@pulsemarketing.co",
    "walter@pulsemarketing.co",
]


def get_gmail_service(subject="jake@pulsemarketing.co"):
    """Build Gmail API service using service account with domain-wide delegation."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa_json_str:
            import json as _json
            try:
                sa_info = _json.loads(sa_json_str)
            except _json.JSONDecodeError:
                # Render converts \n to real newlines — escape them back
                sa_info = _json.loads(sa_json_str.replace("\n", "\\n").replace("\r", ""))
            credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                subject=subject
            )
        else:
            sa_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   ".env.google-service-account.json")
            credentials = service_account.Credentials.from_service_account_file(
                sa_path,
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                subject=subject
            )

        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except ImportError:
        logger.warning("google-auth or google-api-python-client not installed")
        return None
    except Exception as e:
        logger.error(f"Error building Gmail service for {subject}: {e}")
        return None


def search_client_emails_all_accounts(client_name, max_results=3):
    """Search all Pulse team mailboxes for client emails, return deduplicated top results."""
    all_emails = []
    seen_ids = set()
    for email_addr in PULSE_TEAM_EMAILS:
        try:
            svc = get_gmail_service(subject=email_addr)
            if not svc:
                continue
            results = search_client_emails(svc, client_name, max_results=max_results)
            for e in results:
                if e["id"] not in seen_ids:
                    seen_ids.add(e["id"])
                    e["mailbox"] = email_addr
                    all_emails.append(e)
        except Exception as exc:
            logger.warning(f"Gmail search failed for {email_addr}: {exc}")
    # Sort by date descending, return top N
    all_emails.sort(key=lambda x: x.get("date_ts", 0), reverse=True)
    return all_emails[:max_results]


def search_client_emails(gmail_service, client_name, max_results=3):
    """Search Gmail for recent emails mentioning a client."""
    if not gmail_service:
        return []

    try:
        logger.info(f"Gmail: searching for '{client_name}'...")

        # Check client mappings for email domains/keywords
        from client_mappings import load_mappings
        mappings = load_mappings()
        client_mapping = mappings.get("email_domains", {}).get(client_name, {})
        domains = client_mapping.get("domains", [])
        keywords = client_mapping.get("keywords", [])

        # Build search query — use domains if available, otherwise fall back to name
        if domains:
            domain_query = " OR ".join(f"from:{d}" for d in domains)
            query = f'({domain_query})'
        elif keywords:
            kw_query = " OR ".join(f'"{kw}"' for kw in keywords)
            query = kw_query
        else:
            query = f'"{client_name}"'

        # Exclude junk/automated emails
        junk_filters = [
            # WordPress/plugin/security
            '-subject:"plugin update"',
            '-subject:"WordPress"',
            '-subject:"security alert"',
            '-subject:"Wordfence"',
            '-subject:"iThemes Security"',
            '-subject:"uptime monitoring"',
            '-subject:"backup completed"',
            # Billing/invoice auto-notifications (payment processors, not client invoices)
            '-subject:"payment receipt"',
            '-subject:"payment processed"',
            '-subject:"auto-pay"',
            '-subject:"billing statement"',
            # SSL/domain/hosting
            '-subject:"SSL certificate"',
            '-subject:"domain renewal"',
            '-subject:"domain expir"',
            '-subject:"server notification"',
            '-subject:"hosting account"',
            '-subject:"disk usage"',
            '-subject:"cPanel"',
            # Google automated reports
            '-subject:"Analytics report"',
            '-subject:"Search Console"',
            '-subject:"Google Search performance"',
            # Social media auto-notifications
            '-subject:"new follower"',
            '-subject:"posted an update"',
            '-subject:"new comment on"',
            '-from:notify@twitter.com',
            '-from:notification@facebookmail.com',
            '-from:no-reply@linkedin.com',
            # CRM/tool notifications
            '-from:@hubspot.com',
            '-from:@mailchimp.com',
            '-from:@mandrillapp.com',
            '-from:@sendgrid.net',
            # General automated senders
            '-from:noreply',
            '-from:no-reply',
            '-from:wordpress@',
            '-from:notification@',
            '-from:mailer-daemon',
            '-from:postmaster',
        ]
        query += " " + " ".join(junk_filters)
        results = gmail_service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        logger.info(f"Gmail: found {len(messages)} messages for '{client_name}'")
        emails = []

        for msg_ref in messages:
            msg = gmail_service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            internal_date = int(msg.get("internalDate", 0)) / 1000  # ms to seconds

            emails.append({
                "id": msg_ref["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": datetime.fromtimestamp(internal_date, tz=timezone.utc).isoformat() if internal_date else None,
                "date_ts": internal_date,
                "snippet": msg.get("snippet", "")[:200],
            })

        return emails
    except Exception as e:
        logger.error(f"Gmail search error for '{client_name}': {e}")
        return []


def claude_sentiment(client_name, emails):
    """Use Claude API for sentiment analysis of client emails.
    Returns dict: {"rating": "positive"|"neutral"|"concerned"|"negative", "reason": "..."}
    Falls back to "neutral" on any error.
    """
    if not emails:
        return {"rating": "neutral", "reason": "No recent emails to analyze"}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Try loading from .env.local
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
        try:
            with open(env_path, 'r') as f:
                for line in f:
                    if line.strip().startswith("ANTHROPIC_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                        break
        except Exception:
            pass
    if not api_key:
        return {"rating": "neutral", "reason": "No API key configured"}

    # Build email summary for Claude
    email_text = ""
    for e in emails[:5]:
        email_text += f"Subject: {e.get('subject', '')}\nSnippet: {e.get('snippet', '')}\n\n"

    prompt = (
        f"These are recent email communications about client {client_name}. "
        f"Rate the client relationship health on this scale: positive, neutral, concerned, negative. "
        f'Respond with JSON only: {{"rating": "...", "reason": "one sentence explanation"}}\n\n'
        f"{email_text}"
    )

    request_body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}]
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(request_body).encode(),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            text = data["content"][0]["text"].strip()
            # Parse JSON from response (handle markdown code blocks)
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            rating = result.get("rating", "neutral").lower()
            if rating not in ("positive", "neutral", "concerned", "negative"):
                rating = "neutral"
            return {"rating": rating, "reason": result.get("reason", "")}
    except Exception as e:
        logger.error(f"Claude sentiment error for {client_name}: {e}")
        return {"rating": "neutral", "reason": "Analysis unavailable"}


# Sentiment cache (separate from main health cache)
_sentiment_cache = {"data": {}, "expires": 0}


def get_cached_sentiment(client_name, emails):
    """Get sentiment with caching (30 min TTL)."""
    now = time.time()
    if now < _sentiment_cache["expires"] and client_name in _sentiment_cache["data"]:
        return _sentiment_cache["data"][client_name]

    result = claude_sentiment(client_name, emails)
    _sentiment_cache["data"][client_name] = result
    _sentiment_cache["expires"] = now + CLIENT_HEALTH_CACHE_TTL
    return result


def batch_claude_sentiment(clients_with_emails):
    """Batch sentiment analysis: one Claude call for all clients.
    Returns dict: {client_name: {"rating": "...", "reason": "..."}}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
        try:
            with open(env_path, 'r') as f:
                for line in f:
                    if line.strip().startswith("ANTHROPIC_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                        break
        except Exception:
            pass

    if not api_key:
        return {c: {"rating": "neutral", "reason": "No API key"} for c in clients_with_emails}

    # Build batch prompt
    batch_text = """Analyze the client relationship health for each client below based on their recent emails.

HEALTH SCORING RULES:
- 🟢 positive: Regular communication happening, even if there are overdue tasks (communication = healthy relationship)
- 🟢 positive: No overdue tasks, even without frequent communication
- 🔴 negative: Overdue tasks AND no recent communication (both together = red flag)
- 🔴 negative: Approaching project deadline and not close to completion
- 🔴 negative: Client emailed us and we haven't responded in a long time
- 🟡 concerned: Client has gone quiet (was active, now silent)
- 🟡 concerned: Tone shifted from friendly to terse/curt
- 🟡 concerned: Scope creep discussions or payment delay mentions
- Weight recent emails MORE heavily than older ones
- IGNORE automated emails: WordPress updates, plugin notifications, security alerts, backup notices, uptime monitors
- These are NOT real client communication and should not count

Rate each: positive, neutral, concerned, or negative.
Respond with JSON only: {"results": {"ClientName": {"rating": "...", "reason": "one sentence"}, ...}}

"""

    for client_name, emails in clients_with_emails.items():
        batch_text += f"=== {client_name} ===\n"
        for e in emails[:2]:  # Just top 2 emails per client to keep token count manageable
            batch_text += f"Subject: {e.get('subject', '')}\nSnippet: {e.get('snippet', '')}\n"
        batch_text += "\n"

    request_body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": batch_text}]
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(request_body).encode(),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode())
            text = data["content"][0]["text"]
            # Parse JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                results = parsed.get("results", parsed)
                # Map results back, handling name variations
                mapped = {}
                for client_name in clients_with_emails:
                    if client_name in results:
                        mapped[client_name] = results[client_name]
                    else:
                        # Try case-insensitive match
                        for k, v in results.items():
                            if k.lower() == client_name.lower():
                                mapped[client_name] = v
                                break
                        else:
                            mapped[client_name] = {"rating": "neutral", "reason": "Not analyzed"}
                return mapped
    except Exception as e:
        logger.error(f"Batch Claude sentiment error: {e}")

    return {c: {"rating": "neutral", "reason": "Analysis unavailable"} for c in clients_with_emails}


# ============================================================================
# Active Accounts from ClickUp Admin Space (single source of truth)
# ============================================================================

ACTIVE_ACCOUNTS_LIST_ID = "901320376565"

# Short-name aliases: map long ClickUp task names to short dashboard names.
# If a task name contains the key (lowercase), use the value as display name.
_SHORT_NAME_ALIASES = {
    "dcc marketing": "DCC",
    "f.e.a.s.t.": "FEAST",
    "national association of anorexia": "ANAD",
}

# Cache for active accounts (same TTL as health data)
_active_accounts_cache = {"data": None, "expires": 0}


def _normalize_client_name(raw_name):
    """Convert a ClickUp task name into a short dashboard-friendly client name."""
    raw_lower = raw_name.lower().strip()
    for fragment, short in _SHORT_NAME_ALIASES.items():
        if fragment in raw_lower:
            return short
    # Default: use the task name as-is (trimmed)
    return raw_name.strip()


def fetch_active_accounts(clickup_request_fn):
    """Fetch active client accounts from ClickUp Active Accounts list.

    Single source of truth for which clients appear on the dashboard.
    Only includes accounts with status "engaged" or "new account".

    Returns: {
        "clients": ["ANAD", "DCC", ...],           # display names
        "managers": {"ANAD": "jake", "DCC": "sean", ...},
    }
    """
    global _active_accounts_cache
    now = time.time()
    if _active_accounts_cache["data"] and now < _active_accounts_cache["expires"]:
        return _active_accounts_cache["data"]

    clients = []
    managers = {}

    try:
        # Fetch all tasks (include_closed=false still returns non-closed statuses)
        tasks_data = clickup_request_fn(
            f"/list/{ACTIVE_ACCOUNTS_LIST_ID}/task?include_closed=true&subtasks=false"
        )
        for task in tasks_data.get("tasks", []):
            status = (task.get("status", {}).get("status") or "").lower().strip()
            if status not in ACTIVE_ACCOUNT_STATUSES:
                continue

            display_name = _normalize_client_name(task.get("name", ""))
            clients.append(display_name)

            assignees = task.get("assignees", [])
            if assignees:
                managers[display_name] = assignees[0].get("username", "Unassigned")
            else:
                managers[display_name] = "Unassigned"

        clients.sort()
        logger.info(f"Active accounts from ClickUp: {len(clients)} clients ({', '.join(clients)})")
    except Exception as e:
        logger.error(f"Error fetching active accounts: {e}")

    result = {"clients": clients, "managers": managers}
    _active_accounts_cache["data"] = result
    _active_accounts_cache["expires"] = now + CLIENT_HEALTH_CACHE_TTL
    return result


# ============================================================================
# ClickUp Data
# ============================================================================

def fetch_client_tasks(clickup_request_fn, active_clients=None):
    """Fetch tasks for each active client from ClickUp Operations space.

    Args:
        clickup_request_fn: Function to call ClickUp API.
        active_clients: List of client display names to match against.

    Returns dict: {client_name: [tasks]}
    """
    if not active_clients:
        return {}

    client_tasks = defaultdict(list)

    # 1. Get folders from Operations space
    folders_data = clickup_request_fn(f"/space/{OPERATIONS_SPACE_ID}/folder")
    folders = folders_data.get("folders", [])

    for folder in folders:
        folder_name = folder["name"]
        if folder_name in EXCLUDED_FOLDERS:
            continue

        # Check if this folder is an active client
        matched_client = None
        for client in active_clients:
            if client.lower() == folder_name.lower() or client.lower() in folder_name.lower():
                matched_client = client
                break

        if not matched_client:
            continue

        # Get all lists in this folder
        for lst in folder.get("lists", []):
            tasks_data = clickup_request_fn(
                f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
            )
            for task in tasks_data.get("tasks", []):
                client_tasks[matched_client].append(task)

    # 2. Get Recurring Clients folder
    recurring_data = clickup_request_fn(f"/folder/{RECURRING_CLIENTS_FOLDER_ID}")
    recurring_lists = recurring_data.get("lists", [])
    if not recurring_lists:
        # Try getting lists from folder
        recurring_lists_data = clickup_request_fn(f"/folder/{RECURRING_CLIENTS_FOLDER_ID}/list")
        recurring_lists = recurring_lists_data.get("lists", [])

    for lst in recurring_lists:
        list_name = lst["name"]
        # Match list name to active client
        matched_client = None
        for client in active_clients:
            if client.lower() in list_name.lower() or list_name.lower() in client.lower():
                matched_client = client
                break

        if not matched_client:
            continue

        tasks_data = clickup_request_fn(
            f"/list/{lst['id']}/task?include_closed=true&subtasks=true"
        )
        for task in tasks_data.get("tasks", []):
            client_tasks[matched_client].append(task)

    logger.info(f"Fetched tasks for {len(client_tasks)} clients")
    return dict(client_tasks)


def analyze_client_tasks(tasks):
    """Analyze a client's task list. Returns metrics dict."""
    now = datetime.now(timezone.utc)
    open_tasks = []
    overdue_tasks = []
    completed_tasks = []
    assignees = set()

    for task in tasks:
        status_type = task.get("status", {}).get("type", "")
        is_closed = status_type == "closed"

        # Collect assignees
        for assignee in task.get("assignees", []):
            name = assignee.get("username", "")
            if name:
                assignees.add(name)

        if is_closed:
            completed_tasks.append(task)
        else:
            open_tasks.append(task)
            # Check if overdue
            due_date_ms = task.get("due_date")
            if due_date_ms:
                try:
                    due_date = datetime.fromtimestamp(int(due_date_ms) / 1000, tz=timezone.utc)
                    if due_date < now:
                        days_overdue = (now - due_date).days
                        overdue_tasks.append({
                            "id": task["id"],
                            "name": task["name"],
                            "due_date": due_date.isoformat(),
                            "days_overdue": days_overdue,
                            "url": task.get("url", ""),
                            "status": task.get("status", {}).get("status", ""),
                            "assignees": [a.get("username", "") for a in task.get("assignees", [])],
                        })
                except (ValueError, TypeError):
                    pass

    total = len(tasks)
    completion_rate = (len(completed_tasks) / total * 100) if total > 0 else 0
    avg_days_overdue = (
        sum(t["days_overdue"] for t in overdue_tasks) / len(overdue_tasks)
        if overdue_tasks else 0
    )

    return {
        "open_count": len(open_tasks),
        "overdue_count": len(overdue_tasks),
        "completed_count": len(completed_tasks),
        "total_count": total,
        "completion_rate": round(completion_rate, 1),
        "avg_days_overdue": round(avg_days_overdue, 1),
        "overdue_tasks": sorted(overdue_tasks, key=lambda x: x["days_overdue"], reverse=True),
        "assignees": sorted(list(assignees)),
    }


# ============================================================================
# Health Scoring
# ============================================================================

def calculate_health(task_metrics, days_since_email, days_since_call, email_sentiment):
    """
    Calculate health status based on scoring logic.
    Uses combined "last touchpoint" (min of email/call) for communication signals.
    Returns: {"status": "green"|"yellow"|"red", "reasons": [...]}
    """
    yellow_signals = []
    red_signals = []

    # Task-based signals
    overdue = task_metrics.get("overdue_count", 0)
    if overdue >= 4:
        red_signals.append(f"{overdue} overdue tasks")
    elif 1 <= overdue <= 3:
        yellow_signals.append(f"{overdue} overdue task{'s' if overdue > 1 else ''}")

    # Combined touchpoint signal (minimum of email and call days)
    touchpoints = [d for d in [days_since_email, days_since_call] if d is not None]
    if touchpoints:
        days_since_touchpoint = min(touchpoints)
        if days_since_touchpoint > 14:
            red_signals.append(f"No touchpoint in {days_since_touchpoint} days")
        elif days_since_touchpoint > 7:
            yellow_signals.append(f"Last touchpoint {days_since_touchpoint} days ago")

    # Sentiment signals
    if email_sentiment in ("negative",):
        red_signals.append("Negative email sentiment")
    elif email_sentiment in ("concerned", "mildly_negative"):
        yellow_signals.append("Concerned email tone")

    # Determine status
    if red_signals:
        return {"status": "red", "reasons": red_signals + yellow_signals}
    elif len(yellow_signals) >= 2:
        return {"status": "red", "reasons": yellow_signals, "escalated": True}
    elif yellow_signals:
        return {"status": "yellow", "reasons": yellow_signals}
    else:
        return {"status": "green", "reasons": ["All healthy"]}


# ============================================================================
# Main Aggregation
# ============================================================================

def build_client_health_data(clickup_request_fn):
    """
    Aggregate client health data from all sources (ClickUp, Grain, Gmail, Claude).
    This is the expensive function — call it from a background job only.
    """
    logger.info("Building client health data from APIs...")

    # 0. Fetch active accounts from ClickUp (single source of truth)
    accounts_data = fetch_active_accounts(clickup_request_fn)
    active_clients = accounts_data["clients"]
    account_managers = accounts_data["managers"]

    if not active_clients:
        logger.warning("No active accounts found in ClickUp — dashboard will be empty")

    # 1. ClickUp tasks
    client_tasks = fetch_client_tasks(clickup_request_fn, active_clients=active_clients)

    # 2. Grain recordings
    recordings = fetch_grain_recordings()
    client_last_call = {}
    client_recent_calls = defaultdict(list)
    for rec in recordings:
        matched = match_client_to_recording(rec, active_clients)
        if matched:
            rec_date_str = rec.get("date") or rec.get("created_at") or rec.get("start_time") or rec.get("timestamp")
            if rec_date_str:
                try:
                    # Handle various date formats
                    if isinstance(rec_date_str, (int, float)):
                        rec_date = datetime.fromtimestamp(rec_date_str / 1000 if rec_date_str > 1e12 else rec_date_str, tz=timezone.utc)
                    else:
                        # Try ISO format
                        rec_date_str = rec_date_str.replace("Z", "+00:00")
                        rec_date = datetime.fromisoformat(rec_date_str)
                        if rec_date.tzinfo is None:
                            rec_date = rec_date.replace(tzinfo=timezone.utc)

                    if matched not in client_last_call or rec_date > client_last_call[matched]:
                        client_last_call[matched] = rec_date

                    client_recent_calls[matched].append({
                        "title": rec.get("title") or rec.get("name", ""),
                        "date": rec_date.isoformat(),
                        "url": rec.get("url") or rec.get("link", ""),
                    })
                except Exception as e:
                    logger.debug(f"Could not parse Grain date '{rec_date_str}': {e}")

    # Sort recent calls by date
    for client in client_recent_calls:
        client_recent_calls[client].sort(key=lambda x: x["date"], reverse=True)
        client_recent_calls[client] = client_recent_calls[client][:5]  # Keep top 5

    # 3. Gmail (fetch emails for all clients first, then batch Claude sentiment)
    client_email_data = {}
    clients_with_emails = {}  # {client_name: [emails]} for Claude batch

    # Load client mappings for domain-based email search
    from client_mappings import load_mappings as _load_client_mappings
    _client_mappings = _load_client_mappings()
    _email_domain_config = _client_mappings.get("email_domains", {})

    for client in active_clients:
        # Use domain-based search if configured, otherwise fall back to client name
        domain_entry = _email_domain_config.get(client, {})
        configured_domains = domain_entry.get("domains", [])
        if configured_domains:
            # Build Gmail query: from:domain1.com OR from:domain2.com
            domain_query = " OR ".join(f"from:{d}" for d in configured_domains)
            emails = search_client_emails_all_accounts(domain_query, max_results=3)
        else:
            emails = search_client_emails_all_accounts(client, max_results=3)
        if emails:
            latest = max(emails, key=lambda e: e.get("date_ts", 0))
            clients_with_emails[client] = emails
            client_email_data[client] = {
                "last_date": latest.get("date"),
                "last_date_ts": latest.get("date_ts"),
                "sentiment": "neutral",  # placeholder, updated after batch
                "sentiment_reason": "",
                "recent_emails": [{
                    "subject": e["subject"],
                    "from": e["from"],
                    "date": e["date"],
                    "snippet": e["snippet"],
                } for e in emails],
            }
        else:
            client_email_data[client] = {
                "last_date": None,
                "last_date_ts": None,
                "sentiment": "neutral",
                "sentiment_reason": "No recent emails",
                "recent_emails": [],
            }

    # 3b. Batch Claude sentiment analysis (one API call for all clients)
    if clients_with_emails:
        logger.info(f"Running batch Claude sentiment for {len(clients_with_emails)} clients...")
        batch_results = batch_claude_sentiment(clients_with_emails)
        for client_name, result in batch_results.items():
            if client_name in client_email_data:
                client_email_data[client_name]["sentiment"] = result["rating"]
                client_email_data[client_name]["sentiment_reason"] = result["reason"]
        logger.info("Batch Claude sentiment complete")

    # 4. Build per-client health data
    now_dt = datetime.now(timezone.utc)
    clients = []

    for client_name in active_clients:
        tasks = client_tasks.get(client_name, [])
        task_metrics = analyze_client_tasks(tasks)

        # Days since last email
        email_data = client_email_data.get(client_name, {})
        days_since_email = None
        if email_data.get("last_date_ts"):
            last_email_dt = datetime.fromtimestamp(email_data["last_date_ts"], tz=timezone.utc)
            days_since_email = (now_dt - last_email_dt).days

        # Days since last call
        days_since_call = None
        if client_name in client_last_call:
            days_since_call = (now_dt - client_last_call[client_name]).days

        # Health scoring
        health = calculate_health(
            task_metrics, days_since_email, days_since_call,
            email_data.get("sentiment", "neutral")
        )

        # Combined last touchpoint
        touchpoints = [d for d in [days_since_email, days_since_call] if d is not None]
        days_since_touchpoint = min(touchpoints) if touchpoints else None

        clients.append({
            "name": client_name,
            "health": health,
            "account_manager": account_managers.get(client_name, ""),
            "tasks": {
                "open": task_metrics["open_count"],
                "overdue": task_metrics["overdue_count"],
                "completed": task_metrics["completed_count"],
                "total": task_metrics["total_count"],
                "completion_rate": task_metrics["completion_rate"],
                "avg_days_overdue": task_metrics["avg_days_overdue"],
                "overdue_list": task_metrics["overdue_tasks"][:10],
                "assignees": task_metrics["assignees"],
            },
            "communication": {
                "days_since_email": days_since_email,
                "days_since_call": days_since_call,
                "days_since_touchpoint": days_since_touchpoint,
                "email_sentiment": email_data.get("sentiment", "neutral"),
                "sentiment_reason": email_data.get("sentiment_reason", ""),
                "last_email_date": email_data.get("last_date"),
                "last_call_date": client_last_call.get(client_name, "").isoformat() if client_name in client_last_call else None,
                "recent_emails": email_data.get("recent_emails", [])[:5],
                "recent_calls": client_recent_calls.get(client_name, []),
            },
        })

    # Sort: red first, then yellow, then green
    status_order = {"red": 0, "yellow": 1, "green": 2}
    clients.sort(key=lambda c: (status_order.get(c["health"]["status"], 3), c["name"]))

    result = {
        "clients": clients,
        "summary": {
            "total": len(clients),
            "red": sum(1 for c in clients if c["health"]["status"] == "red"),
            "yellow": sum(1 for c in clients if c["health"]["status"] == "yellow"),
            "green": sum(1 for c in clients if c["health"]["status"] == "green"),
        },
        "last_updated": now_dt.isoformat(),
    }

    logger.info(f"Client health data built: {result['summary']}")
    return result
