"""Offline dependency smoke test, temporary files only."""
import importlib.metadata,json,tempfile,zipfile
from pathlib import Path
import duckdb,python_calamine
assert duckdb.__version__=='1.5.6'
assert importlib.metadata.version('python-calamine')=='0.8.3'
assert duckdb.connect(':memory:').execute('select sum(i) from range(10) t(i)').fetchone()[0]==45
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'probe.xlsx'
 with zipfile.ZipFile(p,'w') as z:
  z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
  z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
  z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Test" sheetId="1" r:id="rId1"/></sheets></workbook>')
  z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
  z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>42</v></c></row></sheetData></worksheet>')
 book=python_calamine.CalamineWorkbook.from_path(str(p));assert book.get_sheet_by_index(0).to_python()[0][0]==42
print(json.dumps({'import':'passed','duckdb':duckdb.__version__,'python-calamine':importlib.metadata.version('python-calamine'),'sql':'passed','xlsx':'passed'}))
