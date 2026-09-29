import httpx
from backend import postings
from backend.tool import initialize,add_application_link
from backend.db import connect


def test_greenhouse_streamed_bytes_parse_and_exclude_form(monkeypatch):
    initialize()
    job=add_application_link('https://job-boards.greenhouse.io/example/jobs/123')
    description='Build production software using Python and SQL. Collaborate on reliable features. '*4
    html='<main><div class="job__description">'+description+'<form>Not job-description evidence</form></div></main>'
    real_client=httpx.Client
    transport=httpx.MockTransport(lambda request:httpx.Response(200,text=html))
    monkeypatch.setattr(postings.httpx,'Client',lambda **kwargs:real_client(transport=transport,**kwargs))
    snapshot=postings.fetch_posting(job['job_id'])
    with connect() as c:
        row=c.execute('SELECT text,provenance FROM snapshots WHERE id=?',(snapshot,)).fetchone()
    assert row['text']==description.strip()
    assert row['provenance']=='FETCHED_OFFICIAL_GREENHOUSE'
    assert 'Not job-description evidence' not in row['text']
