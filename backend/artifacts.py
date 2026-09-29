import html
import io
import json
import os
import zipfile
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def resume_text(packet):
    label = 'SYNTHETIC DEMO - NOT FOR APPLICATION' if packet['synthetic'] else 'DRAFT - REVIEW BEFORE APPLYING'
    return '\n\n'.join([label, packet['name'], packet['contact'], packet['heading'], *packet['selected_texts']])


def resume_html(packet):
    esc = html.escape
    label = 'SYNTHETIC DEMO - NOT FOR APPLICATION' if packet['synthetic'] else 'DRAFT - REVIEW BEFORE APPLYING'
    bullets = ''.join(f'<li>{esc(t)}</li>' for t in packet['selected_texts'])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Resume preview</title><style>body{{max-width:720px;margin:50px auto;padding:24px;font:16px/1.6 Georgia,serif;color:#172a26}}small{{font:11px Arial;color:#786348;letter-spacing:1px}}h1{{margin-bottom:0}}h2{{font-size:18px;border-bottom:1px solid #bbb;padding-bottom:10px}}li{{margin:16px 0}}@media print{{body{{margin:0}}}}</style>
    <small>{label}</small><h1>{esc(packet['name'])}</h1><p>{esc(packet['contact'])}</p><h2>{esc(packet['heading'])}</h2><ul>{bullets}</ul></html>'''


def resume_pdf(packet):
    out = io.BytesIO()
    font = 'Helvetica'
    for path in [os.getenv('RESUME_FONT', ''), 'C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if path and Path(path).is_file():
            if 'ResumeSans' not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont('ResumeSans', path))
            font = 'ResumeSans'
            break
    def style(size, leading, color='#233b34'):
        return ParagraphStyle('resume', fontName=font, fontSize=size, leading=leading, textColor=colors.HexColor(color), alignment=TA_LEFT)
    label = 'SYNTHETIC DEMO - NOT FOR APPLICATION' if packet['synthetic'] else 'DRAFT - REVIEW BEFORE APPLYING'
    story = [Paragraph(label, style(8, 12, '#8c6544')), Spacer(1, 25),
             Paragraph(html.escape(packet['name']), style(28, 34)), Spacer(1, 9),
             Paragraph(html.escape(packet['contact']), style(10, 15)), Spacer(1, 27),
             Paragraph(html.escape(packet['heading']), style(13, 18)), Spacer(1, 15)]
    for text in packet['selected_texts']:
        story.extend([Paragraph(html.escape(text), style(11, 17)), Spacer(1, 14)])
    doc = SimpleDocTemplate(out, pagesize=(612, 792), leftMargin=54, rightMargin=54, topMargin=48, bottomMargin=48,
                            title='Application resume - draft', author=packet['name'])
    doc.build(story)
    return out.getvalue()


def packet_zip(packet):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('resume.pdf', resume_pdf(packet))
        archive.writestr('resume.html', resume_html(packet))
        archive.writestr('resume.txt', resume_text(packet))
        archive.writestr('answers.json', json.dumps(packet['answers'], indent=2, ensure_ascii=False))
        archive.writestr('review.json', json.dumps(packet, indent=2, ensure_ascii=False))
        archive.writestr('APPLY.txt', f"{packet['job']['company']} - {packet['job']['title']}\n\nPosting: {packet['destination']}\n\n"
                          f"Eligibility: {packet['eligibility']}\nConfirm: {', '.join(packet['missing_answers'])}\n\n"
                          'Review the resume and official posting. Answer employer-specific questions yourself. '
                          'This packet does not assert eligibility or submit an application. '
                          'Synthetic demo packets must never be sent to employers.\n')
    return out.getvalue()
