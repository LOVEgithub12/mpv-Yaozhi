"""Bind the color-emoji layer stacked on the frozen native-refresh core."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CONFIG = HERE.parents[1]
NATIVE_REFRESH_LOCK = 'build/atmos/parity/native-refresh-lock.json'


def require(test, message):
    if not test:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(source, *args):
    result = subprocess.run(
        ['git', '-C', str(source), '-c', 'core.autocrlf=false', *[str(a) for a in args]],
        capture_output=True, timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    require(result.returncode == 0,
            'Command failed: ' + repr(args) + '\n' + result.stderr.decode('utf-8', 'replace')[:4000])
    return result.stdout.decode('utf-8', 'replace').strip()


def validate_lock(config=CONFIG):
    lock = json.loads((HERE / 'color-lock.json').read_bytes())
    require(lock['schema'] == 1, 'Review a new color-emoji lock schema')
    require(set(lock['patches']) == {
        'packages/libass-0001-font-read-and-resample-embedded-color-bitmaps.patch',
        'packages/libass-0002-render-draw-color-bitmap-glyphs-as-their-own-images.patch',
        'packages/mpv-9310-color-emoji-bgra.patch',
        'packages/mpv-9311-split-color-layer.patch'}, 'Unexpected color-emoji patch set')
    for relative, item in lock['patches'].items():
        origin = (config / item['source']).resolve()
        require(origin.is_file() and sha(origin) == item['sha256'],
                'Configured color patch differs from its lock: ' + relative)
    require(len(lock['mpv_commits']) == 2, 'Color-emoji is exactly two mpv commits')
    return lock


def verify(source, winbuild, variant, lock, *, binaries=(), compiled=False, config=CONFIG,
           refresh_lock=None):
    source, winbuild, config = (p.resolve() for p in (source, winbuild, config))
    packages = (winbuild / 'packages').resolve()
    for relative, item in lock['patches'].items():
        staged = packages / Path(relative).name
        require(staged.is_file() and sha(staged) == item['sha256'],
                'Staged color patch differs from its lock: ' + relative)
    require(not git(source, 'status', '--porcelain', '--untracked-files=all').strip(),
            'Color-emoji source must be completely clean')
    order = sorted(p.name for p in packages.glob('mpv-*.patch'))
    tail = order[-3:]
    require(tail == ['mpv-9309-native-refresh-video-priority.patch',
                     'mpv-9310-color-emoji-bgra.patch',
                     'mpv-9311-split-color-layer.patch'],
            'Color-emoji does not apply last: ' + repr(tail))
    # The frozen native-refresh lock describes the commit directly underneath.
    # It is read from the reviewed configuration commit, never from a copy that
    # the color-emoji branch could re-freeze.
    refresh_path = Path(refresh_lock).resolve() if refresh_lock else (config / NATIVE_REFRESH_LOCK)
    refresh = json.loads(refresh_path.read_bytes())
    require(git(source, 'rev-parse', 'HEAD~2^{tree}') ==
            refresh['variants'][variant]['source_tree'],
            'Layer below color-emoji is not the frozen native-refresh tree')
    commits = []
    # lock['mpv_commits'] is in application order, so the last one is HEAD.
    for offset, expected in zip(range(len(lock['mpv_commits']) - 1, -1, -1),
                                lock['mpv_commits']):
        commit = git(source, 'rev-parse', f'HEAD~{offset}')
        require(git(source, 'show', '-s', '--format=%s', commit) == expected['subject'],
                'Unexpected color-emoji commit subject: ' + commit)
        changed = sorted(git(source, 'diff', '--name-only',
                             f'{commit}^', commit).splitlines())
        require(changed == sorted(expected['changed_files']),
                'Color-emoji commit scope differs: ' + commit)
        commits.append(dict(commit=commit, subject=expected['subject'], changed_files=changed))
    files = sorted({f for c in lock['mpv_commits'] for f in c['changed_files']})
    require(files == sorted(git(source, 'diff', '--name-only', 'HEAD~2', 'HEAD').splitlines()),
            'Combined color-emoji scope differs')
    artifacts = []
    for binary in binaries:
        binary = Path(binary).resolve()
        require(binary.is_file() and binary.stat().st_size > 0, 'Missing compiled artifact')
        artifacts.append(dict(path=str(binary), name=binary.name,
                              sha256=sha(binary), size=binary.stat().st_size))
    require(not compiled or {a['name'] for a in artifacts} == {'mpv.exe', 'mpv.com'},
            'Compiled binding requires exactly mpv.exe and mpv.com')
    return dict(status='COLOR_EMOJI_LAYER_BOUND_TO_NATIVE_REFRESH_CORE', variant=variant,
                color_source_commit=git(source, 'rev-parse', 'HEAD'),
                color_source_tree=git(source, 'rev-parse', 'HEAD^{tree}'),
                native_refresh_tree=refresh['variants'][variant]['source_tree'],
                native_refresh_lock_sha256=sha(refresh_path),
                libass_base_commit=lock['libass_base_commit'],
                patches={Path(k).name: v['sha256'] for k, v in lock['patches'].items()},
                commits=commits, source_sha256={f: sha(source / f) for f in files},
                runtime_verified=False, performance_verified=False,
                bound_binary_artifacts=artifacts)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--winbuild', required=True, type=Path)
    p.add_argument('--variant', required=True, choices=('main', 'atmos'))
    p.add_argument('--compiled', action='store_true')
    p.add_argument('--binary', type=Path, action='append', default=[])
    p.add_argument('--refresh-lock', type=Path,
                   help='native-refresh-lock.json of the reviewed configuration commit')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    result = dict(status='COLOR_EMOJI_GATE_FAILED', runtime_verified=False)
    try:
        result = verify(a.source, a.winbuild, a.variant, validate_lock(),
                        compiled=a.compiled, binaries=a.binary, refresh_lock=a.refresh_lock)
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
