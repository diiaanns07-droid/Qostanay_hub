"""Qorgau Exam local backend. Composition root: proctor.app (owner: A01).

Module packages (each owned by one agent, see proctoring/coordination/OWNERSHIP.json):
capture (A02), phone (A03), attention (A04), fusion (A05), evidence (A08).
"""

from .settings import BACKEND_VERSION

__all__ = ["BACKEND_VERSION"]
