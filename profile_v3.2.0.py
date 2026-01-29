#!/usr/bin/env python3

import time
import datetime
from contextlib import contextmanager


class Profile:
    def __init__(self, file_path="profile.txt", enabled=True, verbose=False):
        self.enabled = enabled
        self.verbose = verbose
        if self.enabled:
            self.file_path = file_path
            self.tasks = {}
            self.tstart = self.current_ms()
            self._write(f"Profiler started: {datetime.datetime.now()}")

    def start(self, task):
        """Start timing a task."""
        if not self.enabled:
            return

        task_data = self.tasks.setdefault(task, {
            "start": 0,
            "count": 0,
            "cumulative": 0,
            "min": float("inf"),
            "max": 0
        })

        task_data["start"] = self.current_ms()
        task_data["count"] += 1

        if self.verbose:
            self._write(f'Starting task "{task}"')

    def stop(self, task):
        """Stop timing a task."""
        if not self.enabled or task not in self.tasks or self.tasks[task]["start"] == 0:
            return

        tend = self.current_ms()
        elapsed = tend - self.tasks[task]["start"]
        task_data = self.tasks[task]

        task_data["cumulative"] += elapsed
        task_data["min"] = min(task_data["min"], elapsed)
        task_data["max"] = max(task_data["max"], elapsed)
        task_data["start"] = 0

        if self.verbose:
            elapsed_td = datetime.timedelta(milliseconds=elapsed)
            self._write(f'Finished task "{task}" iter {task_data["count"]} '
                        f'elapsed {elapsed}ms ({elapsed_td})')

    def annotate(self, text):
        """Write a custom annotation to the profiling file."""
        if self.enabled:
            self._write(text)

    def close(self):
        """Write the summary and close the profiler."""
        if not self.enabled:
            return

        tend = self.current_ms()
        total_elapsed = tend - self.tstart

        self._write("\n=== Raw Data ===")
        self._write(str(self.tasks))
        self._write("\n=== Summary ===")

        # Sort tasks by cumulative time descending
        for task, data in sorted(self.tasks.items(), key=lambda x: x[1]["cumulative"], reverse=True):
            avg = data["cumulative"] / data["count"]
            cumul_td = datetime.timedelta(milliseconds=data["cumulative"])
            self._write(
                f'Task: {task} | Count: {data["count"]} | '
                f'Cumulative: {data["cumulative"]}ms ({cumul_td}) | '
                f'Avg: {avg:.2f}ms | Min: {data["min"]}ms | Max: {data["max"]}ms'
            )

        total_td = datetime.timedelta(milliseconds=total_elapsed)
        self._write(f"\nGLOBAL execution elapsed: {total_elapsed}ms ({total_td})")
        self._write("#" * 50 + "\n")

    def current_ms(self):
        """Return current time in milliseconds."""
        return int(time.time() * 1000)

    def _write(self, text):
        """Internal helper to append text to the profiling file."""
        with open(self.file_path, "a") as f:
            f.write(text + "\n")

    @contextmanager
    def task(self, task_name):
        """Context manager for profiling a task."""
        self.start(task_name)
        try:
            yield
        finally:
            self.stop(task_name)


def main():
    profiler = Profile(file_path="prof.test", verbose=True)
    profiler.annotate("Unit test of profiler class with context manager")

    with profiler.task("Outer"):
        for i in range(10):
            with profiler.task("Inner"):
                for j in range(5):
                    with profiler.task("Loop"):
                        print("Inner loop work")
                        time.sleep(0.1)

    time.sleep(1)
    profiler.close()


if __name__ == "__main__":
    main()
