import dataclasses
from datetime import datetime
from multiprocessing import Process
from pathlib import Path
from time import sleep
from typing import Set, Optional, Dict, Any, Callable


@dataclasses.dataclass
class _Process:
    proc: Process
    start_dt: datetime
    log_path: Optional[Path] = None


class ProcessManager:
    def __init__(self):
        self.procs: Dict[str, _Process] = {}

    def add_and_start(
        self,
        name: str,
        target: Callable,
        args: Optional[tuple] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        log_path: Optional[Path] = None,
    ) -> None:
        """Add and start a new process."""
        if name in self.procs:
            raise ValueError(f"A process with name '{name}' is already running.")

        proc = Process(target=target, args=args or (), kwargs=kwargs or {}, name=name)
        proc.start()
        self.procs[name] = _Process(proc, start_dt=datetime.now(), log_path=log_path)

    def remove_completed(self) -> Set[str]:
        """Remove completed processes and return their names."""
        completed_procs = set()
        for name, proc_obj in self.procs.items():
            proc = proc_obj.proc
            if not proc.is_alive():
                completed_procs.add(name)
                run_time = datetime.now() - proc_obj.start_dt
                logger_msg = f"{name} exited with code {proc.exitcode}. Total time: {run_time}."

                if proc_obj.log_path is not None:
                    logger_msg += f" Log: {proc_obj.log_path}"

                # if proc.exitcode == 0:
                #     logger.info(logger_msg)
                # else:
                #     logger.error(logger_msg)

        for name in completed_procs:
            self.procs[name].proc.terminate()
            self.procs[name].proc.kill()
            self.procs[name].proc.close()
            del self.procs[name]

        return completed_procs

    @property
    def num_running_processes(self) -> int:
        return len(self.get_running_processes())

    def get_running_processes(self) -> Set[str]:
        """Get names of all currently running processes."""
        return {name for name, proc_obj in self.procs.items() if proc_obj.proc.is_alive()}

    def terminate_process(self, name: str) -> bool:
        """Terminate a specific process."""
        if name not in self.procs:
            # logger.warning(f"Process '{name}' not found")
            return False

        proc = self.procs[name].proc
        if proc.is_alive():
            proc.terminate()
            proc.kill()
            # proc.close()
            # logger.info(f"Terminated process '{name}'")
            while len(self.remove_completed()) == 0:
                sleep(0.01)
            return True
        return False

    def terminate_all(self):
        all_names = set(self.procs.keys())
        for name in all_names:
            self.terminate_process(name)

    def block(self, sleep_secs: float | int = 0.5) -> Set[str]:
        all_completed = set()
        while len(self.procs) > 0:
            all_completed.update(self.remove_completed())
            sleep(sleep_secs)
        return all_completed

    def __len__(self) -> int:
        """Return number of processes"""
        return len(self.procs)

    def __contains__(self, name: str) -> bool:
        """Check if process name is tracked."""
        return name in self.procs
