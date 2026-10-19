"""Final-host memory admission, including concurrent BB003 and FW002 workers."""
import json
import os
from pathlib import Path
import re
import subprocess

from .admission import FW002_SERVICES, _fields, _seconds
from .storage import sha

CONFLICT_WORKERS = ("hqa-fw001-daily.service", "hqa-shadow-daily.service")
FW002_WORKERS = ("hqa-fw002-decision.service", "hqa-fw002-fill.service")
PRIORITY_WORKERS = (*CONFLICT_WORKERS, *FW002_WORKERS)
WORKERS = ("hqa-bb003-daily.service",)
DISPATCHERS = ("hqa-bb003-check.service", "hqa-fw002-check@decision.service", "hqa-fw002-check@fill.service")


def bound(value):
    if value in {"max", "infinity", str(2**64 - 1)}:
        return None
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("BB003 unbounded/invalid host memory metadata: " + value)
    return int(value)


def cgroup_hierarchy(read):
    mounts = []
    for line in read("/proc/self/mountinfo").splitlines():
        before, separator, after = line.partition(" - ")
        if separator and after.split()[0] == "cgroup2":
            fields = before.split()
            paths = [re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), p) for p in fields[3:5]]
            if len(paths) != 2 or any(not p.startswith("/") or ".." in Path(p).parts for p in paths):
                raise ValueError("BB003 invalid cgroup v2 mount metadata")
            mounts.append(tuple(paths))
    if len(mounts) != 1:
        raise ValueError("BB003 requires an unambiguous cgroup v2 hierarchy")
    return mounts[0]


def memory_max(read, path, *, root=False):
    try:
        return bound(read(str(path)).strip())
    except FileNotFoundError as exc:
        if root:
            return None  # A real cgroup v2 hierarchy root has no memory.max.
        raise ValueError("BB003 missing non-root cgroup memory.max: " + str(path)) from exc
    except OSError as exc:
        raise ValueError("BB003 cannot read cgroup memory.max: " + str(path)) from exc


def host_limits(*, run=None, read=None):
    """Read effective manager limits, including inherited slice/cgroup caps.

    Slice caps are conservatively treated as aggregate limits even if separate
    slices would permit more memory. Swap never enlarges the admitted budget.
    No credential, process environment or experimental data is read here.
    """
    run = subprocess.check_output if run is None else run
    read = (lambda p: Path(p).read_text()) if read is None else read
    match = re.search(r"^MemTotal:\s+(\d+) kB$", read("/proc/meminfo"), re.M)
    if not match:
        raise ValueError("BB003 cannot read host RAM")
    ram = int(match[1]) * 1024
    hierarchy_root, mount = cgroup_hierarchy(read)
    cgroups = {}

    def ancestors(group):
        path = Path(group)
        if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(hierarchy_root):
            raise ValueError("BB003 service cgroup is outside the visible hierarchy: " + group)
        caps = []
        while True:
            name = path.as_posix()
            if name not in cgroups:
                cgroups[name] = memory_max(read, Path(mount) / path.relative_to(hierarchy_root) / "memory.max",
                                          root=name == hierarchy_root == "/")
            caps.append(cgroups[name])
            if name == hierarchy_root:
                return caps
            path = path.parent

    root_cap = ancestors(hierarchy_root)[0]
    units, slices, absent = {}, {}, []
    properties = "LoadState,NeedDaemonReload,Slice,ControlGroup,MemoryMax,EffectiveMemoryMax,MemoryAccounting,Conflicts,After,KillMode,TimeoutStopUSec,User,Group,Type,RemainAfterExit"
    for unit in (*PRIORITY_WORKERS, *WORKERS, *DISPATCHERS):
        fields = _fields(run(["systemctl", "show", unit, "--property=" + properties], text=True))
        if unit in FW002_SERVICES and fields.get("LoadState") == "not-found":
            absent.append(unit)
            continue
        if fields.get("LoadState") != "loaded" or fields.get("NeedDaemonReload") != "no":
            raise ValueError("BB003 needs effective installed memory limits: " + unit)
        for key in ("MemoryMax", "EffectiveMemoryMax"):
            bound(fields[key])
        slice_unit = fields["Slice"]
        if not re.fullmatch(r"(?:[-\w.:]|\\x[0-9a-fA-F]{2})+\.slice", slice_unit):
            raise ValueError("BB003 invalid installed service slice: " + unit)
        if unit in WORKERS:
            if (not set(CONFLICT_WORKERS) <= set(fields["Conflicts"].split())
                    or set(FW002_SERVICES).intersection(fields["Conflicts"].split())
                    or not set(CONFLICT_WORKERS) <= set(fields["After"].split())
                    or fields["KillMode"] != "control-group" or fields["User"] != "hqa"
                    or fields["Type"] != "oneshot" or fields["RemainAfterExit"] != "no"
                    or fields["MemoryAccounting"] != "yes" or fields["MemoryMax"] != str(600 * 2**20)
                    or not 0 < _seconds(fields["TimeoutStopUSec"]) <= 5):
                raise ValueError("BB003 worker must stop before each conflicting priority worker: " + unit)
        if unit in CONFLICT_WORKERS and (fields["Type"] != "oneshot" or fields["RemainAfterExit"] != "no"):
            raise ValueError("BB003 conflicting priority workers must keep their oneshot start job: " + unit)
        if unit in FW002_WORKERS and (fields["Type"] != "exec" or bound(fields["MemoryMax"]) is None):
            raise ValueError("BB003 needs bounded FW002 exec workers for concurrent memory admission")
        if unit == DISPATCHERS[0] and (set(fields["Conflicts"].split()).intersection(PRIORITY_WORKERS)
                or fields["User"] != "root" or fields["Group"] != "root"
                or fields["MemoryMax"] != str(64 * 2**20) or fields["MemoryAccounting"] != "yes"):
            raise ValueError("BB003 requires its small root dispatcher outside worker Conflicts")
        if slice_unit not in slices:
            limits = _fields(run(["systemctl", "show", slice_unit,
                "--property=LoadState,NeedDaemonReload,ControlGroup,MemoryMax,EffectiveMemoryMax"], text=True))
            if limits.get("LoadState") != "loaded" or limits.get("NeedDaemonReload") != "no":
                raise ValueError("BB003 cannot bound inherited slice: " + slice_unit)
            bound(limits["MemoryMax"])
            bound(limits["EffectiveMemoryMax"])
            if not limits["ControlGroup"]:
                raise ValueError("BB003 installed slice has no cgroup: " + slice_unit)
            ancestors(limits["ControlGroup"])
            slices[slice_unit] = limits
        group = str(Path(slices[slice_unit]["ControlGroup"]) / unit)
        reported = fields["ControlGroup"]
        if reported and reported != group:
            raise ValueError("BB003 service cgroup differs from its installed slice: " + unit)
        unit_path = Path(mount) / Path(group).relative_to(hierarchy_root) / "memory.max"
        try:
            unit_cap = bound(read(str(unit_path)).strip())
        except FileNotFoundError as exc:
            if reported:
                raise ValueError("BB003 missing active unit cgroup memory.max: " + unit) from exc
            unit_cap = bound(fields["MemoryMax"])  # Inactive unit cgroups need not exist yet.
        except OSError as exc:
            raise ValueError("BB003 cannot read unit cgroup memory.max: " + unit) from exc
        caps = [ram, unit_cap, *(bound(fields[k]) for k in ("MemoryMax", "EffectiveMemoryMax")),
                *ancestors(slices[slice_unit]["ControlGroup"])]
        # Normalize inactive/active ControlGroup without binding transient pids
        # or the presence of a cgroup that systemd creates only at unit start.
        fields["ControlGroup"] = group
        units[unit] = {k: " ".join(sorted(v.split())) if k in {"Conflicts", "After"} else v
                       for k, v in fields.items()}
        units[unit]["cgroup_effective_bytes"] = min(c for c in caps if c is not None)
    if absent and set(absent) != set(FW002_SERVICES):
        raise ValueError("BB003 incomplete FW002 memory/unit inventory")
    trigger = _fields(run(["systemctl", "show", "hqa-bb003-daily.timer",
        "--property=LoadState,NeedDaemonReload,Triggers"], text=True))
    if (trigger.get("LoadState") != "loaded" or trigger.get("NeedDaemonReload") != "no"
            or trigger.get("Triggers", "").split() != [DISPATCHERS[0]]):
        raise ValueError("BB003 timer must target the root admission dispatcher")
    caps = [ram, *cgroups.values(), *(bound(s[k]) for s in slices.values()
                                     for k in ("MemoryMax", "EffectiveMemoryMax"))]
    return {"host": os.uname().nodename, "ram_bytes": ram, "root_cgroup_memory_max_bytes": root_cap,
            "cgroup_hierarchy": {"root": hierarchy_root, "mount": mount, "limits": cgroups},
            "slices": slices, "units": units, "timer": trigger,
            "effective_bytes": min(c for c in caps if c is not None)}


def verify_memory(raw, manifest, limits):
    """Bind measured peaks and reserve BB003 + one FW002 enforced worker cap."""
    evidence = json.loads(raw)
    if (evidence.get("scope") != "bb003_plus_one_fw002_worker_dispatchers_and_resident_services"
            or evidence.get("verified_worst_case") is not True
            or evidence.get("peak_metric") != "cgroup_memory_peak_including_children_and_cache"
            or evidence.get("resident_scope") != "all_other_services_and_kernel_including_permitted_priority_overlap"
            or evidence.get("host") != limits["host"] or evidence.get("limits") != limits
            or evidence.get("source_files") != manifest["files"]
            or evidence.get("environment_sha256") != manifest["environment_sha256"]):
        raise ValueError("BB003 requires final-host/source/environment memory evidence and current effective RAM/cgroup limits")
    units = limits["units"]
    groups = {"heavy_job_peak_bytes": set((*PRIORITY_WORKERS, *WORKERS)).intersection(units),
              "dispatcher_peak_bytes": set(DISPATCHERS).intersection(units)}
    for group, expected in groups.items():
        peaks = evidence.get(group)
        if not isinstance(peaks, dict) or peaks.keys() != expected:
            raise ValueError("BB003 missing measured memory peaks: " + group)
        for unit, peak in peaks.items():
            caps = [bound(units[unit][k]) for k in ("MemoryMax", "EffectiveMemoryMax")]
            caps.append(units[unit]["cgroup_effective_bytes"])
            if type(peak) is not int or peak <= 0 or any(peak >= c for c in caps if c is not None):
                raise ValueError("BB003 measured job exceeds effective cgroup cap: " + unit)
    resident = evidence.get("resident_services_peak_bytes")
    if type(resident) is not int or resident <= 0:
        raise ValueError("BB003 requires measured resident services/OS memory")
    peaks = evidence["heavy_job_peak_bytes"]
    concurrent_peak = peaks[WORKERS[0]] + max((peaks[u] for u in FW002_WORKERS if u in units), default=0)
    concurrent_cap = units[WORKERS[0]]["cgroup_effective_bytes"] + max(
        (units[u]["cgroup_effective_bytes"] for u in FW002_WORKERS if u in units), default=0)
    serial_peak = max(peaks[u] for u in CONFLICT_WORKERS)
    dispatchers = sum(evidence["dispatcher_peak_bytes"].values())
    required = max(concurrent_cap, concurrent_peak, serial_peak) + dispatchers + resident
    if required >= limits["effective_bytes"]:
        raise ValueError("BB003 activation refused: concurrent BB003 + FW002 worker + dispatchers + resident services do not fit effective RAM/cgroup limit")
    return {"host_memory_validation_sha256": sha(raw), "host_memory_budget": {
        "effective_bytes": limits["effective_bytes"], "bb003_plus_fw002_peak_bytes": concurrent_peak,
        "bb003_plus_fw002_reserved_bytes": concurrent_cap, "largest_serial_priority_peak_bytes": serial_peak,
        "all_dispatchers_peak_bytes": dispatchers, "resident_services_peak_bytes": resident,
        "required_bytes": required}}
