import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader import PACKAGE_ROOT


def repo_path(*dirs):
    directory = Path(PACKAGE_ROOT)
    for dir in dirs:
        directory = Path(directory, dir)
        if isinstance(dir, str) and "." in dir:
            return directory
        if not directory.exists():
            directory.mkdir()
    return directory


def data_subdir(*dirs):
    return repo_path("data", *dirs)


DT_STR = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d_%H%M%S")


def run_artifacts_subdir(*dirs):
    return data_subdir("runs", DT_STR, *dirs)
