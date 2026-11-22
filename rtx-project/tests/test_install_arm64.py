# SPDX-License-Identifier: GPL-2.0
"""Installation guards tested with synthetic files; never runs sudo or make."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('install_arm64', SCRIPTS / 'install_arm64.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallGuards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.build = self.root / 'build'
        self.source.mkdir()
        self.run = self.build / 'validation-test'
        self.run.mkdir(parents=True)
        self.release = '5.4.214-rtx-v0'
        self.modules = ['drivers/block/virtio_blk.ko']
        self.config = ('CONFIG_ARM64=y\nCONFIG_NET_NS=y\nCONFIG_VETH=y\n'
                       'CONFIG_NET_SCH_NETEM=y\nCONFIG_LOCALVERSION="-rtx-v0"\n'
                       '# CONFIG_MODULE_COMPRESS is not set\n')
        for name, text in {
                '.config': self.config, 'final.config': self.config,
                'prepare-status.txt': 'exit_code=0\nstage=prepared\n',
                'include/config/kernel.release': self.release + '\n',
                'vmlinux': 'synthetic ELF', 'System.map': 'synthetic map',
                'arch/arm64/boot/Image': 'synthetic image',
                'arch/arm64/boot/dts/test.dtb': 'synthetic dtb',
                'modules.order': self.modules[0] + '\n', self.modules[0]: 'synthetic module',
                'modules.builtin': 'builtin module',
                'modules.builtin.modinfo': 'builtin metadata'}.items():
            self.put(self.build / name, text)
        self.state = dict(status='PASS', stage='built', source=str(self.source),
                          output=str(self.build), source_sha=installer.PINS['v0'],
                          kernelrelease=self.release,
                          config_sha256=installer.sha256(self.build / '.config'),
                          **installer.artifacts(self.build))
        self.save_state()
        self.put(self.run / 'build.exitcode', '0\n')

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def save_state(self):
        self.put(self.run / 'status.json', json.dumps(self.state))

    def verify(self):
        # Source cleanliness/pin check is exercised separately on real worktrees.
        with mock.patch.object(installer, 'verify_source') as source_check:
            result = installer.verify_build(self.run, 'v0')
        source_check.assert_called_once_with(self.source, 'v0')
        return result

    def test_complete_inputs_preserve_installation_manifest(self):
        state, source, build, modules, hashes = self.verify()
        self.assertEqual((source, build, modules), (self.source, self.build, self.modules))
        self.assertIn('arch/arm64/boot/dts/test.dtb', hashes)
        self.assertEqual(hashes[self.modules[0]], installer.sha256(self.build / self.modules[0]))

    def test_rejects_unfinished_or_wrong_build_record(self):
        for key, value in (('status', 'RUNNING'), ('stage', 'verify'),
                           ('source_sha', installer.PINS['v1']),
                           ('kernelrelease', '5.4.214-rtx-c23-rb')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                installer.validate_record(dict(self.state, **{key: value}), '0', 'v0')
        for code in ('1', '', '0\n1'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                installer.validate_record(self.state, code, 'v0')

    def test_rejects_primary_artifact_changes(self):
        for name in ('.config', 'final.config', 'vmlinux', 'System.map',
                     'arch/arm64/boot/Image', 'modules.order', 'include/config/kernel.release'):
            path = self.build / name
            previous = path.read_bytes()
            path.write_bytes(previous + b'changed')
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.verify()
            finally:
                path.write_bytes(previous)

    def test_rejects_missing_module_or_builtin_metadata(self):
        for name in (self.modules[0], 'modules.builtin', 'modules.builtin.modinfo'):
            path = self.build / name
            previous = path.read_bytes()
            path.unlink()
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.verify()
            finally:
                path.write_bytes(previous)

    def test_rejects_duplicate_modules_even_if_record_matches(self):
        self.put(self.build / 'modules.order', (self.modules[0] + '\n') * 2)
        self.state.update(installer.artifacts(self.build))
        self.save_state()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.verify()

    def test_rejects_artifacts_outside_build(self):
        outside = self.root / 'outside.ko'
        outside.write_text('not a build artifact')
        link = self.build / 'linked.ko'
        link.symlink_to(outside)
        for name in ('../outside.ko', str(outside), 'linked.ko'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                installer.contained_file(self.build, name)

    def test_rejects_existing_or_partial_installation(self):
        boot, modules = self.root / 'boot', self.root / 'installed-modules'
        boot.mkdir()
        modules.mkdir()
        installer.ensure_uninstalled(self.release, boot, modules)
        for name in ('vmlinuz-', 'config-', 'System.map-', 'initrd.img-'):
            path = boot / (name + self.release)
            path.symlink_to(self.root / 'missing-file')
            with self.subTest(name=name), self.assertRaises(ValueError):
                installer.ensure_uninstalled(self.release, boot, modules)
            path.unlink()
        (modules / self.release).mkdir()
        with self.assertRaises(ValueError):
            installer.ensure_uninstalled(self.release, boot, modules)

    def test_rejects_wrong_or_incomplete_module_versions(self):
        valid = self.release + ' SMP mod_unload aarch64\n'
        installer.verify_vermagic(valid * 2, self.release, 2)
        for text in (valid, valid * 3, valid + '\n', valid + valid.replace('v0', 'v1')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                installer.verify_vermagic(text, self.release, 2)

    def test_rejects_missing_root_boot_requirements(self):
        entries = ['lib/modules/' + self.release + '/kernel/drivers/block/virtio_blk.ko',
                   'scripts/local-top/lvm2', 'sbin/lvm',
                   'lib/modules/' + self.release + '/modules.dep']
        installer.verify_initramfs_listing('\n'.join(entries), self.release)
        installer.verify_initramfs_listing('\n'.join(
            'usr/' + line if line.startswith(('lib/', 'sbin/')) else line for line in entries),
            self.release)
        for missing in entries:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                installer.verify_initramfs_listing('\n'.join(line for line in entries if line != missing),
                                                   self.release)
        with self.assertRaises(ValueError):
            installer.verify_initramfs_listing('\n'.join(entries).replace('v0', 'v1'), self.release)

    def test_rejects_incomplete_installed_modules(self):
        root = self.root / 'installed'
        self.put(root / 'kernel' / self.modules[0], 'installed module')
        metadata = ('modules.order', 'modules.builtin', 'modules.builtin.modinfo',
                    'modules.dep', 'modules.dep.bin')
        for name in metadata:
            self.put(root / name, 'kernel/' + self.modules[0] + '\n' if name == 'modules.order' else 'data')
        installer.verify_installed_modules(root, self.modules)
        for name in ('kernel/' + self.modules[0],) + metadata:
            path = root / name
            previous = path.read_bytes()
            path.write_bytes(b'')
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    installer.verify_installed_modules(root, self.modules)
            finally:
                path.write_bytes(previous)
        (root / 'modules.order').write_text('kernel/other.ko\n')
        with self.assertRaises(ValueError):
            installer.verify_installed_modules(root, self.modules)

    def test_bad_preflight_never_authenticates_or_installs(self):
        self.state['status'] = 'RUNNING'
        self.save_state()
        output = self.root / 'failed-preflight'
        output.mkdir()
        with mock.patch.object(installer.platform, 'machine', return_value='aarch64'), \
                mock.patch.object(installer.subprocess, 'call') as call, \
                mock.patch.object(installer.subprocess, 'run') as run:
            rc = installer.perform(argparse.Namespace(variant='v0', build_run=self.run, install=True), output)
        call.assert_not_called()
        run.assert_not_called()
        result = json.loads((output / 'status.json').read_text())
        self.assertEqual((rc, result['status'], result['installation']), (1, 'FAIL', 'NOT RUN'))

    def test_failed_install_command_stops_and_preserves_log(self):
        output = self.root / 'failed-install'
        output.mkdir()
        result = self.verify()

        def fake_command(command, stdout, stderr):
            if command[0] == 'readelf':
                stdout.write('Machine: AArch64\n')
            elif command[0] == 'modinfo':
                stdout.write(self.release + ' SMP\n')
            else:
                self.assertEqual(command[-1], 'modules_install')
                stdout.write('synthetic install failure\n')
                return subprocess.CompletedProcess(command, 9)
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(installer.platform, 'machine', return_value='aarch64'), \
                mock.patch.object(installer, 'verify_build', return_value=result), \
                mock.patch.object(installer, 'ensure_uninstalled'), \
                mock.patch.object(installer.shutil, 'disk_usage', return_value=argparse.Namespace(free=2**40)), \
                mock.patch.object(installer.subprocess, 'call', return_value=0), \
                mock.patch.object(installer.subprocess, 'run', side_effect=fake_command):
            rc = installer.perform(argparse.Namespace(variant='v0', build_run=self.run, install=True), output)
        result = json.loads((output / 'status.json').read_text())
        self.assertEqual((rc, result['status'], result['installation']), (1, 'FAIL', 'FAIL'))
        self.assertEqual((output / 'modules-install.exitcode').read_text(), '9\n')
        self.assertIn('synthetic install failure', (output / 'modules-install.log').read_text())
        self.assertFalse((output / 'kernel-install.command').exists())
        self.assertIn('modules-install.log', (output / 'SHA256SUMS').read_text())


if __name__ == '__main__':
    unittest.main()
