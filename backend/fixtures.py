from .models import Profile

DEMO = Profile.model_validate({
    'name': 'Alex Example', 'contact': 'alex@example.invalid | Example City',
    'heading': 'Selected projects - synthetic demonstration',
    'facts': [
        {'id': 'f1', 'text': 'Built a Python API with SQLite for a campus event project.', 'source': 'Synthetic fixture, not user history'},
        {'id': 'f2', 'text': 'Wrote unit tests for the campus event API.', 'source': 'Synthetic fixture, not user history'},
    ],
    'bullets': [
        {'id': 'b1', 'text': 'Worked on a campus event project using Python and SQLite.', 'source_fact_ids': ['f1']},
        {'id': 'b2', 'text': 'Did testing for the campus event API.', 'source_fact_ids': ['f2']},
    ],
    'answers': {},
})

DEMO_REQUIREMENTS = 'Synthetic practice requirements: build Python APIs, use relational databases, and write automated unit tests. This is not the employer job description.'
