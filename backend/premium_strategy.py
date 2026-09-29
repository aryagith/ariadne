"""Reusable master audit and JD gap plan for premium job packets."""
from pathlib import Path
from typing import Literal

from pydantic import Field

from .models import StrictModel
from .latex_workflow import validate_section_priority, write_json


class Finding(StrictModel):
    source_bullet_ids: list[str] = Field(max_length=8)
    finding: str = Field(max_length=500)


class Audit(StrictModel):
    strongest_evidence: list[Finding] = Field(max_length=8)
    weak_explanations: list[Finding] = Field(max_length=6)
    questions_for_user: list[Finding] = Field(max_length=5)


class Requirement(StrictModel):
    requirement: str = Field(max_length=200)
    support: Literal['SUPPORTED', 'NEEDS_CLARIFICATION', 'NO_SOURCE_EVIDENCE']
    source_bullet_ids: list[str] = Field(max_length=6)
    treatment: str = Field(max_length=300)


class GapPlan(StrictModel):
    requirements: list[Requirement] = Field(min_length=3, max_length=10)
    selected_bullet_ids: list[str] = Field(min_length=5, max_length=18)
    edit_targets: list[Finding] = Field(max_length=4)
    omitted_material: list[Finding] = Field(max_length=6)
    skills_focus: list[str] = Field(max_length=12)


def make_plan(context, jd, audit_runner, plan_runner, folder):
    known = {b['id'] for b in context['bullets']}
    audit = audit_runner.call('master_audit',
        'Audit the authoritative master resume as a reusable evidence bank, independently of this JD. '
        'Identify its strongest concrete accomplishments, unclear explanations, and questions that '
        'could uncover missing facts. Cite source bullet IDs. No invented metrics, rewrites, or removal '
        'from the protected master.', context, Audit)
    for finding in audit.strongest_evidence + audit.weak_explanations + audit.questions_for_user:
        if set(finding.source_bullet_ids) - known:
            raise ValueError('Master audit cited an unknown source bullet')
    plan = plan_runner.call('gap_analysis',
        'Plan a truthful, full one-page resume for this exact JD before drafting. Map requirements '
        'to source evidence and distinguish omitted evidence, unclear facts, and absent qualifications. '
        'Do not infer Linux/Unix/Mac, work authorization, dates, or metrics. Select 5–18 source bullets '
        'within at most seven non-education entries. Select an engineering design team only when its '
        'work supports this JD, and keep ONE or TWO relevant personal projects. Prioritize relevant '
        'production engineering and database evidence. Identify '
        'at most four bullet targets where rewriting could help. The master audit is advice, not '
        'additional factual evidence.',
        {'master': context, 'master_audit': audit.model_dump(), 'job_description': jd}, GapPlan)
    selected = plan.selected_bullet_ids
    if set(selected) - known or len(set(selected)) != len(selected):
        raise ValueError('Gap plan selected an unknown or duplicate source bullet')
    for requirement in plan.requirements:
        if set(requirement.source_bullet_ids) - known:
            raise ValueError('Gap plan cited an unknown source bullet')
    validate_section_priority(context, selected, final=True)
    Path(folder).mkdir(parents=True, exist_ok=True)
    write_json(Path(folder) / 'master-audit.json', audit.model_dump())
    write_json(Path(folder) / 'gap-plan.json', plan.model_dump())
    return plan


def drafting_data(context, jd, plan):
    selected = set(plan.selected_bullet_ids)
    entries = {b['entry_id'] for b in context['bullets'] if b['id'] in selected}
    scoped = {**context,
              'entries': [e for e in context['entries'] if e['id'] in entries],
              'bullets': [b for b in context['bullets'] if b['id'] in selected]}
    return {'master': scoped, 'job_description': jd, 'editing_brief': plan.model_dump()}
