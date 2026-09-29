"""Immutable master import and constrained edits to the supplied resume template."""
import hashlib
import json
import re
from pathlib import Path
from .db import ROOT

MASTER = ROOT / 'master resume'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register_master():
    path = MASTER / 'master.tex'
    metadata = MASTER / 'source.json'
    if metadata.exists():
        return load_master()[1]
    data = {'sha256': sha(path), 'source': 'User-provided LaTeX resume, September 23 2026',
            'protected': True, 'target_pages': 1, 'identity': 'Arya Gosavi'}
    metadata.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


def load_master():
    data = json.loads((MASTER / 'source.json').read_text(encoding='utf-8'))
    if sha(MASTER / 'master.tex') != data['sha256']:
        raise ValueError('Master changed. Import a new explicit source version before tailoring.')
    return (MASTER / 'master.tex').read_text(encoding='utf-8-sig'), data


def argument(text, start):
    while text[start].isspace():
        start += 1
    if text[start] != '{':
        raise ValueError('Expected a braced LaTeX argument')
    depth, pos = 1, start + 1
    while pos < len(text):
        if text[pos] == '\\':
            pos += 2
            continue
        depth += (text[pos] == '{') - (text[pos] == '}')
        if depth == 0:
            return text[start+1:pos], pos+1
        pos += 1
    raise ValueError('Unbalanced master LaTeX')


def plain(text):
    # Preserve content while removing only presentation wrappers; no TeX execution.
    text = re.sub(r'\\(?:textbf|textit|emph|underline)\s*\{', '{', text)
    text = text.replace(r'\circ', ' degrees').replace('^{', '{')
    text = re.sub(r'(?<!\\)\$', '', text)
    text = re.sub(r'\\([%&#_$])', r'\1', text)
    text = re.sub(r'\\[A-Za-z]+\s*', '', text)
    return ' '.join(text.replace('{', '').replace('}', '').split())


def escape(text):
    substitutions = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '%': r'\%',
                     '&': r'\&', '#': r'\#', '_': r'\_', '$': r'\$', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(substitutions.get(ch, ch) for ch in text)


def index_master(source):
    body = source.index(r'\begin{document}')
    sections = list(re.finditer(r'\\section\{([^}]+)\}', source[body:]))
    entries, bullets = [], []
    for i, section in enumerate(sections):
        start = body + section.end()
        end = body + sections[i+1].start() if i+1 < len(sections) else source.index(r'\end{document}')
        headings = list(re.finditer(r'\\(resumeSubheading|resumeProjectHeading)\{', source[start:end]))
        for heading in headings:
            pos = start + heading.start()
            cursor = pos + len(heading.group(1)) + 1
            args = []
            for _ in range(4 if heading.group(1) == 'resumeSubheading' else 2):
                arg, cursor = argument(source, cursor)
                args.append(arg)
            finish = source.index(r'\resumeItemListEnd', cursor, end) + len(r'\resumeItemListEnd')
            entry_id = f'e{len(entries)+1}'
            entry = {'id': entry_id, 'section': section.group(1), 'context': ' | '.join(plain(a) for a in args),
                     'start': pos, 'end': finish, 'bullets': []}
            for match in re.finditer(r'\\resumeItem\{', source[cursor:finish]):
                astart = cursor + match.end() - 1
                value, bend = argument(source, astart)
                b = {'id': f'b{len(bullets)+1}', 'entry_id': entry_id, 'text': plain(value),
                     'start': cursor+match.start(), 'end': bend, 'arg_start': astart+1, 'arg_end': bend-1}
                bullets.append(b)
                entry['bullets'].append(b['id'])
            entries.append(entry)
    if not entries or not bullets:
        raise ValueError('Master template has no supported resume entries')
    return entries, bullets


def source_context(source):
    entries, bullets = index_master(source)
    return {'entries': [{k: e[k] for k in ('id', 'section', 'context', 'bullets')} for e in entries],
            'bullets': [{k: b[k] for k in ('id', 'entry_id', 'text')} for b in bullets]}


def ats_format(source):
    """Presentation-only changes: plain contact links, ordinary headings, no icon fonts/tables."""
    center = re.search(r'\\begin\{center\}(.*?)\\end\{center\}', source, re.S)
    if center:
        header = center.group(1)
        name = re.search(r'\{\\Huge\s+\\scshape\s+([^}]+)\}', header)
        phone = re.search(r'\\faPhone\\\s*([\d+() -]+)', header)
        urls = [argument(header, match.end()-1)[0] for match in re.finditer(r'\\href\{', header)]
        if name and phone and urls:
            emails = [u for u in urls if u.startswith('mailto:')]
            websites = [u for u in urls if u.startswith(('https://','http://'))]
            def link(url):
                visible = re.sub(r'^(?:mailto:|https?://(?:www\.)?)', '', url).rstrip('/')
                return r'\href{'+url+'}{'+escape(visible)+'}'
            contact = r' \enspace|\enspace '.join([escape(phone.group(1).strip()), *map(link, emails)])
            web = r' \enspace|\enspace '.join(map(link, websites))
            replacement = ('\\begin{center}\n{\\LARGE\\bfseries '+name.group(1)+r'}\\[2pt]'+'\n'
                           +r'{\small '+contact+r'}\\[1pt]'+'\n'+r'{\small '+web+'}\n'
                           +r'\vspace{-8pt}'+'\n'+r'\end{center}')
            source = source[:center.start()] + replacement + source[center.end():]
    macros = {
        'resumeSubheading': (4, r'\vspace{-3pt}\item \textbf{#1}\hfill\textbf{\small #2}\\'+ '\n'
                            +r'\textit{\small #3}\hfill\textit{\small #4}\vspace{-7pt}'),
        'resumeSubSubheading': (2, r'\item \textit{\small #1}\hfill\textit{\small #2}\vspace{-7pt}'),
        'resumeProjectHeading': (2, r'\item {\small #1\hfill #2}\par'),
    }
    for macro, (count, body) in macros.items():
        declaration = r'\newcommand{' + chr(92) + macro + '}['+str(count)+']'
        start = source.find(declaration)
        if start >= 0:
            _, end = argument(source, start+len(declaration))
            source = source[:start]+declaration+'{\n'+body+'\n}'+source[end:]
    for package in ('fontawesome5','marvosym','tabularx','multicol','fancyhdr','latexsym'):
        source = source.replace(r'\usepackage{'+package+'}', '')
    for setting in (r'\setlength{\multicolsep}{-3.0pt}', r'\setlength{\columnsep}{-1pt}', r'\setlength{\tabcolsep}{0in}'):
        source = source.replace(setting,'')
    source = re.sub(r'\\titleformat\{\\section\}[^\n]*', lambda _: r'\titleformat{\section}{\large\bfseries\raggedright}{}{0em}{}[\vspace{1pt}{\titlerule[0.35pt]}]'
                    +'\n'+r'\titlespacing*{\section}{0pt}{4pt}{6pt}', source)
    for level in ('i','ii'):
        source = source.replace(r'\renewcommand\labelitem'+level+r'{$\vcenter{\hbox{\tiny$\bullet$}}$}',
                                r'\renewcommand\labelitem'+level+r'{\textbullet}')
    source = source.replace(r'\vspace{-8pt}\begin{itemize}', r'\begin{itemize}')
    source = source.replace(r'\end{itemize}\vspace{1pt}', r'\end{itemize}\vspace{0pt}')
    return source


def regular_weight(text):
    """Remove selective emphasis without changing the underlying TeX content."""
    pattern = r'\\(?:textbf|textit|emph)\s*\{'
    while match := re.search(pattern, text):
        body, end = argument(text, match.end()-1)
        text = text[:match.start()] + body + text[end:]
    return text


def render_source(source, selected, replacements=None, layout='standard', *, focus_skills=False, job_description=None, skill_lines=3, ranked_skills=None):
    if layout not in {'standard', 'compact'}:
        raise ValueError('Unknown layout preset')
    replacements = replacements or {}
    if focus_skills:
        from .resume_skills import plan_skills, apply_skills
        source = apply_skills(source, plan_skills(source, selected, replacements, job_description, ranked_skills), skill_lines)
    entries, bullets = index_master(source)
    known = {b['id'] for b in bullets}
    if len(selected) != len(set(selected)) or set(selected)-known or set(replacements)-set(selected):
        raise ValueError('Unknown/duplicate selection or replacement outside selection')
    changes = []
    for entry in entries:
        keep = set(selected) & set(entry['bullets'])
        if not keep and entry['section'] != 'Education':
            changes.append((entry['start'], entry['end'], ''))
            continue
        for b in (b for b in bullets if b['entry_id'] == entry['id']):
            if b['id'] not in keep:
                changes.append((b['start'], b['end'], ''))
            else:
                body = escape(replacements[b['id']]) if b['id'] in replacements else source[b['arg_start']:b['arg_end']]
                changes.append((b['arg_start'], b['arg_end'], regular_weight(body)))
    for a, b, value in sorted(changes, reverse=True):
        source = source[:a] + value + source[b:]
    # Keep project names bold while every technology stack uses regular weight.
    for match in reversed(list(re.finditer(r'\\resumeProjectHeading\s*\{', source))):
        body, end = argument(source, match.end()-1)
        if '$|$' in body:
            title, stack = body.split('$|$', 1)
            source = source[:match.end()] + title + '$|$' + regular_weight(stack) + source[end-1:]
    source = source.replace(r'\newpage', '').replace(r'\vspace*{7mm}', '')
    source = re.sub(r'\\resumeItemListStart\s*\\resumeItemListEnd', '', source)
    source = source.replace(r'\pdfgentounicode=1', r'\ifdefined\pdfgentounicode\pdfgentounicode=1\fi')
    source = source.replace(r'\input{glyphtounicode}', r'\ifdefined\pdfglyphtounicode\input{glyphtounicode}\fi')
    # Remove sections whose entries were all omitted, including empty list wrappers.
    source = re.sub(r'\\section\{(?:Experience|Engineering Design Teams|Personal Projects)\}\s*\\resume(?:SubHeading|Project)ListStart\s*\\resume(?:SubHeading|Project)ListEnd', '', source)
    # Fixed page geometry replaces the original unusually small/negative margins.
    source = re.sub(r'\\addtolength\{\\(?:oddsidemargin|evensidemargin|textwidth|topmargin|textheight)\}\{[^}]+\}', '', source)
    margin = '.45in' if layout == 'compact' else '.5in'
    source = source.replace(r'\begin{document}', r'\usepackage[letterpaper,margin='+margin+r']{geometry}'+'\n'+r'\begin{document}')
    source = source.replace(r'\vspace*{15pt}', '')
    source = source.replace(r'\end{itemize}\vspace{-9pt}', r'\end{itemize}\vspace{1pt}')
    # Headers must wrap long project names instead of extending beyond the page.
    source = source.replace(r'{1.001\textwidth}{l@{\extracolsep{\fill}}r}', r'{1.0\textwidth}{p{0.86\textwidth}@{\extracolsep{\fill}}r}')
    return ats_format(source)
