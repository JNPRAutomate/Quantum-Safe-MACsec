#!/usr/bin/env python

import time
import datetime

# for python 3
xrange = range

class Profile:
    def __init__(self, file="/tmp/profile.txt", mode="w", enabled=True, verbose=False):
        self.enabled = enabled
        self.verbose = verbose
        if self.enabled:
            self.pf = open(file, mode)
            self.tasks = {}
            self.tstart = self.current_ms()
            self.pf.write('Profiler: {0}\n'.format(str(datetime.datetime.today())))

    def start(self, task):
        if not self.enabled:
            return
        if task not in self.tasks:
            self.tasks[task] = {"start": self.current_ms(), "count": 1, "cumulative": 0, "min": 86400000, "max": 0}
        else:
            self.tasks[task]["start"] = self.current_ms()
            self.tasks[task]["count"] += 1
        if self.verbose:
            self.pf.write('Starting task "{0}":\n'.format(task))

    def stop(self, task):
        if not self.enabled:
            return
        if task not in self.tasks or self.tasks[task]["start"] == 0:
            return
        tend = self.current_ms()
        elap = tend - self.tasks[task]["start"]
        if elap < self.tasks[task]["min"]:
            self.tasks[task]["min"] = elap
        if elap > self.tasks[task]["max"]:
            self.tasks[task]["max"] = elap
        self.tasks[task]["cumulative"] += elap
        elap2 = str(datetime.timedelta(milliseconds=elap))
        if self.verbose:
            self.pf.write('Finished task "{0}": iter {1} elapsed {2}ms ({3})\n'.format(task, self.tasks[task]["count"], elap, elap2))
        self.tasks[task]["start"] = 0

    def annotate(self, text):
        if not self.enabled:
            return
        self.pf.write('{0}\n'.format(text))

    def close(self):
        if not self.enabled:
            return
        tend = self.current_ms()
        self.pf.write('Raw data: {0}\n\n'.format(self.tasks))
        # klist = list(self.tasks.keys())
        # Order by cumulative
        klist = [ f for (f,g) in sorted(self.tasks.items(), key = lambda k: k[1]['cumulative'], reverse = True) ]
        for k in klist:
            avg = self.tasks[k]["cumulative"] / self.tasks[k]["count"]
            cumul = str(datetime.timedelta(
                milliseconds=self.tasks[k]["cumulative"]))
            self.pf.write('Task: {0} count {1} cumulative {2}ms ({3}) avg {4}ms min {5}ms max {6}ms\n'.format(
                k, self.tasks[k]["count"], self.tasks[k]["cumulative"], cumul, avg, self.tasks[k]["min"], self.tasks[k]["max"]))

        elap2 = str(datetime.timedelta(milliseconds=tend - self.tstart))
        self.pf.write('\nGLOBAL execution elapsed is {0}ms ({1})\n{2}\n\n'.format(
            tend - self.tstart, elap2, '#' * 50))
        self.pf.close()

    def current_ms(self):
        return int(time.time() * 1000)


def main():
    p = Profile(file="./prof.test", verbose=True)
    p.annotate("Unit test of profiler class")
    p.start("Outer")
    for i in xrange(0, 10):
        p.start("Inner")
        for j in xrange(0, 5):
            p.start("Loop")
            print("Inner")
            time.sleep(0.1)
            p.stop("Loop")
        p.stop("Inner")
    p.stop("Outer")
    time.sleep(1)
    p.close()


if __name__ == "__main__":
    main()
