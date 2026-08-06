#!/usr/bin/env python3
"""Unit tests for the GT2N KLayout DRC deck.

Each case is a synthetic layout, a handful of shapes on the real GT2N GDS
layers, built so that one rule of the deck has an unambiguous verdict on it.
The deck is then run over the layout exactly as LibreLane's KLayout.DRC step
runs it, and the per-category violation counts in the resulting marker database
are compared against what the case says they should be.

Every case exercises one of the primitive mappings the port rests on --
directional width, exact width, directional spacing, directional separation,
corner-to-corner spacing, non-rectangles, directional enclosure, edge
alignment, containment and interaction -- at a passing and a failing geometry,
because the mapping from IC Validator's axis-named checks to KLayout's
edge-orientation-named ones is the part of the port that could plausibly be
inverted without anything downstream noticing.

Run inside a shell with KLayout on PATH:

    python3 .librelane-fork/drc_tests/run.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

import klayout.db as db
from klayout.rdb import ReportDatabase

HERE = os.path.dirname(os.path.abspath(__file__))
DECK = os.path.normpath(
    os.path.join(HERE, "..", "..", "ciel", "build", "descriptors", "gt2n", "gt2n_drc.drc")
)

# GT2N GDS layer numbers, as gt2_main.drc.rs assigns them.
ACT = 2
GATE = 3
DUMMY = 4
GCUT = 5
NSEL = 6
BPR = 8
SDCON = 10
M0 = 20
V0 = 22
M1 = 25
V1 = 27
M2 = 30

# The GT2N database unit is 0.5 nm, so a coordinate in nanometres is two
# database units. Every rule dimension in the PDK is an integer number of
# nanometres, which keeps halves of them on grid.
DBU_PER_NM = 2


class Case:
    def __init__(self, name, shapes, expect):
        #: shapes: list of (layer, x0, y0, x1, y1) boxes in nanometres, or
        #: (layer, [(x, y), ...]) polygons in nanometres.
        self.name = name
        self.shapes = shapes
        #: expect: {category name: "zero" | "nonzero"}
        self.expect = expect


def rect(layer, x0, y0, x1, y1):
    return (layer, x0, y0, x1, y1)


def poly(layer, points):
    return (layer, points)


# An M1 wire that satisfies M1.1 (>= 14 nm horizontal width) and M1.2 (>= 28 nm
# vertical length), used wherever a case needs an M1 shape it is not testing.
def m1_wire(x, y, h=100):
    return rect(M1, x, y, x + 14, y + h)


CASES = [
    # -- dir_width: minimum width along a named axis ------------------------
    Case(
        "m1_width_pass",
        [m1_wire(0, 0)],
        {"M1.1 : Horizontal width of M1 >= 14 nm": "zero"},
    ),
    Case(
        "m1_width_fail",
        [rect(M1, 0, 0, 12, 100)],
        {"M1.1 : Horizontal width of M1 >= 14 nm": "nonzero"},
    ),
    # The perpendicular half of the same mapping: if HORIZONTAL and VERTICAL
    # were swapped, this case and the one above would both still fire, but on
    # each other's rule.
    Case(
        "m1_length_pass",
        [rect(M1, 0, 0, 14, 28)],
        {"M1.2 : Vertical length of M1 >= 28 nm": "zero"},
    ),
    Case(
        "m1_length_fail",
        [rect(M1, 0, 0, 14, 20)],
        {
            "M1.2 : Vertical length of M1 >= 28 nm": "nonzero",
            "M1.1 : Horizontal width of M1 >= 14 nm": "zero",
        },
    ),
    # -- exact_width: too narrow and too wide are both violations -----------
    Case(
        "m0_exact_width_pass",
        [rect(M0, 0, 0, 100, 12)],
        {"M0.1 : Exact Vertical width of M0 = 12 nm": "zero"},
    ),
    Case(
        "m0_exact_width_too_narrow",
        [rect(M0, 0, 0, 100, 10)],
        {"M0.1 : Exact Vertical width of M0 = 12 nm": "nonzero"},
    ),
    Case(
        "m0_exact_width_too_wide",
        [rect(M0, 0, 0, 100, 16)],
        {"M0.1 : Exact Vertical width of M0 = 12 nm": "nonzero"},
    ),
    # A shape correct in one part and too wide in another: the violation is
    # the offending part only, which is what makes this the ICV formulation
    # and not a whole-polygon test.
    Case(
        "m0_exact_width_partial",
        [poly(M0, [(0, 0), (100, 0), (100, 16), (200, 16), (200, 28), (0, 28)])],
        {"M0.1 : Exact Vertical width of M0 = 12 nm": "nonzero"},
    ),
    # -- dir_space: minimum spacing along a named axis ----------------------
    Case(
        "m1_hspace_pass",
        [m1_wire(0, 0), m1_wire(28, 0)],
        {"M1.3 : Horizontal spacing between M1 layers >= 14 nm": "zero"},
    ),
    Case(
        "m1_hspace_fail",
        [m1_wire(0, 0), m1_wire(26, 0)],
        {
            "M1.3 : Horizontal spacing between M1 layers >= 14 nm": "nonzero",
            "M1.4 : Vertical (TtT) spacing between M1 layers on same track >= 20 nm": "zero",
        },
    ),
    Case(
        "m1_vspace_pass",
        [m1_wire(0, 0, 40), m1_wire(0, 60, 40)],
        {"M1.4 : Vertical (TtT) spacing between M1 layers on same track >= 20 nm": "zero"},
    ),
    Case(
        "m1_vspace_fail",
        [m1_wire(0, 0, 40), m1_wire(0, 58, 40)],
        {
            "M1.4 : Vertical (TtT) spacing between M1 layers on same track >= 20 nm": "nonzero",
            "M1.3 : Horizontal spacing between M1 layers >= 14 nm": "zero",
        },
    ),
    # -- dir_space with the euclidian metric (ICV extension = RADIAL) -------
    Case(
        "act_vspace_pass",
        [rect(ACT, 0, 0, 100, 20), rect(ACT, 0, 55, 100, 75)],
        {"ACT.3 : Vertical spacing between ACT >= 30 nm": "zero"},
    ),
    Case(
        "act_vspace_fail",
        [rect(ACT, 0, 0, 100, 20), rect(ACT, 0, 45, 100, 65)],
        {"ACT.3 : Vertical spacing between ACT >= 30 nm": "nonzero"},
    ),
    # -- dir_separation: two layers, one axis -------------------------------
    Case(
        "gcut_act_sep_pass",
        [rect(GCUT, 0, 0, 50, 10), rect(ACT, 0, 22, 50, 40)],
        {"GCUT.ACT.1 : Vertical spacing between GCUT and ACT >= 10 nm": "zero"},
    ),
    Case(
        "gcut_act_sep_fail",
        [rect(GCUT, 0, 0, 50, 10), rect(ACT, 0, 18, 50, 40)],
        {"GCUT.ACT.1 : Vertical spacing between GCUT and ACT >= 10 nm": "nonzero"},
    ),
    # -- corner_space, metal flavour: diagonal only -------------------------
    # dx = 14, dy = 6 -> 15.2 nm between the facing corners, under M1.5's 16.
    Case(
        "m1_corner_diag_fail",
        [m1_wire(0, 0, 40), m1_wire(28, 46, 40)],
        {"M1.5 : M1 corner-to-corner spacing >= 16 nm": "nonzero"},
    ),
    # dx = 14, dy = 12 -> 18.4 nm, clear of it.
    Case(
        "m1_corner_diag_pass",
        [m1_wire(0, 0, 40), m1_wire(28, 52, 40)],
        {"M1.5 : M1 corner-to-corner spacing >= 16 nm": "zero"},
    ),
    # Two wires at the legal 14 nm horizontal spacing have convex corners
    # 14 nm apart. The port does not report that against M1.5's 16 nm, because
    # M1.3 declares the gap legal -- the documented deviation.
    Case(
        "m1_corner_straight_gap_not_reported",
        [m1_wire(0, 0), m1_wire(28, 0)],
        {
            "M1.5 : M1 corner-to-corner spacing >= 16 nm": "zero",
            "M1.3 : Horizontal spacing between M1 layers >= 14 nm": "zero",
        },
    ),
    # -- corner_space on a via layer: the same rule, and it has to be -------
    # Two V0 stacked on adjacent M0 tracks sit 12 nm apart on the same M1
    # track. That is how every GT2N standard cell brings an M1 stub down to
    # two M0 tracks, so a corner rule that measured straight gaps would fail
    # the vendor's own library against the vendor's own runset.
    Case(
        "v0_stacked_on_adjacent_m0_tracks_not_reported",
        [rect(V0, 0, 0, 14, 12), rect(V0, 0, 24, 14, 36)],
        {"V0.6 : V0 corner-to-corner spacing >= 18 nm": "zero"},
    ),
    Case(
        "v1_corner_straight_gap_not_reported",
        [rect(V1, 0, 0, 14, 12), rect(V1, 26, 0, 40, 12)],
        {"V1.6 : V1 corner-to-corner spacing >= 18 nm": "zero"},
    ),
    # dx = 12, dy = 12 -> 17.0 nm diagonal, under 18.
    Case(
        "v1_corner_diag_fail",
        [rect(V1, 0, 0, 14, 12), rect(V1, 26, 24, 40, 36)],
        {"V1.6 : V1 corner-to-corner spacing >= 18 nm": "nonzero"},
    ),
    # dx = 16, dy = 12 -> 20.0 nm diagonal, clear of 18.
    Case(
        "v1_corner_diag_pass",
        [rect(V1, 0, 0, 14, 12), rect(V1, 30, 24, 44, 36)],
        {"V1.6 : V1 corner-to-corner spacing >= 18 nm": "zero"},
    ),
    # -- non_rectangles -----------------------------------------------------
    Case(
        "m1_bend_fail",
        [poly(M1, [(0, 0), (14, 0), (14, 60), (60, 60), (60, 74), (0, 74)])],
        {"M1.6 : M1 must not bend": "nonzero"},
    ),
    Case(
        "m1_bend_pass",
        [m1_wire(0, 0)],
        {"M1.6 : M1 must not bend": "zero"},
    ),
    # -- dir_enclosure ------------------------------------------------------
    # V1.M1.EN is a vertical-direction enclosure: M1 must extend past V1 above
    # and below. M1 is 14 wide and V1 is 14 wide, so the horizontal enclosure
    # is zero by construction and must not be what this rule looks at.
    Case(
        "v1_m1_enclosure_pass",
        [rect(M1, 0, 0, 14, 40), rect(V1, 0, 14, 14, 26), rect(M2, 0, 14, 60, 26)],
        {"V1.M1.EN : V1 enclosure by M1 on opposite sides in the vertical direction >= 4 nm": "zero"},
    ),
    Case(
        "v1_m1_enclosure_fail",
        [rect(M1, 0, 12, 14, 28), rect(V1, 0, 14, 14, 26), rect(M2, 0, 14, 60, 26)],
        {"V1.M1.EN : V1 enclosure by M1 on opposite sides in the vertical direction >= 4 nm": "nonzero"},
    ),
    # -- unaligned_edges ----------------------------------------------------
    Case(
        "v1_edge_align_pass",
        [rect(M1, 0, 0, 14, 40), rect(V1, 0, 14, 14, 26), rect(M2, 0, 14, 60, 26)],
        {"V1.3 : Vertical edges of V1 should align with M1": "zero"},
    ),
    # A wider M1 leaves the via's vertical edges floating inside it.
    Case(
        "v1_edge_align_fail",
        [rect(M1, 0, 0, 30, 40), rect(V1, 8, 14, 22, 26), rect(M2, 0, 14, 60, 26)],
        {"V1.3 : Vertical edges of V1 should align with M1": "nonzero"},
    ),
    # ACT.6 is the other edge rule: vertical ACT edges must land in DUMMY.
    Case(
        "act_ends_in_dummy_pass",
        [rect(ACT, 0, 0, 100, 20), rect(DUMMY, -7, -10, 7, 30), rect(DUMMY, 93, -10, 107, 30)],
        {"ACT.6 : ACT must end inside a DUMMY layer": "zero"},
    ),
    Case(
        "act_ends_in_dummy_fail",
        [rect(ACT, 0, 0, 100, 20)],
        {"ACT.6 : ACT must end inside a DUMMY layer": "nonzero"},
    ),
    # -- not_inside (include_touch = ALL: coincident edges are inside) ------
    Case(
        "v0_inside_m0_pass",
        [rect(M0, 0, 0, 100, 12), rect(V0, 20, 0, 34, 12)],
        {"V0.4 : V0 must not be outside M0": "zero"},
    ),
    Case(
        "v0_inside_m0_fail",
        [rect(M0, 0, 0, 100, 12), rect(V0, 120, 0, 134, 12)],
        {"V0.4 : V0 must not be outside M0": "nonzero"},
    ),
    # -- not_overlapping (ICV include_touch = NONE) -------------------------
    Case(
        "v1_touches_m1_only_fail",
        [rect(M1, 0, 0, 14, 40), rect(V1, 14, 14, 28, 26)],
        {"V1.7 : V1 must interact with M1": "nonzero"},
    ),
    Case(
        "v1_overlaps_m1_pass",
        [rect(M1, 0, 0, 14, 40), rect(V1, 0, 14, 14, 26)],
        {"V1.7 : V1 must interact with M1": "zero"},
    ),
    # -- interacting (ICV include_touch = ALL) ------------------------------
    Case(
        "gcut_touches_act_fail",
        [rect(GCUT, 0, 0, 50, 10), rect(ACT, 0, 10, 50, 40)],
        {"GCUT.8 : GCUT may not interact with ACT": "nonzero"},
    ),
    Case(
        "gcut_clear_of_act_pass",
        [rect(GCUT, 0, 0, 50, 10), rect(ACT, 0, 30, 50, 60)],
        {"GCUT.8 : GCUT may not interact with ACT": "zero"},
    ),
    # -- dir_enclosure across two layers, the well-select flavour -----------
    Case(
        "nsel_encloses_act_pass",
        [rect(ACT, 0, 20, 100, 40), rect(NSEL, 0, 0, 100, 60)],
        {"NSEL.3 : Minimum enclosure of ACT by NSEL in the vertical direction >= 15 nm": "zero"},
    ),
    Case(
        "nsel_encloses_act_fail",
        [rect(ACT, 0, 20, 100, 40), rect(NSEL, 0, 10, 100, 50)],
        {"NSEL.3 : Minimum enclosure of ACT by NSEL in the vertical direction >= 15 nm": "nonzero"},
    ),
    # -- exact_width on a backside layer, and BPR.3's micrometre distance ---
    Case(
        "bpr_width_pass",
        [rect(BPR, 0, 0, 20000, 32)],
        {"BRP.1 : BPR vertical width = 32 nm": "zero"},
    ),
    Case(
        "bpr_width_fail",
        [rect(BPR, 0, 0, 20000, 40)],
        {"BRP.1 : BPR vertical width = 32 nm": "nonzero"},
    ),
    # A break of 2 um in a rail is under BPR.3's 10 um, so it is a violation.
    Case(
        "bpr_break_fail",
        [rect(BPR, 0, 0, 20000, 32), rect(BPR, 22000, 0, 42000, 32)],
        {"BPR.3 : BPR must be continuous": "nonzero"},
    ),
    # -- SDCON.2, ported net-blind -----------------------------------------
    Case(
        "sdcon_vspace_pass",
        [rect(SDCON, 0, 0, 16, 40), rect(SDCON, 0, 62, 16, 102)],
        {"SDCON.2 : Vertical (TtT) spacing between SDCON >= 20 nm": "zero"},
    ),
    Case(
        "sdcon_vspace_fail",
        [rect(SDCON, 0, 0, 16, 40), rect(SDCON, 0, 55, 16, 95)],
        {"SDCON.2 : Vertical (TtT) spacing between SDCON >= 20 nm": "nonzero"},
    ),
]


def build(case, path):
    layout = db.Layout()
    layout.dbu = 1.0 / (DBU_PER_NM * 1000)
    top = layout.create_cell("TESTCASE")
    for shape in case.shapes:
        layer = layout.layer(shape[0], 0)
        if len(shape) == 5:
            _, x0, y0, x1, y1 = shape
            top.shapes(layer).insert(
                db.Box(
                    x0 * DBU_PER_NM, y0 * DBU_PER_NM, x1 * DBU_PER_NM, y1 * DBU_PER_NM
                )
            )
        else:
            _, points = shape
            top.shapes(layer).insert(
                db.Polygon(
                    [db.Point(x * DBU_PER_NM, y * DBU_PER_NM) for x, y in points]
                )
            )
    layout.write(path)


def run_deck(gds, report):
    result = subprocess.run(
        [
            "klayout",
            "-b",
            "-zz",
            "-r",
            DECK,
            "-rd",
            f"input={gds}",
            "-rd",
            "topcell=TESTCASE",
            "-rd",
            f"report={report}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"klayout exited {result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return result


def counts(report):
    database = ReportDatabase("db")
    database.load(report)
    return {c.name(): c.num_items() for c in database.each_category()}


def main():
    if shutil.which("klayout") is None:
        print("klayout is not on PATH: run this inside LibreLane's nix develop")
        return 1

    workdir = tempfile.mkdtemp(prefix="gt2n-drc-tests-")
    failures = []
    width = max(len(c.name) for c in CASES)

    for case in CASES:
        gds = os.path.join(workdir, f"{case.name}.gds")
        report = os.path.join(workdir, f"{case.name}.lyrdb")
        build(case, gds)
        run_deck(gds, report)
        got = counts(report)

        problems = []
        for category, expectation in case.expect.items():
            if category not in got:
                problems.append(f"{category!r} was never reported on")
                continue
            n = got[category]
            if expectation == "zero" and n != 0:
                problems.append(f"{category!r} fired {n} time(s), expected none")
            elif expectation == "nonzero" and n == 0:
                problems.append(f"{category!r} did not fire")

        if problems:
            failures.append((case.name, problems))
            print(f"FAIL {case.name.ljust(width)}  {'; '.join(problems)}")
        else:
            print(f"ok   {case.name.ljust(width)}  {len(case.expect)} assertion(s)")

    print()
    print(f"{len(CASES) - len(failures)}/{len(CASES)} cases passed")
    if failures:
        print(f"layouts and reports kept in {workdir}")
        return 1
    shutil.rmtree(workdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
