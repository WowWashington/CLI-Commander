"""Actual selected AST with fake imports/processes and temporary storage only.

Run directly; no application/config import or real CLI/daemon/tmux interaction.
"""
import argparse
import ast
import builtins
from contextlib import contextmanager
import io
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]


def blocked(*args, **kwargs):
    raise AssertionError('Real provider, process or network action forbidden')


def extract(file, names, namespace, imports=None):
    tree = ast.parse((ROOT / 'blades' / file).read_text())
    chosen = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in chosen} == set(names)
    for n in chosen:
        n.decorator_list = []
    real_import = builtins.__import__
    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            assert not name and all(key in (imports or {}) for key in fromlist), fromlist
            return SimpleNamespace(**imports)
        if name.split('.')[0] in {'blades', 'config', 'store', 'tmuxctl', 'flask'}:
            raise AssertionError('Production import forbidden')
        return real_import(name, globals, locals, fromlist, level)
    namespace['__builtins__'] = {**vars(builtins), '__import__': fake_import}
    exec(compile(ast.fix_missing_locations(ast.Module(body=chosen, type_ignores=[])), file, 'exec'), namespace)
    return SimpleNamespace(**{n.name: namespace[n.name] for n in chosen})


class HTTPError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def abort(code, message):
    raise HTTPError(code, message)


class OllamaOfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('run',):
            (self.root / name).mkdir()
        self.patches = [mock.patch.object(socket, 'socket', blocked),
                        mock.patch.object(socket, 'create_connection', blocked),
                        mock.patch.object(subprocess, 'run', blocked),
                        mock.patch.object(subprocess, 'Popen', blocked),
                        mock.patch.object(os, 'execve', blocked), mock.patch.object(os, 'kill', blocked)]
        for patch in self.patches:
            patch.start(); self.addCleanup(patch.stop)
        self.run = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout='NAME ID SIZE MODIFIED\nexample:local abc 1GB now\n', stderr=''))
        self.execve = mock.Mock(side_effect=RuntimeError('fake exec boundary'))
        self.fakeos = SimpleNamespace(environ={'PATH': '/synthetic/bin', 'OLLAMA_HOST': 'http://remote.invalid'},
                                      path=os.path, pathsep=os.pathsep, defpath=os.defpath, execve=self.execve)
        self.response = SimpleNamespace(status=200, read=lambda n:b'{"cloud":{"disabled":true}}')
        self.connection = SimpleNamespace(request=mock.Mock(),getresponse=lambda:self.response,close=mock.Mock())
        self.http = SimpleNamespace(client=SimpleNamespace(HTTPConnection=mock.Mock(return_value=self.connection),HTTPException=http.client.HTTPException))
        self.ons = dict(http=self.http, json=json, re=re, os=self.fakeos, subprocess=SimpleNamespace(run=self.run, DEVNULL=-3, TimeoutExpired=subprocess.TimeoutExpired),
                        argparse=argparse, sys=SimpleNamespace(stderr=io.StringIO()), HOST='http://127.0.0.1:11434')
        self.ollama = extract('ollama.py', {'validate_model', 'environment', 'cloud_disabled', 'status', 'main'}, self.ons)
        self.saved = {}
        self.config = SimpleNamespace(path=lambda *x:str(self.root.joinpath(*x)), HOME=str(self.root), PORT=5090,
              load_json=lambda path,default:json.loads(json.dumps(self.saved.get(path,default))),
              save_json=lambda path,value:self.saved.update({path:json.loads(json.dumps(value))}),
              settings=lambda:{'tmux_cols':80,'tmux_rows':24}, protected=lambda:set())
        self.providers = SimpleNamespace(PROVIDER_IDS=('claude','codex','gemini','ollama'),
                   executable=lambda p:'/fake/Ollama binary', discover=lambda p:[{'path':'/fake/Ollama binary'}],
                   environment_for=lambda b:{'PATH':'/fake/bin'})
        self.imports = {'providers':self.providers,'ollama':self.ollama}
        self.prefns = dict(config=self.config, SUPPORTED=self.providers.PROVIDER_IDS, os=os, _lock=threading.RLock(),
                           status=lambda:{'saved':True})
        self.onboarding = extract('onboarding.py', {'preferences','enabled','configure'}, self.prefns, self.imports)
        self.imports['onboarding'] = self.onboarding

    def enable(self):
        self.onboarding.configure({'enabled_providers':['ollama'], 'ollama_model':'example:local'})

    def test_opt_in_model_roundtrip_and_existing_choices_preserved(self):
        self.assertEqual(self.onboarding.preferences()['enabled_providers'], [])
        self.enable()
        self.assertTrue(self.onboarding.enabled('ollama'))
        self.assertEqual(self.onboarding.preferences()['ollama_model'], 'example:local')
        self.onboarding.configure({'enabled_providers':['claude']})
        self.assertFalse(self.onboarding.enabled('ollama'))
        self.assertEqual(self.onboarding.preferences()['ollama_model'], 'example:local')
        self.run.assert_not_called()

    def test_bad_models_do_not_overwrite_saved_choices(self):
        self.enable(); before=json.dumps(self.saved,sort_keys=True)
        for model in ('',None,'--help','x;echo BAD','x\ny','a b','model:cloud','model-cloud:latest'):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.onboarding.configure({'enabled_providers':['ollama'],'ollama_model':model})
            self.assertEqual(json.dumps(self.saved,sort_keys=True),before)

    def test_status_lists_only_explicit_model_with_loopback_host(self):
        self.assertEqual(self.ollama.status('/fake/Ollama binary','example:local')['state'],'ready')
        args,kw=self.run.call_args
        self.assertEqual(args[0],['/fake/Ollama binary','list'])
        self.assertEqual(kw['env']['OLLAMA_HOST'],'http://127.0.0.1:11434')
        self.assertLessEqual(kw['timeout'],4)
        self.assertNotIn('shell',kw)
        self.execve.assert_not_called()

    def test_unavailable_binary_model_backend_and_unknown_list_fail_closed(self):
        self.assertEqual(self.ollama.status(None,'example:local')['state'],'unavailable')
        self.assertEqual(self.ollama.status('/fake/ollama','')['state'],'unavailable')
        self.run.assert_not_called()
        self.assertEqual(self.ollama.status('/fake/ollama','missing:local')['state'],'unavailable')
        for value in (OSError('private error'),subprocess.TimeoutExpired('list',4),
                      SimpleNamespace(returncode=1,stdout='',stderr='private error'),
                      SimpleNamespace(returncode=0,stdout='unexpected private error',stderr='')):
            self.run.side_effect=value if isinstance(value,Exception) else None
            if not isinstance(value,Exception): self.run.return_value=value
            result=self.ollama.status('/fake/ollama','example:local')
            self.assertEqual(result['state'],'unavailable')
            self.assertNotIn('private error',str(result))
        self.execve.assert_not_called()

    def test_cloud_policy_must_be_explicit_and_checks_never_follow_redirects(self):
        for body in (b'{}', b'{"cloud":{}}', b'{"cloud":{"disabled":false}}', b'{"cloud":{"disabled":"true"}}', b'{"cloud":{"disabled":1}}', b'bad', b'x'*16385):
            self.response.read=lambda n,body=body:body
            self.assertFalse(self.ollama.cloud_disabled())
        self.response.status=302
        self.assertFalse(self.ollama.cloud_disabled())
        self.response.status=200
        self.response.read=lambda n:b'{"cloud":{"disabled":true}}'
        self.assertTrue(self.ollama.cloud_disabled())
        self.http.client.HTTPConnection.assert_called_with('127.0.0.1',11434,timeout=2)
        self.connection.request.assert_called_with('GET','/api/status')
        self.assertTrue(self.connection.close.called)
        self.connection.request.side_effect=OSError('offline')
        self.assertEqual(self.ollama.status('/fake/ollama','example:local')['state'],'unavailable')
        self.run.assert_not_called(); self.execve.assert_not_called()

    def test_native_chat_exec_and_error_are_explicit(self):
        with self.assertRaisesRegex(RuntimeError,'fake exec'):
            self.ollama.main(['--binary','/fake/Ollama binary','--model','example:local'])
        args=self.execve.call_args.args
        self.assertEqual(args[:2],('/fake/Ollama binary',['/fake/Ollama binary','run','example:local']))
        self.assertEqual(args[2]['OLLAMA_HOST'],'http://127.0.0.1:11434')
        self.execve.side_effect=OSError('hidden OS details')
        self.assertEqual(self.ollama.main(['--binary','/fake/ollama','--model','example:local']),2)
        self.assertNotIn('hidden OS',self.ons['sys'].stderr.getvalue())
        self.execve.reset_mock(); self.run.side_effect=OSError('offline')
        self.assertEqual(self.ollama.main(['--binary','/fake/ollama','--model','example:local']),2)
        self.execve.assert_not_called()

    def tmux_functions(self):
        self.enable(); self.tmux_calls=[]
        imports={**self.imports,'install':SimpleNamespace(REPO=str(ROOT),hooks_settings_path=blocked,gemini_launch_environment=blocked)}
        ns=dict(config=self.config,os=os,shlex=shlex,sys=SimpleNamespace(executable='/fake/python'),json=json,
                _run=lambda args,**kwargs:self.tmux_calls.append(args), exists=lambda name:True,
                time=SimpleNamespace(sleep=lambda n:None))
        return extract('tmuxctl.py',{'new_session','send_text'},ns,imports)

    def test_launch_quotes_arguments_skips_project_prompt_and_retains_fast_exit(self):
        tm=self.tmux_functions()
        tm.new_session('blade-example-1',str(self.root),'DO NOT SEND PROJECT INSTRUCTIONS',provider='ollama')
        script=(self.root/'run/blade-example-1.sh').read_text()
        command=next(line for line in script.splitlines() if line.startswith('/fake/python'))
        self.assertEqual(shlex.split(command),['/fake/python',str(ROOT/'blades/ollama.py'),'--binary','/fake/Ollama binary','--model','example:local'])
        self.assertNotIn('DO NOT SEND',script)
        self.assertNotIn('hooks',script)
        self.assertIn(['set-option','-t','blade-example-1','remain-on-exit','on'],self.tmux_calls)
        self.assertTrue((self.root/'run/blade-example-1.ready').is_file())
        self.assertEqual((self.root/'run/blade-example-1.sh').stat().st_mode & 0o777,0o700)
        with self.assertRaisesRegex(RuntimeError,'resume'):
            tm.new_session('blade-example-2',str(self.root),'',provider='ollama',resume_id='old')

    def test_text_input_remains_literal_fake_tmux_only(self):
        tm=self.tmux_functions(); tm.send_text('blade-example-1','text; $(not-a-command)')
        self.assertEqual(self.tmux_calls,[['send-keys','-t','blade-example-1','-l','--','text; $(not-a-command)'],
                                         ['send-keys','-t','blade-example-1','Enter']])

    def app_functions(self):
        self.enable(); self.rows={}; self.launch=mock.Mock()
        def create(name,slug,project,cwd,provider):
            self.rows[name]={'name':name,'slug':slug,'project':project,'cwd':cwd,'provider':provider,'state':'starting'}
        def set_state(name,state,**kw):self.rows[name].update(state=state,**kw)
        self.store=SimpleNamespace(create=create,set_state=set_state,get=lambda n:self.rows.get(n),live=lambda:list(self.rows.values()))
        ns=dict(projects=SimpleNamespace(by_slug=lambda s:{'slug':s,'name':'Synthetic','path':str(self.root),'exists':True}),
                providers=self.providers,abort=abort,_open_lock=threading.Lock(),os=os,
                server_config=SimpleNamespace(status=lambda:{'restart_required':False}),store=self.store,
                tmuxctl=SimpleNamespace(next_name=lambda s:'blade-example-1',new_session=self.launch),
                prompts=SimpleNamespace(render=blocked),_handoff_receipts={})
        app=extract('app.py',{'_open_blade'},ns,self.imports)
        return app

    def test_open_is_chat_only_unknown_and_unresumable(self):
        app=self.app_functions(); row=app._open_blade('example',provider='ollama')
        self.assertEqual(row['state'],'unknown'); self.assertEqual(row['provider'],'ollama')
        self.assertNotIn('session_id',row)
        self.assertEqual(self.launch.call_args.args[2],'')
        with self.assertRaises(HTTPError) as err: app._open_blade('example',provider='ollama',resume_id='anything')
        self.assertEqual(err.exception.code,409)

    def test_open_backend_failure_does_not_create_or_launch_session(self):
        app=self.app_functions(); self.run.side_effect=OSError('no backend')
        with self.assertRaises(HTTPError) as err: app._open_blade('example',provider='ollama')
        self.assertEqual(err.exception.code,409); self.assertEqual(self.rows,{})
        self.launch.assert_not_called()

    def test_launch_failure_preserves_honest_closed_record(self):
        app=self.app_functions(); self.launch.side_effect=RuntimeError('fake launch failure')
        with self.assertRaises(HTTPError) as err: app._open_blade('example',provider='ollama')
        self.assertEqual(err.exception.code,500)
        self.assertEqual(self.rows['blade-example-1']['state'],'closed')
        self.assertIn('launch failed',self.rows['blade-example-1']['note'])

    def test_unknown_completion_never_allows_winddown(self):
        row={'name':'blade-example-1','cwd':str(self.root),'provider':'ollama','state':'idle',
             'idle_since':1,'activity_source':'claude:Stop','transcript_path':'/fake/transcript'}
        ns={'json':json}; lc=extract('lifecycle.py',{'proven_idle'},ns)
        self.assertFalse(lc.proven_idle(row))
        actions=[]
        ns=dict(store=SimpleNamespace(blade_lock=lambda n:threading.RLock(),get=lambda n:row),lifecycle=lc,
                _preserve=lambda *args:actions.append(args) or 'preserved')
        wd=extract('winddown.py',{'_run_locked'},ns)
        self.assertEqual(wd._run_locked(row['name'],'save project',True,{}),'preserved')
        self.assertEqual(len(actions),1)

    def test_real_store_accepts_ollama_and_preserves_duplicate_history(self):
        tree=ast.parse((ROOT/'blades/store.py').read_text()); constants={}
        for node in tree.body:
            if isinstance(node,ast.Assign) and node.targets[0].id in ('SCHEMA','MIGRATIONS'):
                constants[node.targets[0].id]=ast.literal_eval(node.value)
        conn=sqlite3.connect(':memory:'); self.addCleanup(conn.close); conn.row_factory=sqlite3.Row
        conn.executescript(constants['SCHEMA'])
        for name,kind in constants['MIGRATIONS'].items():conn.execute(f'ALTER TABLE blades ADD COLUMN {name} {kind}')
        ns=dict(conn=lambda:conn,_lock=threading.RLock(),time=time)
        st=extract('store.py',{'create','get','_row'},ns)
        row=st.create('synthetic','synthetic','Synthetic',str(self.root),provider='ollama')
        self.assertEqual(row['provider'],'ollama')
        with self.assertRaises(sqlite3.IntegrityError):st.create('synthetic','other','Other',str(self.root),provider='ollama')
        self.assertEqual(st.get('synthetic')['project'],'Synthetic')

    def test_terminal_archive_is_bounded_and_capture_failure_preserves_session(self):
        row={'name':'synthetic','slug':'synthetic','cwd':str(self.root),'provider':'ollama','state':'unknown'}
        actions=[]
        def handoff(*args):
            p=self.root/'handoff.md';p.write_text('Synthetic retained output');return str(p)
        ns=dict(store=SimpleNamespace(blade_lock=lambda n:threading.RLock(),get=lambda n:dict(row),update=lambda n,**kw:row.update(kw)),
                config=self.config,Path=Path,os=os,time=time,uuid=uuid,json=json,shutil=shutil,
                tmuxctl=SimpleNamespace(capture_all=lambda n:'synthetic retained chat'),
                prompts=SimpleNamespace(write_handoff=handoff))
        lc=extract('lifecycle.py',{'snapshot','_write'},ns)
        path=Path(lc.snapshot('synthetic'))
        self.assertEqual((path/'terminal.txt').read_text(),'synthetic retained chat')
        metadata=json.loads((path/'metadata.json').read_text())
        self.assertIsNone(metadata['transcript']);self.assertTrue(metadata['recovery_limitations'])
        ns['tmuxctl'].capture_all=mock.Mock(side_effect=RuntimeError('fake capture failure'))
        killns=dict(lifecycle=lc,tmuxctl=SimpleNamespace(kill=lambda n:actions.append('kill')),
                    store=SimpleNamespace(set_state=blocked),log=SimpleNamespace(exception=lambda *a:None),abort=abort)
        api=extract('app.py',{'api_blade_kill'},killns)
        with self.assertRaises(HTTPError) as err:api.api_blade_kill('synthetic')
        self.assertEqual(err.exception.code,409);self.assertEqual(actions,[])
        self.assertTrue(path.is_dir())


if __name__=='__main__':
    unittest.main(verbosity=2)
