"""Requirement consistency checking and completion.

Uses only the *definitional* relations between ballistic targets::

    I_t = F_avg * t_b        F_avg = I_t / t_b
    I_t = m_p * I_sp * g0
    I_t = F_avg * t_b

Missing quantities are filled from these relations; contradictory inputs are
reported, never silently overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..constants import G0
from ..models.requirements import RequirementSet


@dataclass
class ConsistencyReport:
    filled: dict[str, float] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    degrees_of_freedom: int = 0
    notes: list[str] = field(default_factory=list)


def check_and_complete(req: RequirementSet, rel_tol: float = 0.02) -> ConsistencyReport:
    """Fill missing ballistic targets and flag contradictions."""
    report = ConsistencyReport()
    f = req.thrust
    tb = req.burn_time
    mp = req.propellant_mass
    it = req.total_impulse
    isp = req.specific_impulse
    favg = req.average_thrust

    def conflict(msg: str) -> None:
        report.conflicts.append(msg)

    # total impulse relations
    if it is None:
        if favg is not None and tb is not None:
            it = favg * tb
            report.filled["total_impulse"] = it
        elif f is not None and tb is not None:
            it = f * tb
            report.filled["total_impulse"] = it
    if mp is None and it is not None and isp is not None:
        mp = it / (isp * G0)
        report.filled["propellant_mass"] = mp
    if isp is None and it is not None and mp is not None:
        isp = it / (mp * G0)
        report.filled["specific_impulse"] = isp
    if favg is None and it is not None and tb is not None:
        favg = it / tb
        report.filled["average_thrust"] = favg
    if tb is None and it is not None and favg:
        tb = it / favg
        report.filled["burn_time"] = tb

    # cross-checks
    if f is not None and favg is not None:
        if abs(f - favg) > rel_tol * max(abs(f), abs(favg)):
            report.notes.append(
                f"thrust {f:.0f} N differs from average thrust {favg:.0f} N by more "
                f"than {rel_tol:.0%}; check whether 'thrust' means peak or average"
            )
    if it is not None and f is not None and tb is not None:
        if abs(it - f * tb) > rel_tol * abs(it):
            conflict(f"total impulse {it:.0f} N.s inconsistent with F*t_b = {f*tb:.0f} N.s")

    # write back the completed values so downstream modules see them
    for key, val in report.filled.items():
        setattr(req, key, val)

    # degrees of freedom: how many independent ballistic targets were supplied
    supplied = sum(v is not None for v in (f, tb, mp, it, isp))
    report.degrees_of_freedom = supplied
    if supplied < 2:
        report.notes.append("fewer than two independent ballistic targets: under-determined")
    return report
