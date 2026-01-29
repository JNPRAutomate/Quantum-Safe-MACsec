# Python Profiler Class

This document describes a **Python 3 profiler** script that allows you to measure and log the execution time of tasks (or code sections) in your Python code, in milliseconds. 
It’s designed to help you see **how long different parts of a script take to run**, including counts, cumulative time, min/max, and averages.
The profiler includes a **context manager interface** for clean and hierarchical task timing.

## Table of Contents
<!--TOC-->
- [Python Profiler Class](#python-profiler-class)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Features](#features)
  - [Installation](#installation)
  - [Usage](#usage)
    - [Basic start/stop profiling](#basic-startstop-profiling)
    - [Context manager usage (recommended)](#context-manager-usage-recommended)
    - [Nested loops example](#nested-loops-example)
  - [Profiler Output](#profiler-output)
  - [Advanced Notes](#advanced-notes)
<!--/TOC-->
---

## Overview

The profiler allows you to:

- Measure **execution time** of named tasks (allows timing **named tasks** with start/stop calls).
- Collects stats: track **count**, **cumulative time**, **average**, **minimum**, and **maximum** durations.
- Writes a detailed **profiling log** to a Log file, in a human-readable format.
- Profile and support **nested tasks** (Outer → Inner → Loop) with automatic hierarchical timing.
- Useful for **benchmarking parts of a Python program** without external libraries.


The script is fully compatible with **Python 3** and can be used as a module in other scripts.

---

## Features

- Task timing using `start(task)` and `stop(task)` methods.
- Verbose logging of task start/stop events.
- Automatic statistics: cumulative time, min/max time, average, and iteration counts.
- Annotate the profiling log with custom text.
- Context manager interface for clean nested profiling:

```python
with profiler.task("MyTask"):
    # code to profile
````

* Clean, modern Python 3 implementation using f-strings and `with open(...)`.
* Output summary sorted by **cumulative execution time**.

---

## Installation

Simply copy the `profiler.py` file into your project. No external dependencies are required.
The script uses only Python standard library modules (`time`, `datetime`, `contextlib`).

---

## Usage

### Basic start/stop profiling

```python
from profiler import Profile
import time

profiler = Profile(file_path="profile.log", verbose=True)

profiler.start("TaskA")
time.sleep(1)  # Simulate work
profiler.stop("TaskA")

profiler.close()
```

---

### Context manager usage (recommended)

```python
from profiler import Profile
import time

profiler = Profile(file_path="profile.log", verbose=True)

with profiler.task("Outer"):
    time.sleep(1)  # Some code to profile

profiler.close()
```

* Using the **context manager** is cleaner and automatically handles `start()` and `stop()`.
* Supports nested tasks without manually tracking start/stop.

---

### Nested loops example

This example demonstrates **nested profiling** for loops, similar to the original test case:

```python
from profiler import Profile
import time

profiler = Profile(file_path="prof.test", verbose=True)
profiler.annotate("Unit test of profiler class with context manager")

with profiler.task("Outer"):
    for i in range(10):
        with profiler.task("Inner"):
            for j in range(5):
                with profiler.task("Loop"):
                    print("Processing inner loop work...")
                    time.sleep(0.1)  # Simulate work

profiler.close()
```

* Tasks `"Outer"`, `"Inner"`, and `"Loop"` are tracked separately.
* Automatically calculates **count, cumulative, min/max, and average times** for each task.

---

## Profiler Output

A typical profiling log looks like this:

```
Profiler started: 2025-11-27 12:00:00
Unit test of profiler class with context manager
Starting task "Outer"
Starting task "Inner"
Starting task "Loop"
Finished task "Loop" iter 1 elapsed 100ms (0:00:00.100000)
...

=== Summary ===
Task: Loop | Count: 50 | Cumulative: 5000ms (0:00:05) | Avg: 100.00ms | Min: 100ms | Max: 100ms
Task: Inner | Count: 10 | Cumulative: 5000ms (0:00:05) | Avg: 500.00ms | Min: 500ms | Max: 500ms
Task: Outer | Count: 1 | Cumulative: 5000ms (0:00:05) | Avg: 5000.00ms | Min: 5000ms | Max: 5000ms

GLOBAL execution elapsed: 6000ms (0:00:06)
##################################################
```

* Shows **task counts**, **cumulative execution time**, **average**, **min/max**, and **global execution time**.
* Nested tasks are automatically tracked.

---

## Advanced Notes

* **Enable/Disable profiling**: Pass `enabled=False` when creating the profiler to temporarily disable profiling.
* **Verbose mode**: Set `verbose=True` to log start/stop events to the profiling file.
* **Custom annotations**: Use `profiler.annotate("My note")` to add custom messages in the log.
* **File management**: The profiler appends to the specified log file; previous logs are preserved unless you manually delete the file.

