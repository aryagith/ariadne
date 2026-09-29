"""Conservative, explainable routing for subscription-backed resume preparation."""
import re

PREMIUM_COMPANIES = frozenset({
    'alphabet', 'google', 'amazon', 'apple', 'meta', 'facebook', 'netflix',
    'microsoft', 'nvidia',
})
MIN_PREMIUM_HOURLY_USD = 60
POLICY_VERSION = 'tier-routing-v1'


def _company_key(company):
    value = re.sub(r'[^a-z0-9]+', ' ', company.lower()).strip()
    return re.sub(r'\s+(?:inc|llc|ltd|corp|corporation)$', '', value).strip()


def _hourly_floor(text):
    """Only read unambiguous USD hourly amounts; do not guess from annual pay."""
    patterns = (
        r'\$([\d,]+(?:\.\d+)?)\s*(?:-|–|—|to)\s*\$?([\d,]+(?:\.\d+)?)\s*(?:USD\s*)?(?:per\s+hour|/\s*(?:hour|hr))',
        r'\$([\d,]+(?:\.\d+)?)\s*(?:USD\s*)?(?:per\s+hour|/\s*(?:hour|hr))',
    )
    amounts = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if re.search(r'up\s+to\s*$', text[max(0, match.start()-12):match.start()], re.IGNORECASE):
                continue
            currency_context = text[max(0, match.start()-5):match.end()+5]
            if not re.search(r'USD|US\s*\$', currency_context, re.IGNORECASE):
                continue
            amounts.append(float(match.group(1).replace(',', '')))
    return min(amounts) if amounts else None


def route_for(job, snapshot):
    company = _company_key(job.get('company') or '')
    if company in PREMIUM_COMPANIES:
        return {'tier': 'premium', 'reason': 'explicit_top_company', 'evidence': job['company'],
                'policy_version': POLICY_VERSION}
    floor = _hourly_floor(snapshot.get('text') or '')
    if floor is not None and floor >= MIN_PREMIUM_HOURLY_USD:
        return {'tier': 'premium', 'reason': 'official_hourly_floor', 'evidence': floor,
                'policy_version': POLICY_VERSION}
    return {'tier': 'standard', 'reason': 'no_premium_evidence', 'evidence': floor,
            'policy_version': POLICY_VERSION}
