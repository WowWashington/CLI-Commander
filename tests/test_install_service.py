"""Service migration cannot consume unrelated jobs or lose the original plist."""
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock

_IMPORT_HOME = tempfile.TemporaryDirectory(prefix='blades-install-import-')
with mock.patch.dict(os.environ, {'BLADES_HOME': _IMPORT_HOME.name}):
    from blades import config, install


class ServiceInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blades-service-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / 'Checkout with spaces'
        self.python = self.checkout / '.venv/bin/python'
        self.python.parent.mkdir(parents=True)
        self.python.write_text('#!/bin/sh\nexit 0\n')
        self.python.chmod(0o700)
        self.agents = self.root / 'Library/LaunchAgents'
        self.agents.mkdir(parents=True)
        self.state = self.root / '.cli_blades'
        self.state.mkdir()
        self.patches = [mock.patch.object(config, 'HOME', str(self.root)),
                        mock.patch.object(config, 'CONFIG_DIR', str(self.state)),
                        mock.patch.object(config, 'PORT', 5090),
                        mock.patch.object(install, 'REPO', str(self.checkout)),
                        mock.patch.object(install, 'LAUNCH_AGENTS', str(self.agents)),
                        mock.patch.object(install.sys, 'platform', 'darwin'),
                        mock.patch.dict(os.environ, {'HOME': str(self.root)}, clear=True),
                        mock.patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def plist(self, label, *, directory=None, command='serve', extra=None):
        data = {'Label': label, 'WorkingDirectory': str(directory or self.checkout),
                'ProgramArguments': [str(self.python), '-m', 'blades', command],
                'EnvironmentVariables': {'HOME': str(self.root), 'BLADES_HOME': str(self.state)}}
        data.update(extra or {})
        path = self.agents / (label + '.plist')
        with path.open('wb') as stream:
            plistlib.dump(data, stream)
        return path

    def test_unrelated_malformed_plists_do_not_interrupt_install_or_get_modified(self):
        malformed = self.agents / 'unrelated-malformed.plist'
        malformed.write_bytes(b'<?xml version="1.0"?><plist><broken></plist>')
        sequence = self.agents / 'unrelated-sequence.plist'
        with sequence.open('wb') as stream:
            plistlib.dump(['a', 'different', 'application'], stream)
        invalid_args = self.plist('unrelated-invalid-args', extra={'ProgramArguments': 42})
        originals = {path: path.read_bytes() for path in (malformed, sequence, invalid_args)}
        results = install.install_launchd()
        self.assertEqual([entry[1] for entry in results], [0, 0])
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        touched = {call.args[0][-1] for call in install.subprocess.run.call_args_list}
        self.assertFalse(touched.intersection(map(str, originals)))

    def test_only_matching_service_and_nightly_are_backed_up_then_migrated(self):
        old_service = self.plist('example.old-service')
        old_nightly = self.plist('example.old-nightly', command='nightly')
        other_checkout = self.plist('example.other-checkout', directory=self.root / 'Other checkout')
        other_command = self.plist('example.other-command', command='audit')
        other_module = self.plist('example.other-module', extra={'ProgramArguments': [str(self.python), '-m', 'other_app', 'serve']})
        preserved = {path: path.read_bytes() for path in (other_checkout, other_command, other_module)}
        migrated = {path: path.read_bytes() for path in (old_service, old_nightly)}

        def launchctl(args, **kwargs):
            target = Path(args[-1])
            if args[1] == 'bootout' and target in migrated:
                self.assertTrue(target.exists(), 'the original must remain until bootout succeeds')
                backups = list((self.state / 'backups/service-migration').glob(target.name + '.*'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), migrated[target])
            return subprocess.CompletedProcess(args, 0, '', '')

        install.subprocess.run.side_effect = launchctl
        results = install.install_launchd(nightly_hour=21, nightly_minute=15)
        self.assertEqual([entry[1] for entry in results], [0, 0])
        for path in migrated:
            self.assertFalse(path.exists())
            backup = next((self.state / 'backups/service-migration').glob(path.name + '.*'))
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        for path, original in preserved.items():
            self.assertEqual(path.read_bytes(), original)
        with (self.agents / (install.SERVICE_LABEL + '.plist')).open('rb') as stream:
            service = plistlib.load(stream)
        self.assertEqual(service['WorkingDirectory'], str(self.checkout))
        self.assertEqual(service['ProgramArguments'], [str(self.python), '-m', 'blades', 'serve'])
        with (self.agents / (install.NIGHTLY_LABEL + '.plist')).open('rb') as stream:
            nightly = plistlib.load(stream)
        self.assertEqual(nightly['StartCalendarInterval'], {'Hour': 21, 'Minute': 15})

    def test_failed_old_service_bootout_retains_original_and_aborts_before_new_launch(self):
        old = self.plist('example.old-service')
        original = old.read_bytes()
        install.subprocess.run.return_value = subprocess.CompletedProcess([], 5, '', 'cannot unload')
        with self.assertRaisesRegex(RuntimeError, 'previous service'):
            install.install_launchd()
        self.assertEqual(old.read_bytes(), original)
        self.assertFalse((self.agents / (install.SERVICE_LABEL + '.plist')).exists())
        backup = next((self.state / 'backups/service-migration').glob(old.name + '.*'))
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(install.subprocess.run.call_count, 1)
        self.assertEqual(install.subprocess.run.call_args.args[0][1], 'bootout')

    def test_already_unloaded_old_service_can_be_migrated(self):
        old = self.plist('example.old-service')
        def launchctl(args, **kwargs):
            return subprocess.CompletedProcess(args, 3 if args[1] == 'bootout' and args[-1] == str(old) else 0, '', '')
        install.subprocess.run.side_effect = launchctl
        self.assertEqual([result[1] for result in install.install_launchd()], [0, 0])
        self.assertFalse(old.exists())

    def test_same_checkout_different_data_directory_is_preserved(self):
        other = self.plist('example.separate-instance', extra={
            'EnvironmentVariables': {'HOME': str(self.root), 'BLADES_HOME': str(self.root / 'other-state')}})
        original = other.read_bytes()
        install.install_launchd()
        self.assertEqual(other.read_bytes(), original)
        self.assertNotIn(str(other), [call.args[0][-1] for call in install.subprocess.run.call_args_list])
        self.assertFalse((self.state / 'backups/service-migration').exists())

    def test_current_service_label_with_other_owner_aborts_before_mutating_anything(self):
        old = self.plist('example.owned-legacy')
        conflict = self.plist(install.SERVICE_LABEL, directory=self.root / 'Other checkout')
        config.save_json(config.path('server.json'), {'port': config.PORT, 'projects_root': str(self.root)})
        originals = {path: path.read_bytes() for path in (old, conflict, self.state / 'server.json')}
        with self.assertRaisesRegex(RuntimeError, 'another checkout or data directory'):
            install.install_launchd()
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        install.subprocess.run.assert_not_called()
        self.assertFalse((self.state / 'backups').exists())

    def test_legacy_default_state_location_can_be_identified_without_blades_home(self):
        legacy = self.plist('example.legacy-default-home', extra={'EnvironmentVariables': {'HOME': str(self.root)}})
        install.install_launchd()
        self.assertFalse(legacy.exists())
        self.assertEqual(len(list((self.state / 'backups/service-migration').glob(legacy.name + '.*'))), 1)

    def test_port_mismatch_with_live_sessions_aborts_before_service_changes(self):
        old = self.plist('example.old-service')
        config.save_json(config.path('server.json'), {'port': 5091})
        originals = {path: path.read_bytes() for path in (old, self.state / 'server.json')}
        with mock.patch('blades.tmuxctl.list_sessions', return_value=['blade-example-1']):
            with self.assertRaisesRegex(RuntimeError, 'ports differ'):
                install.install_launchd()
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        install.subprocess.run.assert_not_called()

    def test_effective_environment_port_is_persisted_without_freezing_new_plists(self):
        config.save_json(config.path('server.json'), {'port': 5091, 'projects_root': str(self.root)})
        with mock.patch.dict(os.environ, {'BLADES_PORT': '5090'}), \
                mock.patch('blades.tmuxctl.list_sessions', return_value=[]):
            install.install_launchd()
        self.assertEqual(config.server_settings(), {'port': 5090, 'projects_root': str(self.root)})
        with (self.agents / (install.SERVICE_LABEL + '.plist')).open('rb') as stream:
            service = plistlib.load(stream)
        self.assertNotIn('BLADES_PORT', service['EnvironmentVariables'])
        self.assertEqual(service['EnvironmentVariables']['BLADES_HOME'], str(self.state))


if __name__ == '__main__':
    unittest.main()
