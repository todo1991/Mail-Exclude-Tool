"""Pure filter logic — no DB access here."""
import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def is_valid_email(s: str) -> bool:
    return bool(EMAIL_RE.match(s))


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[1] if "@" in email else ""


def parse_lines(text: str) -> list[str]:
    """Split textarea input by newlines/commas, trim, lowercase, dedupe (preserve order)."""
    if not text:
        return []
    raw = re.split(r"[\n,;\s]+", text)
    seen, out = set(), []
    for item in raw:
        v = item.strip().lower()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


@dataclass
class FilterResult:
    kept: list[str]
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def excluded_count(self) -> int:
        return sum(self.breakdown.values())


PUBLIC_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "msn.com", "ymail.com", "mail.com",
    "protonmail.com", "proton.me", "gmx.com", "gmx.net",
    "qq.com", "163.com", "126.com", "sina.com", "yeah.net",
    "yandex.com", "yandex.ru", "zoho.com", "fastmail.com",
})


def is_public_domain(domain: str) -> bool:
    return (domain or "").lower() in PUBLIC_EMAIL_DOMAINS


# Region classification — TLD-based heuristic.
# Limitation: companies in CN/ME often use .com; those need explicit
# Domain Exclude entries since TLD alone can't classify them.
CN_TLDS = frozenset({"cn", "hk"})
ME_TLDS = frozenset({
    # Gulf + Levant Arab states
    "ae", "sa", "qa", "kw", "om", "bh", "jo", "lb",
    "sy", "iq", "ye", "ps",
    # Iran
    "ir",
    # North Africa MENA
    "eg",
    # Turkey
    "tr",
    # Israel
    "il",
})

REGION_MODES = (
    "all", "cn_only", "me_only", "cn_me_only", "no_cn", "no_me", "no_cn_me",
)


def region_of(email: str) -> str:
    """Return 'cn', 'me', or 'other' based on email's TLD."""
    d = domain_of(email)
    tld = d.rsplit(".", 1)[-1].lower() if "." in d else ""
    if tld in CN_TLDS:
        return "cn"
    if tld in ME_TLDS:
        return "me"
    return "other"


def apply_region_filter(emails: list[str], mode: str) -> list[str]:
    """Filter source emails by region. Unknown/empty mode → no filter."""
    if mode == "all" or not mode:
        return emails
    keep = {
        "cn_only":    lambda r: r == "cn",
        "me_only":    lambda r: r == "me",
        "cn_me_only": lambda r: r in ("cn", "me"),
        "no_cn":      lambda r: r != "cn",
        "no_me":      lambda r: r != "me",
        "no_cn_me":   lambda r: r not in ("cn", "me"),
    }.get(mode)
    if not keep:
        return emails
    return [e for e in emails if keep(region_of(e))]


def apply_filter(
    source: list[str],
    requesters: list[str] | None = None,
    extra_emails: list[str] | None = None,
    extra_domains: list[str] | None = None,
    perm_emails: list[str] | None = None,
    perm_domains: list[str] | None = None,
) -> FilterResult:
    """Return source minus the union of all exclusion sets, with per-reason counts.

    `requesters` are excluded as exact emails only — their domains are NOT
    auto-excluded, since a public domain (gmail.com, yahoo.com, ...) would wipe
    out unrelated subscribers. To exclude any of those domains too, the user must
    add it explicitly via the More Exclude step (the UI suggests this).
    """
    requester_set = {normalize_email(r) for r in (requesters or []) if normalize_email(r)}

    extra_email_set = set(extra_emails or [])
    extra_domain_set = set(extra_domains or [])
    perm_email_set = set(perm_emails or [])
    perm_domain_set = set(perm_domains or [])

    kept: list[str] = []
    breakdown = {
        "requester_email": 0,
        "permanent_email": 0,
        "permanent_domain": 0,
        "adhoc_email": 0,
        "adhoc_domain": 0,
    }

    for raw in source:
        email = normalize_email(raw)
        if not email:
            continue
        d = domain_of(email)

        if email in requester_set:
            breakdown["requester_email"] += 1
        elif email in perm_email_set:
            breakdown["permanent_email"] += 1
        elif d in perm_domain_set:
            breakdown["permanent_domain"] += 1
        elif email in extra_email_set:
            breakdown["adhoc_email"] += 1
        elif d in extra_domain_set:
            breakdown["adhoc_domain"] += 1
        else:
            kept.append(email)

    return FilterResult(kept=kept, breakdown=breakdown)
