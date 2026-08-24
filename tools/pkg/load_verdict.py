"""Say whether a world load finished, from the log alone, without eyeballing it.

A load starts at `activity:initial_slice_set_loading` and is judged by where the chain it hands off
to ends up: reaching `activity:in_world` is the only success, and falling into `cleanup` is the
failure. The client says why on the way out - a prerequisite fails, and above that a job stalled,
and above that the activity host was declared lost. Both outcomes look identical from the outside
(a frozen window), which is why this reads the log rather than the screen.

Do not trust a verdict function that has not been replayed against a known pass and a known fail.
`--selftest` does exactly that against two archived runs and refuses to agree with itself.

Two earlier versions of this file were wrong in ways the replay caught, so the reasoning is kept:

  * Judging on "did `ENUM(3)` complete" reports a PASS on a load that failed. The task numbers are
    per state, so the cleanup state the failure falls into starts its own `ENUM(3)` and completes
    it in 0ms, sixteen milliseconds after the load was abandoned. A window that closes only at the
    next load folds that in.
  * Settling the window on the next state change is also wrong, in the other direction: a load
    that works leaves for `activity:physics_join` and reaches the world several states later, so
    that rule failed all four loads of a known-good session. Only `in_world` and `cleanup` settle.
  * Watching fresh loads alone reports four passes on a session the player experienced as a hang,
    because an in-world teleport to another part of the same world is a different code path.
    `transitions()` covers it.

Usage:
    python load_verdict.py                       # the live log
    python load_verdict.py <path> [<path> ...]   # archived runs
    python load_verdict.py --selftest <pass.log> <fail.log>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LIVE = Path(r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log")

LOADING = "activity:initial_slice_set_loading"
IN_WORLD_STATE = "activity:in_world"
ENTER_ANY = re.compile(r"Entering state '([a-z_:]+)'")
IN_WORLD = f"Entering state '{IN_WORLD_STATE}'"
TELEPORT = re.compile(r"Starting a new transition of type 'transitioning:teleportation' to '([^']+)'")
SUICIDE = "_connection_failure_suicide"
COMPLETED = re.compile(r"Completed task 'ENUM\((\d+)\)' after '(\d+)ms'")
DESTINATION = re.compile(r"dest=([a-z_0-9]+)")

# Why a load was abandoned, most specific first. The prerequisite is the state machine's own
# verdict; the stall and the host loss are the two things that produce it here.
PREREQ = re.compile(r"Prerequisite 'ENUM\((\d+)\)' failed for reason '([a-z_]+)'")
STALL = re.compile(r"hitch detected: mainloop world controller state \S+ \S+ job name: '([^']+)'")
HOST_LOST = "Lost connection to activity host"
DURATION = re.compile(r"Total time spent: \[(\d+)\] ms in world controller state: \[(\d+)\]")
# The cleanup state reports its own progress on a timer while it drains. A repeat past this many
# milliseconds is a teardown that is not draining, which strands the client after a failed load.
CLEANUP = re.compile(r"state:cleanup: After (\d+)ms, status: '(\d+)', tasks active: '(0x[0-9a-f]+)'")
CLEANUP_STALL_MS = 15000
# States that settle a load. Reaching the world is the only success; falling into cleanup is
# the failure. Everything between the two is still the load running.
TERMINAL = {IN_WORLD_STATE, "cleanup"}


class Window:
    """One pass through the loading state."""

    def __init__(self, destination: str) -> None:
        self.destination = destination
        self.next_state = ""
        self.completed: dict[str, str] = {}
        self.stalls: list[str] = []
        self.host_lost = 0
        self.prereq: tuple[str, str] | None = None
        self.duration_ms: int | None = None

    def verdict(self, landed: bool) -> tuple[str, str, str]:
        detail = []
        if self.duration_ms is not None:
            detail.append(f"{self.duration_ms / 1000:.1f}s in the load")
        if landed:
            detail.append(f"{len(self.completed)} tasks done")
            return self.destination, "PASS", ", ".join(detail)
        if self.stalls:
            worst = max(set(self.stalls), key=self.stalls.count)
            detail.append(f"job '{worst}' stalled ({len(self.stalls)} asserts)")
        if self.host_lost:
            detail.append(f"activity host declared lost x{self.host_lost}")
        if self.prereq:
            detail.append(f"prerequisite ENUM({self.prereq[0]}) failed '{self.prereq[1]}'")
        detail.append(f"left for '{self.next_state or 'nothing - log ends here'}'")
        return self.destination, "FAIL", ", ".join(detail)


def verdicts(text: str) -> list[tuple[str, str, str]]:
    """@return (destination, verdict, detail) for each slice-set load the log contains."""
    out: list[tuple[str, str, str]] = []
    destination = "?"
    window: Window | None = None
    # The load does not go straight to the world: it hands off to `activity:physics_join` and runs
    # on through several more states first. So leaving the loading state settles nothing, and the
    # window stays open until the chain reaches the world or falls into cleanup.
    in_flight = False

    for line in text.splitlines():
        found = DESTINATION.search(line)
        if found and found.group(1):
            destination = found.group(1)

        entering = ENTER_ANY.search(line)
        if entering:
            state = entering.group(1)
            if window is not None and (state == LOADING or state in TERMINAL):
                window.next_state = state
                out.append(window.verdict(landed=state == IN_WORLD_STATE))
                window, in_flight = None, False
            elif window is not None and not in_flight:
                window.next_state = state
                in_flight = True
            if state == LOADING:
                window = Window(destination)
            continue

        if window is None:
            continue
        done = COMPLETED.search(line)
        if done:
            window.completed[done.group(1)] = done.group(2)
        stalled = STALL.search(line)
        if stalled:
            window.stalls.append(stalled.group(1))
        if HOST_LOST in line:
            window.host_lost += 1
        failed = PREREQ.search(line)
        if failed:
            window.prereq = (failed.group(1), failed.group(2))
        spent = DURATION.search(line)
        if spent:
            window.duration_ms = int(spent.group(1))

    if window is not None:
        out.append(window.verdict(landed=False))
    out.extend(transitions(text))
    out.extend(cleanup_stalls(text))
    return out


def cleanup_stalls(text: str) -> list[tuple[str, str, str]]:
    """@return A finding when the teardown state stops draining.

    A failed load falls into `cleanup`, which normally finishes in single-digit milliseconds. When
    it instead reports the same one active task every fifteen seconds the client is stranded there
    with no screen of its own, which reads as the same freeze as the load that preceded it.
    """
    worst = 0
    active = ""
    for line in text.splitlines():
        found = CLEANUP.search(line)
        if found and int(found.group(1)) > worst:
            worst, active = int(found.group(1)), found.group(3)
    if worst < CLEANUP_STALL_MS:
        return []
    return [("cleanup", "STUCK", f"{worst / 1000:.0f}s draining, tasks active {active}")]


def transitions(text: str) -> list[tuple[str, str, str]]:
    """@return A verdict for each in-world teleport to another slice set.

    A world can load perfectly and then hang on the move to a different part of it, which is a
    different code path. A teleport that lands reaches `activity:in_world` again; one that does
    not ends with the activity-host connections raising `_connection_failure_suicide` and never
    reconnecting.
    """
    out: list[tuple[str, str, str]] = []
    pending: str | None = None
    suicides = 0
    for line in text.splitlines():
        started = TELEPORT.search(line)
        if started:
            if pending is not None:
                out.append(("teleport", "FAIL", f"to {pending}, never landed"))
            pending, suicides = started.group(1), 0
            continue
        if pending is None:
            continue
        if SUICIDE in line:
            suicides += 1
        if IN_WORLD in line:
            out.append(("teleport", "PASS", f"to {pending}, reached in_world"))
            pending = None
    if pending is not None:
        out.append(("teleport", "FAIL",
                    f"to {pending}, never reached in_world"
                    f"{f', {suicides} bap connection suicides' if suicides else ''}"))
    return out


def report(path: Path) -> list[tuple[str, str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    found = verdicts(text)
    print(f"\n{path.name}: {len(found)} finding(s)")
    for destination, verdict, detail in found:
        print(f"  {verdict:<8} {destination:<26} {detail}")
    return found


def main() -> None:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        rest = [Path(a) for a in argv if a != "--selftest"]
        if len(rest) != 2:
            raise SystemExit("--selftest needs a known-pass log and a known-fail log")
        good, bad = (verdicts(p.read_text(encoding="utf-8", errors="replace")) for p in rest)
        loads = lambda rows: [(d, v) for d, v, _x in rows if d not in ("teleport", "cleanup")]
        passes = [v for _d, v in loads(good) if v == "PASS"]
        fails = [v for _d, v in loads(bad) if v == "FAIL"]
        print(f"known pass {rest[0].name}: {len(passes)} PASS of {len(loads(good))} loads")
        print(f"known fail {rest[1].name}: {len(fails)} FAIL of {len(loads(bad))} loads")
        # Not "the pass log contains no failures" - it legitimately does, which is how this check
        # caught itself being wrong. What must hold is that each log's *known* outcome is
        # reproduced: the good log's routine worlds pass, and every load in the bad log fails.
        routine = {"edz_freeroam", "polaris_freeroam", "dreaming_city_freeroam",
                   "tangled_shore_freeroam"}
        ok = (bool(passes) and bool(fails)
              and all(v == "PASS" for d, v in loads(good) if d in routine)
              and all(v == "FAIL" for _d, v in loads(bad)))
        print("SELFTEST", "OK - the verdict separates them" if ok
              else "BROKEN - do not trust this until it does")
        raise SystemExit(0 if ok else 1)

    for path in ([Path(a) for a in argv] or [LIVE]):
        report(path)


if __name__ == "__main__":
    main()
