"""Inline the data script(s) into a single self-contained preview file."""
from pathlib import Path
here = Path(__file__).parent
html = (here / 'index.html').read_text()
for name in ('data.js', 'history.js'):
    tag = '<script src="%s"></script>' % name
    assert tag in html, tag
    html = html.replace(tag, '<script>' + (here / name).read_text() + '</script>')
(here / '界面预览.html').write_text(html)
print('built', (here / '界面预览.html').stat().st_size)
