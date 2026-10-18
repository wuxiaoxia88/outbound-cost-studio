"""Reproducible application bundles; never bundle business databases."""
from pathlib import Path
from v2.version import VERSION
import argparse,hashlib,json,zipfile,urllib.request,subprocess,sys,struct
BASE=Path(__file__).resolve().parent
LOCK={
 'duckdb':{'version':'1.5.6','mac':'dcccce20965e6986cd083fdf192c461685ad0b93cd1ccd0b2a8207f1185f078b','win':'09ff51b230219f0d8b47fc8a1e17fb595ba9fab0c3d96a6de4d00b8ff86b3cf1'},
 'python-calamine':{'version':'0.8.3','mac':'07fe3050517bc8f94b407f11ad43332d17b0d468c4cd245b49cac068ba00587e','win':'de1a82f7f1e61fb492845723ce1a8532b70dce6df04c337bdd8dcab483ad6929'}}
# Exact upstream release hashes; changing a version requires explicit lock changes.
TARGETS=[('Mac-AppleSilicon','runtime-mac','mac','macosx_11_0_arm64','lib/python3.12/site-packages',['启动出港成本分析.command']),('Windows-x64','runtime-windows','win','win_amd64','Lib/site-packages',['启动出港成本分析-Windows.bat','Windows自检.bat'])]
def install_dependencies():
 for platform,runtime,key,tag,site,_ in TARGETS:
  dest=BASE/runtime/site;dest.mkdir(parents=True,exist_ok=True);records=[]
  for name,pin in LOCK.items():
   meta=json.load(urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{pin["version"]}/json'))
   matches=[f for f in meta['urls'] if f['filename'].endswith(f'-cp312-cp312-{tag}.whl')]
   if len(matches)!=1:raise RuntimeError(f'Expected one pinned wheel: {name} {tag}')
   f=matches[0];data=urllib.request.urlopen(f['url']).read();digest=hashlib.sha256(data).hexdigest()
   if digest!=pin[key] or digest!=f['digests']['sha256']:raise RuntimeError('Wheel hash mismatch: '+f['filename'])
   import io
   with zipfile.ZipFile(io.BytesIO(data)) as z:
    if z.testzip():raise RuntimeError('Wheel CRC error')
    for info in z.infolist():
     path=Path(info.filename)
     if path.is_absolute() or '..' in path.parts or '.data' in path.parts:raise RuntimeError('Unsupported wheel layout')
    z.extractall(dest)
   records.append({'package':name,'version':pin['version'],'wheel':f['filename'],'sha256':digest,'source':f['url']})
   print('Installed',platform,f['filename'],flush=True)
  (BASE/runtime/'dependencies.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
 pth=BASE/'runtime-windows/python312._pth';lines=pth.read_text().splitlines()
 if 'Lib/site-packages' not in lines:lines.insert(2,'Lib/site-packages')
 if 'import site' not in lines:lines.append('import site')
 pth.write_text('\n'.join(lines)+'\n')
def verify_dependencies():
 for _,runtime,key,_,site,_ in TARGETS:
  dest=BASE/runtime/site
  manifest=json.loads((BASE/runtime/'dependencies.json').read_text())
  for name,pin in LOCK.items():
   rec=next(r for r in manifest if r['package']==name)
   if rec['sha256']!=pin[key]:raise RuntimeError('Runtime dependency lock mismatch')
   metadata=dest/(name.replace('-','_')+'-'+pin['version']+'.dist-info/METADATA')
   if ('Version: '+pin['version']) not in metadata.read_text():raise RuntimeError('Runtime package metadata mismatch')
  if key=='win':
   for p in dest.rglob('*.pyd'):
    b=p.read_bytes();off=struct.unpack_from('<I',b,0x3c)[0]
    if b[:2]!=b'MZ' or b[off:off+4]!=b'PE\0\0' or struct.unpack_from('<H',b,off+4)[0]!=0x8664:raise RuntimeError('Wrong Windows architecture: '+str(p))
   if 'Lib/site-packages' not in (BASE/runtime/'python312._pth').read_text():raise RuntimeError('Windows package path missing')
 if sys.platform=='darwin':subprocess.run([str(BASE/'runtime-mac/bin/python3'),str(BASE/'scripts/check_dependencies.py')],check=True)
def package_sources(runtime,launch):
 # Allowlisted product files; no customer fixtures, design data, collaboration notes or archives.
 common=['server.py','使用说明.md','README.md','LICENSE','CHANGELOG.md','build_packages.py','scripts/check_dependencies.py','scripts/make_demo_data.py','docs/demo.png','docs/部署与运维说明.md','requirements-server.txt']
 common += [str(p.relative_to(BASE)) for p in (BASE/'scripts').glob('*') if p.suffix in ('.py','.sh','.json')]
 for directory in ['web','v2']:
  common += [str(p.relative_to(BASE)) for p in (BASE/directory).glob('*') if p.is_file() and p.suffix in ('.py','.js','.html','.css') and not (directory=='web' and p.name.startswith('v3') and not p.name.startswith('v34'))]
 common += [str(p.relative_to(BASE)) for p in (BASE/'tests').glob('test_*') if p.suffix in ('.py','.cjs')]
 common += launch
 common += [str(p.relative_to(BASE)) for p in (BASE/runtime).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store']
 return sorted(set(common))

def build():
 from scripts.build_web import bundle
 if (BASE/'web/v34.bundle.js').read_text()!=bundle():raise RuntimeError('请先运行 python scripts/build_web.py 更新前端包')
 verify_dependencies();out=BASE/'dist';out.mkdir(exist_ok=True);result=[]
 for platform,runtime,_,_,_,launch in TARGETS:
  p=out/('出港成本分析-'+platform+'.zip');root='OutboundCostStudio-'+platform
  files=package_sources(runtime,launch)
  manifest={'product':'出港成本分析','version':VERSION,'platform':platform,'files':[]}
  for name in files:
   f=BASE/name
   manifest['files'].append({'path':name,'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
  manifest_bytes=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
  manifest_name='出港成本分析-'+platform+'-files.json'
  (out/manifest_name).write_bytes(manifest_bytes)
  with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
   for name in files:z.write(BASE/name,root+'/'+name)
   z.writestr(root+'/文件清单.json',manifest_bytes)
  with zipfile.ZipFile(p) as z:
   assert z.testzip() is None
   assert not any('/data/' in n or '/qa/' in n or '/archive/' in n or '/design/' in n or '/协作/' in n for n in z.namelist())
   assert len(z.namelist())==len(files)+1
   for record in manifest['files']:
    assert hashlib.sha256(z.read(root+'/'+record['path'])).hexdigest()==record['sha256']
  result.append({'file':p.name,'root':root,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'manifest':manifest_name,'manifestSha256':hashlib.sha256(manifest_bytes).hexdigest(),'fileCount':len(files)+1,'dependencies':{k:v['version'] for k,v in LOCK.items()},'windowsExecutionVerified':False if 'Windows' in platform else None})
 (out/'packages.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--install-deps',action='store_true');p.add_argument('--verify-only',action='store_true');a=p.parse_args()
 if a.install_deps:install_dependencies()
 if a.verify_only:verify_dependencies()
 else:build()
