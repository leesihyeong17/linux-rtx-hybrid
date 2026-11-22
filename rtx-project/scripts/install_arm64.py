#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Validate a completed V0/V1 build; --install installs it without rebooting.

The module, initramfs and installed-artifact checks reuse the C23 installation
procedure exercised on this ARM64 UTM VM. The root filesystem uses LVM and
virtio_blk. This is not an installer for arbitrary machines or source pins.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from build_arm64 import FLAGS, PINS, artifacts, sha256, verify_config, verify_source, write_status

PROJECT = Path(__file__).resolve().parents[1]
ENV_INPUTS = ('MAKEFLAGS', 'MFLAGS', 'KBUILD_OUTPUT', 'KBUILD_SRC', 'KCONFIG_ALLCONFIG',
              'KBUILD_EXTMOD', 'KERNELRELEASE', 'INSTALL_PATH', 'INSTALL_MOD_PATH', 'MODLIB') + FLAGS


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def validate_record(state, exit_code, variant):
    if (state.get('status') != 'PASS' or state.get('stage') != 'built' or
            exit_code.strip() != '0' or state.get('source_sha') != PINS[variant] or
            state.get('kernelrelease') != '5.4.214-rtx-' + variant):
        raise ValueError('Require a completed build with the selected source pin and release')


def contained_file(root, name):
    path = Path(name)
    if path.is_absolute() or root.resolve() not in (root / path).resolve().parents:
        raise ValueError('Artifact path escapes its root: ' + name)
    return root / path


def ensure_uninstalled(release, boot=Path('/boot'), module_base=Path('/lib/modules')):
    paths = [module_base / release]
    paths += [boot / (prefix + release) for prefix in ('vmlinuz-', 'config-', 'System.map-', 'initrd.img-')]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError('Release already has installed files; inspect the previous installation before retrying')


def verify_vermagic(text, release, expected_count):
    lines = text.splitlines()
    if len(lines) != expected_count or any(
            not line.split() or line.split()[0] != release for line in lines):
        raise ValueError('Module vermagic does not match the selected release')


def verify_installed_modules(module_root, modules):
    for name in modules:
        path = contained_file(module_root / 'kernel', name)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing or empty installed module: ' + name)
    metadata = ('modules.order', 'modules.builtin', 'modules.builtin.modinfo', 'modules.dep', 'modules.dep.bin')
    for name in metadata:
        path = module_root / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing or empty module metadata: ' + name)
    if (module_root / 'modules.order').read_text().splitlines() != ['kernel/' + name for name in modules]:
        raise ValueError('Installed modules.order mismatch')
    return {name: sha256(module_root / name) for name in metadata}


def verify_initramfs_listing(listing, release):
    entries = {line.lstrip('./') for line in listing.splitlines()}
    module = 'lib/modules/' + release + '/kernel/drivers/block/virtio_blk.ko'
    requirements = (
        ({module, 'usr/' + module}, 'virtio_blk for the installed release'),
        ({'scripts/local-top/lvm2'}, 'LVM activation script'),
        ({'sbin/lvm', 'usr/sbin/lvm'}, 'LVM executable'),
        ({'lib/modules/' + release + '/modules.dep',
          'usr/lib/modules/' + release + '/modules.dep'}, 'module dependencies'),
    )
    for alternatives, description in requirements:
        if not entries.intersection(alternatives):
            raise ValueError('Initramfs missing ' + description)


def verify_build(build_run, variant):
    state = json.loads((build_run / 'status.json').read_text())
    validate_record(state, (build_run / 'build.exitcode').read_text(), variant)
    source, build = Path(state['source']).resolve(strict=True), Path(state['output']).resolve(strict=True)
    if build_run.parent != build or source == build or source in build.parents or build in source.parents:
        raise ValueError('Build evidence must belong to a separate source/output pair')
    verify_source(source, variant)
    if verify_config(build, variant) != state['config_sha256']:
        raise ValueError('Build config changed')
    actual = artifacts(build)
    if any(actual[key] != state.get(key) for key in ('hashes', 'module_count', 'dtb_count')):
        raise ValueError('Build artifacts differ from completed build evidence')
    if (build / 'include/config/kernel.release').read_text().strip() != state['kernelrelease']:
        raise ValueError('Generated kernel release changed')
    if '# CONFIG_MODULE_COMPRESS is not set' not in (build / '.config').read_text().splitlines():
        raise ValueError('This installation verifier requires uncompressed modules')
    modules = (build / 'modules.order').read_text().splitlines()
    if len(set(modules)) != len(modules):
        raise ValueError('Duplicate modules.order entry')
    extra = set(modules) | {'modules.builtin', 'modules.builtin.modinfo'}
    extra.update(str(path.relative_to(build)) for path in (build / 'arch/arm64/boot/dts').rglob('*.dtb'))
    hashes = {}
    for name in sorted(extra):
        path = contained_file(build, name)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing or empty additional artifact: ' + name)
        hashes[name] = sha256(path)
    return state, source, build, modules, hashes


def perform(args, run):
    summary = {'status': 'RUNNING', 'stage': 'preflight', 'variant': args.variant,
               'started_utc': stamp(), 'boot': 'NOT RUN', 'runtime_tests': 'NOT RUN',
               'installation': 'NOT RUN',
               'install_requested': args.install, 'build_run': str(args.build_run)}
    write_status(run, summary)

    def logged(name, command):
        (run / (name + '.command')).write_text(shlex.join(command) + '\n')
        with (run / (name + '.log')).open('w') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
        (run / (name + '.exitcode')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(name + ' failed; inspect ' + str(run / (name + '.log')))
        return (run / (name + '.log')).read_text()

    try:
        if platform.machine() != 'aarch64':
            raise ValueError('Native ARM64 VM required')
        for key in FLAGS:
            if os.environ.get(key):
                raise ValueError('Unset ' + key)
        state, source, build, modules, additional = verify_build(args.build_run, args.variant)
        release = state['kernelrelease']
        ensure_uninstalled(release)
        summary.update(source_sha=state['source_sha'], kernelrelease=release,
                       config_sha256=state['config_sha256'], image_sha256=state['hashes']['arch/arm64/boot/Image'],
                       module_count=len(modules), dtb_count=state['dtb_count'])
        (run / 'build-status.json').write_text(json.dumps(state, indent=2) + '\n')
        (run / 'additional-artifacts.json').write_text(json.dumps(additional, indent=2) + '\n')
        # Old build evidence fingerprints primary files, not every module/DTB.
        # These additional hashes describe the current verified installation input.
        summary['additional_artifacts_sha256'] = sha256(run / 'additional-artifacts.json')
        elf = logged('vmlinux-elf', ['readelf', '-h', str(build / 'vmlinux')])
        if 'AArch64' not in elf:
            raise ValueError('vmlinux is not AArch64')
        for start in range(0, len(modules), 128):
            batch = modules[start:start + 128]
            text = logged('input-vermagic-' + str(start),
                          ['modinfo', '-F', 'vermagic'] + [str(build / name) for name in batch])
            verify_vermagic(text, release, len(batch))
        root_free, boot_free = shutil.disk_usage('/').free, shutil.disk_usage('/boot').free
        module_bytes = sum((build / name).stat().st_size for name in modules)
        image_bytes = (build / 'arch/arm64/boot/Image').stat().st_size
        summary['space'] = {'root_free': root_free, 'boot_free': boot_free,
                            'module_bytes_unstripped': module_bytes, 'image_bytes': image_bytes}
        # Conservative allowance; installed stripped modules are normally smaller.
        if root_free < module_bytes + 512 * 1024**2 or boot_free < image_bytes + 256 * 1024**2:
            raise ValueError('Insufficient space allowance; preserve evidence and plan storage first')
        summary.update(status='PASS', stage='preflight', installation='NOT RUN')
        write_status(run, summary)
        if not args.install:
            return 0

        # The shared build lock is held by main throughout validation and install.
        summary.update(status='RUNNING', stage='authenticate')
        write_status(run, summary)
        if subprocess.call(['sudo', '-v']):
            raise RuntimeError('sudo authentication failed; use your VM terminal')
        ensure_uninstalled(release)
        command = ['sudo', '-n', 'env']
        for key in ENV_INPUTS:
            command += ['-u', key]
        command += ['make', '--no-print-directory', '-C', str(source), 'O=' + str(build),
                    'ARCH=arm64', 'LOCALVERSION=', 'CROSS_COMPILE=', 'KCONFIG_CONFIG=.config',
                    'CC=gcc', 'HOSTCC=gcc', 'HOSTCXX=g++', 'LD=ld', 'HOSTLD=ld']
        summary.update(stage='install', installation='RUNNING')
        write_status(run, summary)
        logged('modules-install', command + ['INSTALL_MOD_STRIP=1', 'modules_install'])
        module_root = Path('/lib/modules') / release
        summary['module_metadata_sha256'] = verify_installed_modules(module_root, modules)
        for start in range(0, len(modules), 128):
            batch = modules[start:start + 128]
            text = logged('installed-vermagic-' + str(start),
                          ['modinfo', '-F', 'vermagic'] + [str(module_root / 'kernel' / name) for name in batch])
            verify_vermagic(text, release, len(batch))
        logged('kernel-install', command + ['install'])
        initrd = Path('/boot/initrd.img-' + release)
        if not initrd.is_file():
            logged('initramfs', ['sudo', '-n', 'update-initramfs', '-c', '-k', release])
        logged('grub', ['sudo', '-n', 'update-grub'])
        installed = {}
        for name, checksum in (('vmlinuz-' + release, state['hashes']['arch/arm64/boot/Image']),
                               ('config-' + release, state['config_sha256']),
                               ('System.map-' + release, state['hashes']['System.map'])):
            installed[name] = sha256(Path('/boot') / name)
            if installed[name] != checksum:
                raise ValueError('Installed hash mismatch: ' + name)
        if not initrd.is_file() or not initrd.stat().st_size:
            raise ValueError('Missing or empty initramfs')
        verify_initramfs_listing(logged('initramfs-list', ['lsinitramfs', str(initrd)]), release)
        if release not in Path('/boot/grub/grub.cfg').read_text():
            raise ValueError('Missing GRUB entry')
        summary.update(status='PASS', stage='installed', installation='PASS',
                       installed_sha256=installed, initramfs_sha256=sha256(initrd))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            subprocess.SubprocessError, KeyboardInterrupt) as exc:
        summary.update(status='FAIL', error=str(exc) or type(exc).__name__)
        if summary.get('installation') == 'RUNNING':
            summary['installation'] = 'FAIL'
        print(str(exc), file=sys.stderr)
    finally:
        summary['finished_utc'] = stamp()
        write_status(run, summary)
        files = sorted(p for p in run.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
        (run / 'SHA256SUMS').write_text(''.join(sha256(p) + '  ' + p.name + '\n' for p in files))
    return 0 if summary['status'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=PINS, required=True)
    parser.add_argument('--build-run', type=Path, required=True, help='completed validation-* directory')
    parser.add_argument('--install', action='store_true', help='install after checks; otherwise read-only preflight')
    args = parser.parse_args()
    args.build_run = args.build_run.resolve()
    root = PROJECT / '.work'
    root.mkdir(exist_ok=True)
    with (root / 'arm64-build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, 'Another ARM64 build/installation holds the lock\n')
        run = Path(tempfile.mkdtemp(prefix='d-install-' + args.variant + '-', dir=str(root)))
        print(run, flush=True)
        return perform(args, run)


if __name__ == '__main__':
    sys.exit(main())
