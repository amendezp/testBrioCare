"""Live, real-time voice-demo server for BrioCare.

A thin FastAPI + WebSocket layer over the existing synchronous ``SessionMachine``.
It holds one machine per room in memory, drives timers off a real ``WallClock``,
phrases spoken actions through Claude, and pushes to two browser roles (kid /
clinician). Nothing under ``briocare.runtime`` is modified — only imported.
"""
