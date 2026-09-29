"""Pure fixture state machine. This module deliberately has NO live browser capability."""
import json
import time
from .db import audit, connect, digest, uid


def validate_packet(c, packet_id):
    row = c.execute('SELECT * FROM packets WHERE id=?', (packet_id,)).fetchone()
    if not row:
        raise ValueError('Unknown packet')
    packet = json.loads(row['content'])
    if digest(packet) != row['hash']:
        raise ValueError('Packet integrity check failed')
    if not packet['synthetic'] or not packet['review']['mocked']:
        raise ValueError('Fixture execution accepts synthetic packets only. Live execution is disabled.')
    if not packet['review']['approved']:
        raise ValueError('Content review has not passed')
    job = c.execute('SELECT material_hash FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
    latest = c.execute('SELECT hash FROM snapshots WHERE job_id=? ORDER BY created DESC LIMIT 1', (row['job_id'],)).fetchone()
    if job[0] != packet['job']['material_hash'] or latest[0] != packet['snapshot']['hash']:
        raise ValueError('Posting changed; prepare a new packet')
    return row, packet


def approve(packet_id, action, identity='local-human'):
    if action not in {'disclose', 'submit'}:
        raise ValueError('Unknown action')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row, packet = validate_packet(c, packet_id)
        expected = 'DEMO_READY' if action == 'disclose' else 'WAITING_FOR_SUBMIT_APPROVAL'
        if row['status'] != expected:
            raise ValueError('Approval not available in this state')
        approval_id = uid()
        c.execute('INSERT INTO approvals VALUES (?,?,?,?,?,?,?,0)',
                  (approval_id, packet_id, row['hash'], action, packet['destination'], identity, time.time()+600))
        audit(c, 'FIXTURE_APPROVAL_'+action.upper(), packet_id)
        return approval_id


def execute_fixture(packet_id, operation, approval_id, simulate_timeout=False, identity='local-human'):
    # No generic click, Enter, JavaScript, URL, network, shell, or arbitrary values accepted.
    if operation not in {'fill', 'upload', 'submit'}:
        raise ValueError('Unsupported operation')
    action = 'submit' if operation == 'submit' else 'disclose'
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row, packet = validate_packet(c, packet_id)
        approval = c.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
        if not approval or approval['used'] or approval['expires'] <= time.time():
            raise ValueError('Missing, expired, or consumed approval')
        for key, expected in {'packet_id': packet_id, 'packet_hash': row['hash'], 'action': action,
                              'destination': packet['destination'], 'identity': identity}.items():
            if approval[key] != expected:
                raise ValueError('Approval scope mismatch')
        required = 'WAITING_FOR_SUBMIT_APPROVAL' if action == 'submit' else 'DEMO_READY'
        if row['status'] != required:
            raise ValueError('Unsafe or repeated application transition')
        c.execute('UPDATE approvals SET used=1 WHERE id=?', (approval_id,))
        if action == 'disclose':
            # Fixture atomically models filling + upload of exactly this immutable packet.
            state = 'WAITING_FOR_SUBMIT_APPROVAL'
        else:
            # Commit unknown BEFORE the simulated side effect. A crash is never retryable.
            state = 'SUBMISSION_UNKNOWN'
        c.execute('UPDATE packets SET status=? WHERE id=?', (state, packet_id))
        audit(c, 'FIXTURE_'+operation.upper(), packet_id)
    if action == 'submit' and not simulate_timeout:
        with connect() as c:
            c.execute("UPDATE packets SET status='DEMO_SUBMITTED' WHERE id=? AND status='SUBMISSION_UNKNOWN'", (packet_id,))
            audit(c, 'FIXTURE_RECEIPT', packet_id)
        state = 'DEMO_SUBMITTED'
    return {'status': state, 'receipt': 'fixture:'+packet_id if state == 'DEMO_SUBMITTED' else None, 'live': False}


def execute_live(*args, **kwargs):
    raise ValueError('Live execution disabled: browser isolation and the approval boundary are not implemented')
