"""Run with bundled PDF Python. Emit QA JSON without exposing private resume text in logs."""
import json
import sys
import pdfplumber

with pdfplumber.open(sys.argv[1]) as document:
    margin = float(sys.argv[2]) if len(sys.argv)>2 else 36.0
    pages = []
    for page in document.pages:
        chars = [c for c in page.chars if c.get('text','').strip()]
        clipped = [c for c in chars if c['x0'] < 10 or c['x1'] > page.width-10 or c['top'] < 10 or c['bottom'] > page.height-10]
        words = page.extract_words(x_tolerance=1.5)
        ordered_chars = sorted(chars,key=lambda c:c['top'])
        collisions = 0
        for i,a in enumerate(ordered_chars):
            for b in ordered_chars[i+1:]:
                if b['top'] >= a['bottom']:
                    break
                intersection = max(0,min(a['x1'],b['x1'])-max(a['x0'],b['x0'])) * max(0,min(a['bottom'],b['bottom'])-max(a['top'],b['top']))
                smaller = min((a['x1']-a['x0'])*(a['bottom']-a['top']), (b['x1']-b['x0'])*(b['bottom']-b['top']))
                if smaller>0 and intersection/smaller>.6:
                    collisions+=1
        overlaps = 0
        for i, a in enumerate(words):
            for b in words[i+1:]:
                if abs(a['top']-b['top']) > 3 and min(a['x1'],b['x1'])-max(a['x0'],b['x0']) > 2 and min(a['bottom'],b['bottom'])-max(a['top'],b['top']) > 2:
                    overlaps += 1
        bottom = max((c['bottom'] for c in chars), default=margin)
        fill = max(0, (bottom-margin)/(page.height-2*margin))
        pages.append({'characters': len(chars), 'clipped_characters': len(clipped), 'overlapping_words': overlaps,
                      'overlapping_characters': collisions,
                      'content_bottom': round(bottom,2), 'bottom_gap_points': round(page.height-margin-bottom,2),
                      'vertical_fill_ratio': round(fill,4), 'text': page.extract_text(x_tolerance=1.5) or ''})
    print(json.dumps({'page_count': len(pages), 'pages': pages, 'bounds_ok': all(p['characters'] > 50 and not p['clipped_characters'] and not p['overlapping_words'] and not p['overlapping_characters'] for p in pages)}))
