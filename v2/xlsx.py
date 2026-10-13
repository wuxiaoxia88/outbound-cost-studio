"""Streaming XLSX fallback, independent of the archived application."""
import zipfile
from xml.etree import ElementTree as ET
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def xlsx_rows(path):
    """Ignore untrustworthy dimension metadata; stream every worksheet row."""
    with zipfile.ZipFile(path) as z:
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            with z.open('xl/sharedStrings.xml') as f:
                for _, e in ET.iterparse(f, events=('end',)):
                    if e.tag==NS+'si':
                        shared.append(''.join(t.text or '' for t in e.iter(NS+'t'))); e.clear()
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        rel={e.attrib['Id']:e.attrib['Target'] for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in wb.find(NS+'sheets'):
            target=rel[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            with z.open(target) as f:
                parent=None
                for event,e in ET.iterparse(f, events=('start','end')):
                    if event=='start' and e.tag==NS+'sheetData': parent=e
                    if event!='end' or e.tag!=NS+'row': continue
                    row=[]
                    for c in e:
                        if c.tag!=NS+'c': continue
                        letters=''.join(a for a in c.get('r','A1') if a.isalpha()); idx=0
                        for a in letters: idx=idx*26+ord(a)-64
                        while len(row)<idx: row.append('')
                        typ=c.get('t'); v=c.find(NS+'v')
                        if typ=='inlineStr': val=''.join(t.text or '' for t in c.iter(NS+'t'))
                        elif typ=='s': val=shared[int(v.text)] if v is not None else ''
                        else: val=v.text if v is not None and v.text is not None else ''
                        row[idx-1]=val
                    yield sheet.get('name'), int(e.get('r','0')), row
                    e.clear()
                    if parent is not None: parent.clear()
