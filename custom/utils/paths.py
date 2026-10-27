import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader import PACKAGE_ROOT, ENV
from nautilus_trader.model.identifiers import TraderId


def repo_path(*dirs):
    directory = Path(PACKAGE_ROOT)
    for dir in dirs:
        directory = Path(directory, dir)
        if isinstance(dir, str) and "." in dir:
            return directory
        directory.mkdir(exist_ok=True)
    return directory


def data_subdir(*dirs):
    return repo_path("data", *dirs)


def live_run_artifacts_dir(*dirs, paper=True):
    if paper:
        return data_subdir("paper_runs", *dirs)
    return data_subdir("runs", *dirs)


RUN_START_TIME_AS_STR = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d_%H%M%S")
RUN_INFO_FILE = "run_info.json"  # Written to a live run's folder at startup; see LiveArtifactsIO


def run_trader_id(run_start: str) -> TraderId:
    """
    Return the trader ID of the live run that started at `run_start` (formatted like
    RUN_START_TIME_AS_STR).

    The tag after "T-" has to stay `run_start`: nautilus puts it in every client order ID, which is
    how parsing.client_id_is_real tells this run's orders apart from others on the account.
    """
    return TraderId(f"T-{run_start}")


def current_live_run_artifacts_dir(*dirs):
    return live_run_artifacts_dir(*(RUN_START_TIME_AS_STR, *dirs), paper=ENV.PAPER)
