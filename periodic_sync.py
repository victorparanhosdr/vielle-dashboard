"""Serial periodic refreshes, sharing the manual refresh coordinator."""
import functools
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BRAZIL = ZoneInfo("America/Sao_Paulo")
STATE_KEY = "_PERIODIC_SYNC_STATE"


class ClinicSyncLocks:
    def __init__(self):
        self.guard = threading.Lock()
        self.locks = {}

    def serialize(self, clinic_getter):
        def decorate(function):
            @functools.wraps(function)
            def wrapped(*args, **kwargs):
                clinic = clinic_getter()
                with self.guard:
                    lock = self.locks.setdefault(clinic, threading.RLock())
                with lock:
                    return function(*args, **kwargs)
            return wrapped
        return decorate


class PeriodicClinicSync:
    def __init__(self, clinics, jobs, run, read_state, save_state, *,
                 interval_seconds=300, history_hour=3, clock=None, settings_getter=None):
        if interval_seconds < 300 or not 0 <= history_hour <= 23:
            raise ValueError("Invalid automatic synchronization schedule")
        self.clinics, self.jobs, self.run = tuple(clinics), jobs, run
        self.read_state, self.save_state = read_state, save_state
        self.interval, self.history_hour = interval_seconds, history_hour
        self.clock = clock or (lambda: datetime.now(BRAZIL))
        self.settings_getter = settings_getter
        self.intervals, self.enabled = {}, {}
        self.states, self.deadlines = {}, {}
        self.tick_lock = threading.Lock()

    def _prepare(self, clinic):
        settings = self.settings_getter(clinic) if self.settings_getter else {}
        interval = max(300, int(settings.get("interval_seconds", self.interval)))
        self.enabled[clinic] = settings.get("enabled", True)
        if clinic in self.deadlines:
            self.deadlines[clinic]["recent"] += interval - self.intervals[clinic]
            self.intervals[clinic] = interval
            return
        now = self.clock()
        state = self.read_state(clinic)
        state = state if isinstance(state, dict) else {}
        recent = state.get("recent_finished_at", 0)
        recent = recent if isinstance(recent, (int, float)) and 0 < recent <= now.timestamp() else 0
        daily = state.get("next_daily_at", 0)
        daily = daily if isinstance(daily, (int, float)) and 0 < daily <= now.timestamp() + 86400 else 0
        if not daily:
            scheduled = now.replace(hour=self.history_hour, minute=0, second=0, microsecond=0)
            if scheduled <= now:
                scheduled += timedelta(days=1)
            daily = scheduled.timestamp()
        self.states[clinic] = state
        self.intervals[clinic] = interval
        self.deadlines[clinic] = {
            "recent": max(now.timestamp(), recent + interval) if recent else now.timestamp() + interval,
            "daily": daily,
        }

    def status(self, clinic):
        if clinic not in self.deadlines:
            return None
        state, deadlines = self.states[clinic], self.deadlines[clinic]
        return {"enabled": self.enabled[clinic], "interval_minutes": self.intervals[clinic] // 60,
                "timezone": "America/Sao_Paulo", "history_hour": self.history_hour,
                "next_recent_at": int(deadlines["recent"]), "next_daily_at": int(deadlines["daily"]),
                "recent_finished_at": state.get("recent_finished_at"),
                "daily_finished_at": state.get("daily_finished_at"),
                "daily_phase": state.get("daily_phase")}

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            for clinic in self.clinics:
                try:
                    self._prepare(clinic)
                    if not self.enabled[clinic]:
                        continue
                    now = self.clock()
                    deadlines = self.deadlines[clinic]
                    mode = "daily" if now.timestamp() >= deadlines["daily"] else "recent"
                    if now.timestamp() < deadlines[mode]:
                        continue
                    result = self.jobs.start(clinic, lambda progress: self.run(clinic, mode, progress),
                                             background=False, source="automatic_" + mode)
                    if not result.get("started"):
                        continue
                    finished = self.clock()
                    state = self.states[clinic]
                    state[mode + "_finished_at"] = int(finished.timestamp())
                    state[mode + "_phase"] = result["phase"]
                    deadlines["recent"] = finished.timestamp() + self.intervals[clinic]
                    state["recent_finished_at"] = int(finished.timestamp())
                    if mode == "daily":
                        next_day = (finished + timedelta(days=1)).replace(
                            hour=self.history_hour, minute=0, second=0, microsecond=0)
                        deadlines["daily"] = next_day.timestamp()
                    state["next_daily_at"] = int(deadlines["daily"])
                    self.save_state(clinic, state)
                except Exception:
                    # Keep provider payloads and credentials out of scheduler logs.
                    logging.warning("Periodic refresh could not complete for clinic %s", clinic)
        finally:
            self.tick_lock.release()

    def serve(self, stop):
        while not stop.is_set():
            self.tick()
            stop.wait(15)
