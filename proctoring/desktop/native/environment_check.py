"""Read-only environment snapshot. Never closes processes or changes Windows settings."""
import ntpath

REMOTE_BASENAMES = frozenset(("rustdesk", "anydesk", "teamviewer", "tv_w32", "tv_x64",
    "ultraviewer_desktop", "remoting_host", "parsecd", "winvnc", "tvnserver", "rutserv"))


def remote_processes(names):
    found = set()
    for name in names:
        base = ntpath.basename(name).lower()
        stem = base[:-4] if base.endswith(".exe") else base
        if stem in REMOTE_BASENAMES:
            found.add(stem + ".exe")
    return sorted(found)


def snapshot(api):
    return {"type": "environment", "processes": remote_processes(api.process_names()),
            "remote_session": api.remote_session()}
