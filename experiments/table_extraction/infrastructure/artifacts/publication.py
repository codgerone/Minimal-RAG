"""Publish completed directories with rollback on replacement errors."""
from contextlib import contextmanager
from pathlib import Path
import shutil
from uuid import uuid4

def publish_directories(pairs: list[tuple[Path, Path]]) -> None:
    """Keep old directories until all replacements succeed; rollback exceptions."""
    backups: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path]] = []
    try:
        for staging, destination in pairs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = destination.with_name(f'.{destination.name}.backup-{uuid4().hex}')
                destination.rename(backup)
                backups.append((backup, destination))
            staging.rename(destination)
            published.append((destination, staging))
    except BaseException:
        for destination, staging in reversed(published):
            destination.rename(staging)
        for backup, destination in reversed(backups):
            backup.rename(destination)
        raise
    else:
        for backup, _ in backups:
            shutil.rmtree(backup)

class FileArtifactPublisher:
    @contextmanager
    def stage(self, destination: Path):
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(f'.{destination.name}.staging-{uuid4().hex}')
        staging.mkdir()
        try:
            yield staging
            publish_directories([(staging, destination)])
        finally:
            if staging.exists():
                shutil.rmtree(staging)
