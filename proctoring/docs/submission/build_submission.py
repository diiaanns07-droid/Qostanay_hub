"""Build the two submission PDFs from the editable Markdown sources.

Isolated document tools only: reportlab, pypdf, pymupdf. Product dependencies unchanged.
python docs/submission/build_submission.py --preview-dir PATH_OUTSIDE_GIT
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

import pymupdf as fitz
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[2]
NAVY = colors.HexColor('#172F40')
TEAL = colors.HexColor('#087F83')
INK = colors.HexColor('#20333F')
MUTED = colors.HexColor('#536876')
CREAM = colors.HexColor('#F8FAF9')


def markup(text: str) -> str:
    text = html.escape(text.strip())
    text = re.sub(r'\[([^\]]+)\]\((https://[^)]+)\)', r'<link href="\2" color="#087F83">\1</link>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    return text


def styles(deck: bool) -> dict:
    size = 19 if deck else 10.5
    base = dict(fontName='Qorgau', textColor=INK, alignment=TA_LEFT,
                fontSize=size, leading=size * 1.35, spaceAfter=13 if deck else 6)
    return {
        'p': ParagraphStyle('body', **base),
        'h1': ParagraphStyle('title', fontName='QorgauBold', fontSize=34 if deck else 25,
                             leading=41 if deck else 29, textColor=NAVY, spaceAfter=23 if deck else 8),
        'h2': ParagraphStyle('heading', fontName='QorgauBold', fontSize=22 if deck else 13,
                             leading=29 if deck else 17, textColor=TEAL, spaceAfter=14 if deck else 6),
        'table': ParagraphStyle('table', fontName='Qorgau', fontSize=16 if deck else 9.4,
                                leading=21 if deck else 12, textColor=INK),
        'th': ParagraphStyle('table-header', fontName='QorgauBold', fontSize=15 if deck else 9.4,
                             leading=20 if deck else 12, textColor=colors.white),
        'note': ParagraphStyle('note', fontName='Qorgau', fontSize=13 if deck else 9,
                               leading=17 if deck else 12, textColor=MUTED, spaceAfter=11),
    }


def table(lines: list[str], deck: bool, width: float, st: dict) -> Table:
    rows = [[x.strip() for x in line.strip().strip('|').split('|')] for line in lines]
    rows = [r for r in rows if not all(re.fullmatch(r'[: -]+', c) for c in r)]
    count = len(rows[0])
    assert all(len(r) == count for r in rows), 'Unequal table columns'
    ratios = {2: [0.28, 0.72], 3: [0.30, 0.48, 0.22], 4: [0.12, 0.33, 0.37, 0.18]}[count]
    if rows[0][0] in {'Порядок', 'Шаг'}:
        ratios = [0.10, 0.46, 0.44]
    if rows[0][0] == 'Компонент':
        ratios = [0.32, 0.30, 0.38]
    cells = [[Paragraph(markup(c), st['th'] if i == 0 else st['table']) for c in row]
             for i, row in enumerate(rows)]
    result = Table(cells, colWidths=[width * r for r in ratios], hAlign='LEFT', repeatRows=1)
    commands = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 12 if deck else 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12 if deck else 6),
                ('TOPPADDING', (0, 0), (-1, -1), 10 if deck else 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10 if deck else 5),
                ('LINEBELOW', (0, 1), (-1, -1), .5, colors.HexColor('#D8E2E3'))]
    if count == 4:
        for i, fill in enumerate(['#FBE9E7', '#FFF3D6', '#E9EEF1', '#E1F2E9'], 1):
            commands.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor(fill)))
    result.setStyle(TableStyle(commands))
    return result


def flow(text: str, deck: bool, width: float) -> list:
    st = styles(deck)
    lines = text.strip().splitlines()
    result, i = [], 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith('|'):
            group = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                group.append(lines[i]); i += 1
            result.extend([table(group, deck, width, st), Spacer(1, 13 if deck else 8)])
            continue
        style = 'p'
        if line.startswith('# '):
            line, style = line[2:], 'h1'
        elif line.startswith('## '):
            line, style = line[3:], 'h2'
        elif line.startswith('- '):
            line = '• ' + line[2:]
        elif line.startswith(('partial:', 'Подтверждено на каркасе:', 'zone-rule-1:', 'МАКЕТ')):
            style = 'note'
        result.append(Paragraph(markup(line), st[style]))
        i += 1
    return result


def build(source: Path, output: Path, deck: bool) -> None:
    page_size = (960, 540) if deck else A4
    margin = 46 if deck else 39
    width = page_size[0] - 2 * margin
    doc = SimpleDocTemplate(str(output), pagesize=page_size, leftMargin=margin, rightMargin=margin,
                            topMargin=42 if deck else 35, bottomMargin=40 if deck else 33,
                            title='Qorgau Exam: презентация' if deck else 'Qorgau Exam: описание',
                            author='Команда Qorgau Exam', pageCompression=1)
    separator = '<!-- slide -->' if deck else '<!-- pagebreak -->'
    chunks = source.read_text(encoding='utf-8').split(separator)
    story = []
    for i, chunk in enumerate(chunks):
        if i:
            story.append(PageBreak())
        story.extend(flow(chunk, deck, width - 12))

    def page(canvas, document):
        canvas.saveState()
        canvas.setFillColor(CREAM)
        canvas.rect(0, 0, *page_size, fill=1, stroke=0)
        canvas.setFillColor(MUTED)
        canvas.setFont('Qorgau', 10 if deck else 8)
        canvas.drawString(margin, 18, 'Qorgau Exam · пакет отбора · 08.10.2026')
        canvas.drawRightString(page_size[0] - margin, 18, f'{document.page} / {len(chunks)}')
        canvas.restoreState()
    doc.build(story, onFirstPage=page, onLaterPages=page)
    pdf = PdfReader(output)
    assert len(pdf.pages) == len(chunks), f'{output.name}: overflow into {len(pdf.pages)} pages, expected {len(chunks)}'


def inspect(output: Path, expected_pages: int, preview_dir: Path) -> dict:
    pdf = fitz.open(output)
    assert len(pdf) == expected_pages
    text = '\n'.join(page.get_text() for page in pdf)
    for word in ['Qorgau', 'преподавател', 'зон']:
        assert word in text, f'Cyrillic extraction failed: {word}'
    assert '\ufffd' not in text and '\x00' not in text
    for forbidden in ['списывает', 'виновен', 'вероятность списывания']:
        assert forbidden not in text.lower(), forbidden
    pages = []
    preview_dir.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(pdf):
        for block in page.get_text('blocks'):
            if len(block) > 6 and block[6] == 0:
                assert block[0] >= 0 and block[1] >= 0 and block[2] <= page.rect.width + 1 and block[3] <= page.rect.height + 1
        file = preview_dir / f'{output.stem}-{i+1:02}.png'
        page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False).save(file)
        pages.append({'page': i + 1, 'characters': len(page.get_text()), 'fonts': [f[3] for f in page.get_fonts()]})
    return {'file': output.name, 'pages': pages, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'cyrillic_extraction': 'PASS', 'text_bounds': 'PASS', 'visual_review': 'PENDING'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview-dir', type=Path, required=True)
    parser.add_argument('--font-dir', type=Path, default=Path('C:/Windows/Fonts'))
    args = parser.parse_args()
    pdfmetrics.registerFont(TTFont('Qorgau', str(args.font_dir / 'arial.ttf')))
    pdfmetrics.registerFont(TTFont('QorgauBold', str(args.font_dir / 'arialbd.ttf')))
    pdfmetrics.registerFontFamily('Qorgau', normal='Qorgau', bold='QorgauBold', italic='Qorgau', boldItalic='QorgauBold')
    outputs = [(ROOT/'docs/pitch/PRESENTATION.md', ROOT/'docs/pitch/Qorgau_Exam_Presentation.pdf', True, 10),
               (ROOT/'docs/submission/ОПИСАНИЕ.md', ROOT/'docs/submission/Qorgau_Exam_Description.pdf', False, 2)]
    report = []
    for src, output, deck, count in outputs:
        build(src, output, deck)
        item = inspect(output, count, args.preview_dir)
        item['source_sha256'] = hashlib.sha256(src.read_bytes()).hexdigest()
        report.append(item)
    target = args.preview_dir/'build_report.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'outputs': [x['file'] for x in report], 'pages': [len(x['pages']) for x in report],
                      'checks': 'PASS; visual inspection pending', 'report': str(target)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
