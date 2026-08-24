"""Say whether a world load finished, from the log alone, without eyeballing it.

The signal is the world controller's task manager. On entering `activity:initial_slice_set_loading`
the client starts a fixed set of tasks; a load that finishes completes `ENUM(3)` and then runs on
through twenty more. A load that does not finish leaves `ENUM(3)` started and never completed, and
the log simply stops. Both look identical from the outside - a frozen window - which is why this
reads the log rather than the screen. See `docs/WORLD_POPULATION.md` and the handoff.

Do not trust a verdict function that has not been replayed against a known pass and a known fail.
`--selftest` does exactly that against two archived runs and refuses to agree with itself.

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

ENTER = "Entering state 'activity:initial_slice_set_loading'"
IN_WORLD = "Entering state 'activity:in_world'"
TELEPORT = re.compile(r"Starting a new transition of type 'transitioning:teleportation' to '([^']+)'")
SUICIDE = "_connection_failure_suicide"
STARTED = re.compile(r"Started\s+task 'ENUM\((\d+)\)'")
COMPLETED = re.compile(r"Completed task 'ENUM\((\d+)\)' after '(\d+)ms'")
DESTINATION = re.compile(r"dest=([a-z_0-9]+)")
STALL = "hitch detected"
# The task whose completion is the load's verdict.
VERDICT_TASK = "3"


def verdicts(text: str) -> list[tuple[str, str, str]]:
    """@return (destination, verdict, detail) for each slice-set load the log contains."""
    out: list[tuple[str, str, str]] = []
    destination = "?"
    entered = False
    started: set[str] = set()
    completed: dict[str, str] = {}
    stalls = 0

    def settle() -> None:
        if not entered:
            return
        if VERDICT_TASK in completed:
            out.append((destination, "PASS",
                        f"ENUM(3) completed in {completed[VERDICT_TASK]}ms, "
                        f"{len(completed)} tasks done"))
        elif VERDICT_TASK in started:
            out.append((destination, "FAIL",
                        f"ENUM(3) started and never completed"
                        f"{f', {stalls} hitch asserts' if stalls else ''}"))
        else:
            out.append((destination, "PARTIAL", "never reached ENUM(3)"))

    for line in text.splitlines():
        found = DESTINATION.search(line)
        if found and found.group(1):
            destination = found.group(1)
        if ENTER in line:
            settle()
            entered, started, completed, stalls = True, set(), {}, 0
            continue
        if not entered:
            continue
        if STALL in line:
            stalls += 1
        begin = STARTED.search(line)
        if begin:
            started.add(begin.group(1))
        done = COMPLETED.search(line)
        if done:
            completed[done.group(1)] = done.group(2)
    settle()
    out.extend(transitions(text))
    return out


def transitions(text: str) -> list[tuple[str, str, str]]:
    """@return A verdict for each in-world teleport to another slice set.

    A world can load perfectly and then hang on the move to a different part of it, which is a
    different code path and was invisible to the check above - it reported four passes on a session
    the player experienced as a hang. A teleport that lands reaches `activity:in_world` again; one
    that does not ends with the activity-host connections raising `_connection_failure_suicide` and
    never reconnecting.
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
    print(f"\n{path.name}: {len(found)} slice-set load(s)")
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
        passes = [v for _d, v, _x in good if v == "PASS"]
        fails = [v for _d, v, _x in bad if v == "FAIL"]
        print(f"known pass {rest[0].name}: {len(passes)} PASS of {len(good)} loads")
        print(f"known fail {rest[1].name}: {len(fails)} FAIL of {len(bad)} loads")
        # Not "the pass log contains no failures" - it legitimately does, which is how this
        # check caught itself being wrong. What must hold is that each log's *known* outcome is
        # reproduced: the good log's routine worlds pass, and every load in the bad log fails.
        routine = {"edz_freeroam", "polaris_freeroam", "dreaming_city_freeroam",
                   "tangled_shore_freeroam"}
        ok = (bool(passes) and bool(fails)
              and all(v == "PASS" for d, v, _x in good if d in routine)
              and all(v == "FAIL" for _d, v, _x in bad))
        print("SELFTEST", "OK - the verdict separates them" if ok
              else "BROKEN - do not trust this until it does")
        raise SystemExit(0 if ok else 1)

    for path in ([Path(a) for a in argv] or [LIVE]):
        report(path)


if __name__ == "__main__":
    main()
