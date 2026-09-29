from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Fact(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=700)
    source: str = Field(min_length=1, max_length=200)


class Bullet(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=500)
    source_fact_ids: list[str] = Field(min_length=1, max_length=8)


class Profile(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    contact: str = Field(max_length=250)
    heading: str = Field(min_length=1, max_length=200)
    facts: list[Fact] = Field(min_length=1, max_length=20)
    bullets: list[Bullet] = Field(min_length=1, max_length=6)
    # Only explicitly confirmed answers belong here; absent answers remain unknown.
    answers: dict[str, str] = Field(default_factory=dict, max_length=20)

    @model_validator(mode='after')
    def references(self):
        ids = [f.id for f in self.facts]
        if len(set(ids)) != len(ids) or len({b.id for b in self.bullets}) != len(self.bullets):
            raise ValueError('Fact and bullet IDs must be unique')
        if any(not set(b.source_fact_ids) <= set(ids) for b in self.bullets):
            raise ValueError('Unknown source fact')
        if any(len(k) > 100 or len(v) > 1000 for k, v in self.answers.items()):
            raise ValueError('Answer too long')
        return self


class Candidate(StrictModel):
    target_bullet_id: str
    candidate_id: str
    replacement_text: str = Field(min_length=1, max_length=500)
    source_fact_ids: list[str] = Field(min_length=1, max_length=8)
    job_requirement_ids: list[str] = Field(max_length=8)


class Proposals(StrictModel):
    candidates: list[Candidate] = Field(min_length=3, max_length=3)


class Evaluation(StrictModel):
    support: Literal['SUPPORTED', 'UNSUPPORTED', 'UNCLEAR']
    relevance: float = Field(ge=0, le=100)
    clarity: float = Field(ge=0, le=100)
    specificity: float = Field(ge=0, le=100)
    concision: float = Field(ge=0, le=100)
    reason: str
    provider: str
    mocked: bool

    @property
    def score(self):
        return round(.4*self.relevance + .25*self.clarity + .2*self.specificity + .15*self.concision, 2)


class ApprovalRequest(StrictModel):
    action: Literal['disclose', 'submit']


class FixtureAction(StrictModel):
    operation: Literal['fill', 'upload', 'submit']
    approval_id: str
    simulate_timeout: bool = False


class Login(StrictModel):
    password: str = Field(max_length=300)


class SnapshotInput(StrictModel):
    text: str = Field(min_length=50, max_length=18000)


class Prepare(StrictModel):
    profile_id: str


class Consent(StrictModel):
    confirmed: Literal[True]
