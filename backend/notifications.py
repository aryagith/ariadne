"""Optional explicit notification dispatch; only public role metadata and packet links."""
import os
from urllib.parse import urlsplit
import httpx
from .db import connect, setting


def dispatch():
    url = os.getenv('NTFY_URL', '')
    app = os.getenv('PUBLIC_APP_URL', '').rstrip('/')
    if not setting('notifications_enabled', False):
        return {'sent': 0, 'disabled': True}
    if urlsplit(url).scheme != 'https' or urlsplit(app).scheme != 'https':
        raise ValueError('Configure HTTPS NTFY_URL and PUBLIC_APP_URL first')
    with connect() as c:
        rows = c.execute('SELECT * FROM notifications WHERE delivered=0 AND error IS NULL ORDER BY created LIMIT 10').fetchall()
    sent = 0
    for row in rows:
        headers = {'Title': 'Apply Desk: packet ready', 'Click': f"{app}/?packet={row['packet_id']}"}
        if os.getenv('NTFY_TOKEN'):
            headers['Authorization'] = 'Bearer '+os.environ['NTFY_TOKEN']
        # Claim before send. Unknown delivery is visible, never automatically duplicated.
        with connect() as c:
            claimed = c.execute("UPDATE notifications SET error='Delivery pending or unknown' WHERE id=? AND error IS NULL AND delivered=0", (row['id'],)).rowcount
        if not claimed:
            continue
        try:
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                r = client.post(url, content=row['title'].encode('utf-8'), headers=headers)
                r.raise_for_status()
            with connect() as c:
                c.execute('UPDATE notifications SET delivered=1,error=NULL WHERE id=?', (row['id'],))
            sent += 1
        except httpx.HTTPError:
            pass
    return {'sent': sent}
