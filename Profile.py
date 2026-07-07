#!/usr/bin/env python3

import time
import datetime
from contextlib import contextmanager


class Profile:
    def __init__(self, file=None, file_path=None, enabled=True, verbose=False, mode="a"):
        self.enabled = enabled
        self.verbose = verbose
        self.file_path = file or file_path or "profile.txt"
        self.mode = mode or "a"
        self.tasks = {}
        self.tstart = self.current_ms()
        self._initialized = False

        if self.enabled:
            self._write(f"Profiler started: {datetime.datetime.now()}", first=True)

    def start(self, task):
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
        if not self.enabled:
            return

        if task not in self.tasks:
            return

        if self.tasks[task]["start"] == 0:
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
            self._write(
                f'Finished task "{task}" iter {task_data["count"]} '
                f'elapsed {elapsed}ms ({elapsed_td})'
            )

    def annotate(self, text):
        if self.enabled:
            self._write(text)

    def report(self):
        """
        qkd_macsec.py-compatible method.
        Some versions call report(), others call close().
        """
        if not self.enabled:
            return

        tend = self.current_ms()
        total_elapsed = tend - self.tstart

        self._write("")
        self._write("=== Raw Data ===")
        self._write(str(self.tasks))
        self._write("")
        self._write("=== Summary ===")

        for task, data in sorted(
            self.tasks.items(),
            key=lambda x: x[1]["cumulative"],
            reverse=True
        ):
            count = data["count"] or 1
            avg = data["cumulative"] / count
            cumul_td = datetime.timedelta(milliseconds=data["cumulative"])

            min_value = 0 if data["min"] == float("inf") else data["min"]

            self._write(
                f'Task: {task} | Count: {data["count"]} | '
                f'Cumulative: {data["cumulative"]}ms ({cumul_td}) | '
                f'Avg: {avg:.2f}ms | Min: {min_value}ms | Max: {data["max"]}ms'
            )

        total_td = datetime.timedelta(milliseconds=total_elapsed)
        self._write(f"")
        self._write(f"GLOBAL execution elapsed: {total_elapsed}ms ({total_td})")
        self._write("#" * 50)
        self._write("")

    def close(self):
        self.report()

    def current_ms(self):
        return int(time.time() * 1000)

    def _write(self, text, first=False):
        if not self.enabled:
            return

        write_mode = self.mode if first and not self._initialized else "a"

        with open(self.file_path, write_mode) as f:
            f.write(text + "\n")

        self._initialized = True

    @contextmanager
    def task(self, task_name):
        self.start(task_name)
        try:
            yield
        finally:
            self.stop(task_name)


def main():
    profiler = Profile(file="prof.test", verbose=True, enabled=True, mode="w+")
    profiler.annotate("Unit test of profiler class")
    with profiler.task("Outer"):
        time.sleep(0.1)
    profiler.close()


if __name__ == "__main__":
    main()