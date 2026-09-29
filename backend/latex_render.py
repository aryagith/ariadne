import json
import os
import re
from pathlib import Path
import subprocess
from .db import ROOT

MIN_VERTICAL_FILL = 0.94


def assess_layout(qa, overfull_boxes):
    """Require content near the bottom of the printable area as well as safe fit."""
    qa['overfull_boxes'] = overfull_boxes
    qa['vertical_fill_ratio'] = qa['pages'][0]['vertical_fill_ratio'] if qa['pages'] else 0
    qa['technical_passed'] = qa['page_count'] == 1 and qa['bounds_ok'] and overfull_boxes == 0 and qa['vertical_fill_ratio'] <= 1.005
    qa['underfilled'] = qa['page_count'] == 1 and qa['vertical_fill_ratio'] < MIN_VERTICAL_FILL
    qa['minimum_vertical_fill'] = MIN_VERTICAL_FILL
    qa['passed'] = qa['technical_passed'] and not qa['underfilled']
    return qa


def compile_resume(folder):
    try:
        qa, images = _compile_resume(folder)
        # Preserve substantive sections first: trim skills height locally before the
        # layout model is ever asked to remove an experience/project bullet.
        path = Path(folder)/'resume.tex'
        for budget in (2,1,0):
            if qa['technical_passed']:
                break
            source = path.read_text(encoding='utf-8')
            match = re.search(r'\\def\\AutoapplySkillLines\{(\d)\}',source)
            if not match or int(match.group(1))<=budget:
                continue
            source = source[:match.start(1)]+str(budget)+source[match.end(1):]
            path.write_text(source,encoding='utf-8')
            qa, images = _compile_resume(folder)
        return qa, images
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        raise ValueError('Local LaTeX/PDF stage failed: '+str(exc)[:300]) from exc


def _compile_resume(folder):
    folder = Path(folder).resolve()
    home = Path.home()
    latex = Path(os.getenv('AUTOAPPLY_TECTONIC') or os.getenv('CODEX_TECTONIC_PATH') or
                 str(home/'.codex/plugins/cache/openai-bundled/latex/0.2.7/bin/tectonic.exe'))
    runtime = home/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    python = Path(os.getenv('AUTOAPPLY_PDF_PYTHON', str(runtime/'python/python.exe')))
    poppler = Path(os.getenv('AUTOAPPLY_PDFTOPPM', str(runtime/'native/poppler/Library/bin/pdftoppm.exe')))
    if not all(p.exists() for p in (latex, python, poppler)):
        raise ValueError('LaTeX/PDF runtime missing; configure AUTOAPPLY_TECTONIC, AUTOAPPLY_PDF_PYTHON, AUTOAPPLY_PDFTOPPM')
    run = subprocess.run([str(latex), '--untrusted', '--keep-logs', '--outdir', str(folder), str(folder/'resume.tex')],
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    (folder/'compile.txt').write_text(run.stdout+'\n'+run.stderr, encoding='utf-8')
    if run.returncode:
        raise ValueError('LaTeX compilation failed; inspect compile.txt. No PDF approved.')
    geometry = re.search(r'margin=(\.45|\.5)in', (folder/'resume.tex').read_text(encoding='utf-8'))
    margin = float(geometry.group(1))*72 if geometry else 36
    inspection = subprocess.run([str(python), str(ROOT/'scripts/inspect_resume_pdf.py'), str(folder/'resume.pdf'), str(margin)],
                                capture_output=True, text=True, encoding='utf-8', timeout=60, check=True)
    qa = json.loads(inspection.stdout)
    log = (folder/'resume.log').read_text(encoding='utf-8', errors='replace') if (folder/'resume.log').exists() else ''
    tex_lines = (folder/'resume.tex').read_text(encoding='utf-8').splitlines()
    qa['overfull_locations'] = []
    for match in list(re.finditer(r'Overfull \\[hv]box[^\n]*?at lines? (\d+)(?:--(\d+))?', log))[:5]:
        line = int(match.group(1))
        qa['overfull_locations'].append({
            'tex_line': line,
            'source_excerpt': ' '.join(tex_lines[max(0,line-2):min(len(tex_lines),line+2)])[:240]})
    skill_budget = re.search(r'\\def\\AutoapplySkillLines\{(\d)\}',(folder/'resume.tex').read_text(encoding='utf-8'))
    if skill_budget:
        qa['skill_line_budget'] = int(skill_budget.group(1))
        qa['rendered_skills'] = list(dict.fromkeys(bytes.fromhex(token).decode('utf-8') for token in re.findall(r'AUTOAPPLY-SKILL:([a-f0-9]+)',log)))
    assess_layout(qa, log.count('Overfull \\hbox') + log.count('Overfull \\vbox'))
    (folder/'layout.json').write_text(json.dumps(qa, indent=2), encoding='utf-8')
    (folder/'resume.txt').write_text('\n\n'.join(page['text'] for page in qa['pages']), encoding='utf-8')
    subprocess.run([str(poppler), '-f', '1', '-l', '3', '-scale-to', '1600', '-png', str(folder/'resume.pdf'), str(folder/'page')],
                   capture_output=True, timeout=60, check=True)
    # Ignore stale page images left by a previous, longer render.
    images = [p for p in sorted(folder.glob('page-*.png')) if int(p.stem.split('-')[-1]) <= min(3,qa['page_count'])]
    return qa, images
