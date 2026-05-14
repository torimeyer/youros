"""macOS Contacts service.

Uses Swift/CNContactFetchRequest to bulk-load all contacts from macOS Contacts.app.
Chosen over AppleScript (loop = 4m9s for 1223 contacts) and SQLite direct read
(AddressBook DBs have no local rows when contacts are synced via iCloud).
Swift via subprocess completes in ~1s and outputs clean JSON.

Contacts are cached in-process for 5 minutes so the Swift subprocess cost
is paid at most once per cache window.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import time

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_cache_data: list[dict] | None = None
_cache_ts: float = 0.0

_SWIFT_SCRIPT = """\
import Contacts
import Foundation

let store = CNContactStore()
let keys = [
    CNContactGivenNameKey, CNContactFamilyNameKey, CNContactNicknameKey,
    CNContactOrganizationNameKey, CNContactPhoneNumbersKey, CNContactEmailAddressesKey,
] as [CNKeyDescriptor]
let request = CNContactFetchRequest(keysToFetch: keys)
var contacts: [[String: Any]] = []
try? store.enumerateContacts(with: request) { contact, _ in
    let phones = contact.phoneNumbers.map { $0.value.stringValue }
    let emails = contact.emailAddresses.map { $0.value as String }
    let parts = [contact.givenName, contact.familyName].filter { !$0.isEmpty }
    let fullName = parts.joined(separator: " ")
    let displayName = !contact.nickname.isEmpty ? contact.nickname
        : (!fullName.isEmpty ? fullName : contact.organizationName)
    if displayName.isEmpty && phones.isEmpty && emails.isEmpty { return }
    contacts.append(["name": displayName, "phone_numbers": phones, "emails": emails])
}
let data = try! JSONSerialization.data(withJSONObject: contacts)
print(String(data: data, encoding: .utf8)!)
"""


def is_macos() -> bool:
    return platform.system().lower() == "darwin"


def _fetch_contacts() -> list[dict]:
    """Run the Swift script and return parsed contacts list."""
    try:
        result = subprocess.run(
            ["swift", "-"],
            input=_SWIFT_SCRIPT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        logger.warning("swift not found — cannot load contacts")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("Swift contacts script timed out")
        return []

    if result.returncode != 0:
        logger.warning("Swift contacts script exited %d: %s", result.returncode, result.stderr[:300])
        return []

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse contacts JSON: %s", exc)
        return []


def list_contacts() -> list[dict]:
    """Return all macOS contacts, refreshing the 5-minute in-process cache if stale.

    Each contact is: {name: str, phone_numbers: list[str], emails: list[str]}.
    Returns [] on non-macOS platforms or if Contacts access is not granted.
    """
    global _cache_data, _cache_ts
    now = time.time()
    if _cache_data is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache_data
    contacts = _fetch_contacts() if is_macos() else []
    _cache_data = contacts
    _cache_ts = now
    return contacts


def invalidate_cache() -> None:
    global _cache_data, _cache_ts
    _cache_data = None
    _cache_ts = 0.0


def search_by_prefix(q: str, limit: int = 8) -> list[dict]:
    """Return contacts whose name contains q (case-insensitive), up to limit.

    Each result: {name, phone, email, identifier}
    identifier is the primary phone or email used to address a message.
    Returns [] on non-macOS or if Contacts access is not granted.
    """
    q_lower = q.lower().strip()
    if not q_lower:
        return []
    results: list[dict] = []
    for c in list_contacts():
        name = c.get("name", "")
        if not name or q_lower not in name.lower():
            continue
        phones = c.get("phone_numbers", [])
        emails = c.get("emails", [])
        identifier = phones[0] if phones else (emails[0] if emails else "")
        if not identifier:
            continue
        results.append({
            "name": name,
            "phone": phones[0] if phones else None,
            "email": emails[0] if emails else None,
            "identifier": identifier,
        })
        if len(results) >= limit:
            break
    return results
