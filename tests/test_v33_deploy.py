"""No launchd commands run: release lifecycle and TLS-independent paths are mocked."""
import contextlib
import io
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch,Mock
from scripts import activate_release as d

@unittest.skipIf(os.name == 'nt', 'macOS release lifecycle uses POSIX symlinks')
class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.base=self.root/'app';self.data=self.root/'data'
        self.old=self.base/'releases/v3.2.2';self.new=self.base/'releases/v3.3.0'
        for p in [self.old,self.new,self.data/'v2',self.base/'venv/bin',self.new/'docs',self.new/'deploy']:p.mkdir(parents=True,exist_ok=True)
        (self.base/'current').symlink_to(self.old);(self.base/'venv/bin/python').touch()
        (self.new/'docs/部署与运维说明.md').write_text('instructions')
        self.plist=self.root/'main.plist';self.plist.write_bytes(plistlib.dumps({'Label':d.LABEL,'KeepAlive':True}))
        self.guard=self.root/'guard.plist';self.guard.write_bytes(b'guard')
        self.backup=self.data/'release-backups/verified';self.backup.mkdir(parents=True)
        (self.backup/'warehouse.duckdb').write_bytes(b'previous database')
        (self.data/'v2/warehouse.duckdb').write_bytes(b'previous database')
    def tearDown(self):self.tmp.cleanup()
    def test_same_plist_only_kickstart_no_unload(self):
        with patch.object(d,'command') as cmd,patch.object(d,'online_backup',return_value=self.backup),patch.object(d,'wait_health',return_value={'ok':True}),patch.object(d,'unload') as unload,contextlib.redirect_stdout(io.StringIO()):
            d.activate(self.base,self.data,self.new,self.plist,self.guard)
        self.assertFalse(unload.called);self.assertEqual((self.base/'current').resolve(),self.new)
        self.assertEqual(cmd.call_args_list[-1].args,('sudo','-n','launchctl','kickstart','-k','system/'+d.LABEL))
    def test_same_dependencies_do_not_find_uv_or_sync(self):
        for p in [self.old,self.new]:(p/'requirements-server.txt').write_text('duckdb==1.5.6\n')
        with patch.object(d,'command') as cmd:self.assertTrue(d.sync_dependencies(self.base,self.old,self.new,self.root))
        self.assertEqual(cmd.call_count,1);self.assertNotIn('sync',cmd.call_args.args)
    def test_changed_dependencies_offline_and_failure_before_switch(self):
        (self.old/'requirements-server.txt').write_text('old');(self.new/'requirements-server.txt').write_text('new')
        uv=self.root/'.hermes/tools/uv-test/uv';uv.parent.mkdir(parents=True);uv.touch();uv.chmod(0o700)
        with patch.object(d,'command',side_effect=RuntimeError('no cached wheel')) as cmd:
            with self.assertRaises(RuntimeError):d.sync_dependencies(self.base,self.old,self.new,self.root)
        self.assertIn('--offline',cmd.call_args.args);self.assertEqual((self.base/'current').resolve(),self.old)
    def test_changed_plist_waits_before_install_and_bootstrap(self):
        p=self.new/'deploy'/(d.LABEL+'.plist');p.write_bytes(plistlib.dumps({'Label':d.LABEL,'KeepAlive':False}))
        actions=[]
        with patch.object(d,'command',side_effect=lambda *a:actions.append(a)),patch.object(d,'unload',side_effect=lambda *a:actions.append(('unloaded',*a))),patch.object(d,'wait_port_free'),patch.object(d,'online_backup',return_value=self.backup),patch.object(d,'wait_health',return_value={'ok':True}),contextlib.redirect_stdout(io.StringIO()):d.activate(self.base,self.data,self.new,self.plist,self.guard)
        ix=actions.index(('unloaded',d.LABEL));self.assertEqual(actions[ix+1][2],'install');self.assertEqual(actions[ix+2][3],'bootstrap')
    def test_unload_waits_for_confirmed_missing_service(self):
        results=[Mock(returncode=0,stderr='',stdout='running'),Mock(returncode=113,stderr='Could not find service',stdout='')]
        with patch.object(d,'command'),patch.object(d.subprocess,'run',side_effect=results) as run,patch.object(d.time,'sleep') as sleep:d.unload(d.LABEL)
        self.assertEqual(run.call_count,2);self.assertEqual(sleep.call_count,1)
    def test_unload_timeout_or_permission_does_not_mean_unloaded(self):
        with patch.object(d,'command'),patch.object(d.subprocess,'run',return_value=Mock(returncode=1,stderr='permission denied',stdout='')),patch.object(d.time,'monotonic',side_effect=[0,31]):
            with self.assertRaisesRegex(RuntimeError,'未执行 bootstrap'):d.unload(d.LABEL)
    def test_failed_health_restores_db_and_preserves_failed_state(self):
        def command(*args):
            if 'kickstart' in args:(self.data/'v2/warehouse.duckdb').write_bytes(b'failed migration')
        with patch.object(d,'command',side_effect=command),patch.object(d,'online_backup',return_value=self.backup),patch.object(d,'unload') as unload,patch.object(d,'wait_port_free'),patch.object(d,'wait_health',side_effect=RuntimeError('health')):
            with self.assertRaises(RuntimeError):d.activate(self.base,self.data,self.new,self.plist,self.guard)
        self.assertEqual((self.base/'current').resolve(),self.old);self.assertEqual((self.data/'v2/warehouse.duckdb').read_bytes(),b'previous database');self.assertEqual((self.backup/'failed-state/warehouse.duckdb').read_bytes(),b'failed migration');self.assertEqual(unload.call_count,2)
if __name__=='__main__':unittest.main()
