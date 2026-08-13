"""
fork_recovery.py
================
Deployable, minimal recovery patch for ``pyrotgfork==2.2.24``.

WHY IT IS NEEDED (exact failure path, verified against the installed fork)
--------------------------------------------------------------------------
The Telegram receive path is driven by ``Session.recv_worker()``. When the TCP link drops,
``Connection.recv()`` -> ``TCP.recv()`` NEVER raises: it returns ``None`` on timeout/OSError/EOF.
So ``recv_worker`` does not die on its own - it schedules a recovery and ``break``s via an
unmanaged ``self.client.loop.create_task(self.restart())`` (session.py lines 218, 292, 356, 416,
462). No reference is retained and no done-callback is attached, so:

* ``Session.start()`` raises e.g. ``TimeoutError`` (builtin, from the initial Ping/InitConnection
  timing out on a half-open socket) -> its ``except Exception`` branch does ``stop(); raise``.
  The raise propagates out of the fire-and-forget ``restart()`` task, which is then reported only
  as ``asyncio ERROR: Task exception was never retrieved`` and NEVER schedules another restart.
* ``Session.start()`` can also RETURN WITHOUT reaching STARTED (its ``except (OSError, RPCError)``
  branch schedules a nested restart and returns), leaving the session not started.

Result: the receiver/recovery is left permanently dead while the process and the event loop stay
alive -> the bot stops answering Telegram updates while unrelated asyncio tasks (UTAG timer, ...)
keep running. This is the production freeze observed after commit a96f605.

FIX (real task-supervision, NOT a time-based watchdog)
------------------------------------------------------
Replace ``Session.restart`` with one coalesced, supervised recovery loop that:
  * keeps a single in-flight flag so only ONE recovery runs at a time (no duplicate receivers,
    no reconnect churn, no multiple concurrent Session objects);
  * retries with capped backoff and logs every failure (the receiver's restart is supervised);
  * VERIFIES the session actually reached STARTED (``is_started``) before declaring success -
    covering the silent "start() returned without STARTED" case;
  * stops retrying only when the session was deliberately stopped or the client disconnected.
Wrap ``recv_worker`` / ``ping_worker`` so any unexpected exception is logged and funnels into the
same supervised recovery instead of silently killing the worker.

This keeps the original ``start``/``stop``/``handle_packet``/``connect`` behaviour untouched; only
`restart`, `recv_worker` and `ping_worker` are monkey-patched, so the change is minimal and
reproducible on a stock ``pip install pyrotgfork==2.2.24`` environment.
"""
import asyncio
import logging

from pyrogram.session.session import Session, SessionState

log = logging.getLogger("ventofork.recovery")

_installed = False
# Originals captured before patching (raw unbound functions, callable as func(session)).
_orig_recv_worker = None
_orig_ping_worker = None


def _should_stop_retrying(session) -> bool:
    """Stop auto-recovery when the session was deliberately torn down or the client disconnected."""
    return session._state == SessionState.STOPPED or not getattr(
        getattr(session, "client", None), "is_connected", True
    )


async def _restart(session):
    """Supervised, coalesced recovery loop. Replaces ``Session.restart``.

    Runs at most one recovery at a time per Session; retries with capped backoff; treats both
    a raised ``start()`` and a ``start()`` that silently failed to reach STARTED as a failed
    attempt; stops only when the session/client was deliberately shut down.
    """
    if getattr(session, "_restart_inflight", False):
        return  # another recovery is already running
    session._restart_inflight = True
    delay = 1.0
    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                async with session.restart_lock:
                    await session.stop()
                    await session.start()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(
                    "[recovery] %s: Session.restart attempt %d failed: %s",
                    session.client.name, attempt, e, exc_info=True,
                )
                if _should_stop_retrying(session):
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            if session.is_started.is_set():
                log.warning("[recovery] %s: Session recovered after %d attempt(s).", session.client.name, attempt)
                return

            # start() returned without reaching STARTED (the fork's OSError/RPCError branch
            # schedules a nested, now no-op restart and returns). Treat as a failed attempt.
            log.warning(
                "[recovery] %s: Session.start returned without STARTED (attempt %d); retrying.",
                session.client.name, attempt,
            )
            if _should_stop_retrying(session):
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
    finally:
        session._restart_inflight = False


def _request_recovery(session):
    """Coalesced entry point for all recovery. `_restart` itself is idempotent (in-flight flag)."""
    try:
        session.client.loop.create_task(_restart(session))
    except (RuntimeError, AttributeError):
        # Event loop not running / client absent - nothing schedulable right now.
        pass


async def _recv_worker(session):
    """Supervised recv_worker: an unexpected inside-failure is logged and triggers recovery."""
    try:
        await _orig_recv_worker(session)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("[recovery] %s: NetworkTask failed unexpectedly: %s", session.client.name, e, exc_info=True)
        _request_recovery(session)


async def _ping_worker(session):
    """Supervised ping_worker: an unexpected inside-failure is logged and triggers recovery."""
    try:
        await _orig_ping_worker(session)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("[recovery] %s: PingTask failed unexpectedly: %s", session.client.name, e, exc_info=True)
        _request_recovery(session)


def install():
    """Monkey-patch the Session class. Idempotent."""
    global _installed, _orig_recv_worker, _orig_ping_worker
    if _installed:
        return
    _orig_recv_worker = Session.recv_worker
    _orig_ping_worker = Session.ping_worker
    Session.recv_worker = _recv_worker
    Session.ping_worker = _ping_worker
    Session.restart = _restart
    Session._request_recovery = _request_recovery
    _installed = True
    log.warning("[recovery] pyrotgfork Session recovery supervision installed (recv_worker/ping_worker/restart patched).")


install()

