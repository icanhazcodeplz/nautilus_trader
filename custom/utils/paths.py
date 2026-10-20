import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader import PACKAGE_ROOT, ENV


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


def live_run_artifacts_dir(*dirs, paper=True):
    if paper:
        return data_subdir("paper_runs", *dirs)
    return data_subdir("runs", *dirs)


RUN_START_TIME_AS_STR = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d_%H%M%S")

def current_live_run_artifacts_dir(*dirs):
    return live_run_artifacts_dir(*dirs, paper=ENV.PAPER)



