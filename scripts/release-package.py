#!/usr/bin/env python3
"""Build the release packages for the TypeScript profiler.

For one stable release tag this writes, into a new or empty directory:

  rc-profiler-typescript_<v>.tar.gz                   the app without Node.js, for Homebrew
  rc-profiler-typescript_<v>-1_<amd64|arm64>.deb      the app with its own Node.js, for apt
  rc-profiler-typescript-<v>-1.<x86_64|aarch64>.rpm   the same, for yum and dnf
  rc-profiler-typescript.rb                           the Homebrew formula for this release
  SHA256SUMS                                          the checksum of every file above

The deb and rpm carry the Node.js release pinned in distribution/node.lock.json, so installing
them needs nothing but the system package manager. Homebrew depends on its own node formula.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
NAME = "rc-profiler-typescript"
APP_INPUTS = ("package.json", "package-lock.json", "tsconfig.json", "tsconfig.build.json")
APP_DIRECTORIES = ("src", "schemas")
WRAPPER = f"""#!/bin/sh
# Runs the TypeScript profiler with the Node.js runtime that ships in this package.
exec /usr/lib/{NAME}/node/bin/node /usr/lib/{NAME}/app/dist/src/main.js "$@"
"""


def run(args, *, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, destination, expected_sha256):
    """Download url to destination and refuse it unless it matches the pinned checksum."""
    with urllib.request.urlopen(url, timeout=120) as response, open(destination, "wb") as out:
        shutil.copyfileobj(response, out)
    actual = sha256_file(destination)
    if actual != expected_sha256:
        Path(destination).unlink()
        raise ValueError(f"{url}: checksum {actual} differs from the pinned {expected_sha256}")


def node_runtime(lock, goarch, cache):
    """Return the paths of the pinned Node.js binary and license for one architecture."""
    platform = lock["platforms"][goarch]
    archive = cache / platform["file"]
    if not archive.exists():
        fetch(f"{lock['baseUrl']}/{platform['file']}", archive, platform["sha256"])
    elif sha256_file(archive) != platform["sha256"]:
        raise ValueError(f"{archive}: cached Node.js archive differs from the pinned checksum")
    top = platform["file"].removesuffix(".tar.xz")
    out = cache / goarch
    out.mkdir(exist_ok=True)
    with tarfile.open(archive, "r:xz") as tar:
        for member, target, mode in ((f"{top}/bin/node", out / "node", 0o755), (f"{top}/LICENSE", out / "LICENSE", 0o644)):
            source = tar.extractfile(tar.getmember(member))
            if source is None:
                raise ValueError(f"{archive}: {member} is not a regular file")
            target.write_bytes(source.read())
            target.chmod(mode)
    return out / "node", out / "LICENSE"


def build_app(repository, work, version):
    """Build the profiler in a copy of the repository and return the app tree without dev tools."""
    source = work / "source"
    source.mkdir()
    for name in APP_INPUTS:
        shutil.copy2(repository / name, source / name)
    for name in APP_DIRECTORIES:
        shutil.copytree(repository / name, source / name)
    package = json.loads((source / "package.json").read_text())
    package["version"] = version
    (source / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=source)
    run(["npm", "run", "build"], cwd=source)
    run(["npm", "prune", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=source)
    app = work / "app"
    app.mkdir()
    shutil.copy2(source / "package.json", app / "package.json")
    shutil.copytree(source / "dist/src", app / "dist/src")
    shutil.copytree(source / "schemas", app / "schemas")
    shutil.copytree(source / "node_modules", app / "node_modules", symlinks=True)
    shutil.rmtree(app / "node_modules/.bin", ignore_errors=True)
    return app


def normalize(root, epoch):
    """Give every file and directory the same modification time, so equal input gives equal packages."""
    for path in [root, *root.rglob("*")]:
        os.utime(path, (epoch, epoch), follow_symlinks=False)


def stage_package_root(app, node, node_license, project_license, root, epoch):
    usr = root / "usr"
    (usr / "bin").mkdir(parents=True)
    (usr / "bin" / NAME).write_text(WRAPPER)
    (usr / "bin" / NAME).chmod(0o755)
    library = usr / "lib" / NAME
    shutil.copytree(app, library / "app", symlinks=True)
    (library / "node/bin").mkdir(parents=True)
    shutil.copy2(node, library / "node/bin/node")
    shutil.copy2(node_license, library / "node/LICENSE")
    documentation = usr / "share/doc" / NAME
    documentation.mkdir(parents=True)
    shutil.copy2(project_license, documentation / "copyright")
    normalize(root, epoch)


def make_archive(root, output, epoch):
    """Write a reproducible tar.gz: sorted entries, fixed times, no owner names."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(root.rglob("*")):
                info = archive.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                if info.isreg():
                    with open(path, "rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
    Path(output).write_bytes(buffer.getvalue())


def render_formula(template, version, archive_sha256):
    rendered = template.replace("{{VERSION}}", version).replace("{{SHA256}}", archive_sha256)
    if "{{" in rendered:
        raise ValueError("Homebrew formula template has unresolved release fields")
    return rendered


def source_date_epoch(repository):
    value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is not None:
        return int(value)
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=repository, check=True, capture_output=True, text=True)
        return int(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="stable release tag, such as v0.3.0")
    parser.add_argument("--output", required=True, type=Path, help="new or empty directory for the release files")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    match = TAG.fullmatch(args.version)
    if not match:
        raise ValueError("release requires a stable vMAJOR.MINOR.PATCH tag")
    version = args.version[1:]
    repository = args.repository.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("release output directory must be new or empty")
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve()
    epoch = source_date_epoch(repository)
    lock = json.loads((repository / "distribution/node.lock.json").read_text())
    nfpm_config = (repository / "distribution/nfpm.yaml").resolve()

    with tempfile.TemporaryDirectory(prefix="rc-profiler-typescript-release-") as temporary:
        work = Path(temporary)
        app = build_app(repository, work, version)
        project_license = repository / "LICENSE"

        archive_root = work / "archive"
        shutil.copytree(app, archive_root / "app", symlinks=True)
        shutil.copy2(project_license, archive_root / "LICENSE")
        normalize(archive_root, epoch)
        archive = output / f"{NAME}_{version}.tar.gz"
        make_archive(archive_root, archive, epoch)

        cache = work / "node"
        cache.mkdir()
        for goarch in sorted(lock["platforms"]):
            node, node_license = node_runtime(lock, goarch, cache)
            package_root = work / goarch / "package-root"
            stage_package_root(app, node, node_license, project_license, package_root, epoch)
            env = dict(os.environ, GOARCH=goarch, PACKAGE_VERSION=version, SOURCE_DATE_EPOCH=str(epoch))
            for packager in ("deb", "rpm"):
                run(["nfpm", "package", "--config", str(nfpm_config), "--packager", packager, "--target", str(output)],
                    cwd=package_root, env=env)

    template = (repository / "distribution/homebrew" / f"{NAME}.rb").read_text()
    (output / f"{NAME}.rb").write_text(render_formula(template, version, sha256_file(archive)))
    manifest = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text("".join(f"{sha256_file(path)}  {path.name}\n" for path in manifest))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"release-package: {error}")
