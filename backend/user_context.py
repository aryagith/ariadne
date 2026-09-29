"""Locally saved, user-confirmed facts and search preferences for Autoapply."""

import time
from typing import Literal

from pydantic import Field

from .db import audit, connect, packed, setting
from .models import StrictModel


CONTEXT_KEY = 'autoapply:user-context:v1'


class ReusableFacts(StrictModel):
    residence_city: str | None = Field(default=None, max_length=120)
    residence_region: str | None = Field(default=None, max_length=120)
    residence_country: str | None = Field(default=None, max_length=120)
    canada_work_authorization: str | None = Field(default=None, max_length=300)
    canada_sponsorship_needed: bool | None = None
    school: str | None = Field(default=None, max_length=200)
    year_of_study: str | None = Field(default=None, max_length=80)
    completed_internships: int | None = Field(default=None, ge=0, le=100)
    internship_part_of_academic_program: bool | None = None
    expected_graduation: str | None = Field(default=None, max_length=80)
    grading_scale_max_points: Literal[9] | None = None
    cumulative_gpa_nine_point: float | None = Field(default=None, ge=0, le=9)
    cumulative_gpa_as_of: str | None = Field(default=None, max_length=40)


class SearchPreferences(StrictModel):
    preferred_coop_months: Literal[4] | None = None
    conditional_coop_months: Literal[8] | None = None
    conditional_coop_rule: str | None = Field(default=None, max_length=300)
    resume_selection_rule: str | None = Field(default=None, max_length=300)
    mobile_revision_summary: bool | None = None


class UserContextUpdate(StrictModel):
    facts: ReusableFacts = Field(default_factory=ReusableFacts)
    preferences: SearchPreferences = Field(default_factory=SearchPreferences)


def get_user_context():
    return setting(CONTEXT_KEY, {'facts': {}, 'preferences': {}, 'provenance': {}})


def save_user_context(update: dict, evidence: str):
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 500:
        raise ValueError('A concise user-confirmation evidence reference is required')
    parsed = UserContextUpdate.model_validate(update)
    changes = {
        'facts': parsed.facts.model_dump(exclude_none=True),
        'preferences': parsed.preferences.model_dump(exclude_none=True),
    }
    if not any(changes.values()):
        raise ValueError('At least one confirmed fact or preference is required')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT value FROM settings WHERE key=?', (CONTEXT_KEY,)).fetchone()
        import json
        current = json.loads(row[0]) if row else {'facts': {}, 'preferences': {}, 'provenance': {}}
        for group, values in changes.items():
            current[group].update(values)
            for key in values:
                current['provenance'][f'{group}.{key}'] = {'evidence': evidence.strip(), 'updated': time.time()}
        c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (CONTEXT_KEY, packed(current)))
        audit(c, 'USER_CONTEXT_SAVED', CONTEXT_KEY)
    return current
