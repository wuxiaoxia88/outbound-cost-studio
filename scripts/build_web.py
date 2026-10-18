"""Reproducible browser bundle; source files remain individually editable."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCES=['core','charts','simulation','inputs','history','regions','report','main','quote','settings','combo']
def bundle():
    return '\n;\n'.join('// '+p.name+'\n'+p.read_text() for k in SOURCES for p in [ROOT/'web'/('v34.js' if k=='main' else 'v34-'+k+'.js')])
if __name__=='__main__':
    (ROOT/'web/v34.bundle.js').write_text(bundle())
