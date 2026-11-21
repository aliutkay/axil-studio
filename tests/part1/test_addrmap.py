"""Part 1: address-map rules (project model -> addresses of the crossbar specs)."""

import random

import pytest

from axil_studio.core import addrmap as A
from tests.helpers import bases, hier_project, ip, project


def test_auto_addresses_are_consecutive_and_aligned():
    p = project(ip("a"), ip("b", 0x1FFC))
    x = A.add_instance(p, "a")
    y = A.add_instance(p, "b")          # 8 KB register map -> 8 KB window, aligned
    z = A.add_instance(p, "a")
    assert (x.base, y.base, z.base) == (0x0, 0x2000, 0x4000)
    assert A.window_of(p, y) == 0x2000
    assert A.validate(p) == []


def test_start_address_and_default_window():
    p = project()
    A.set_start(p, 0x40000000)
    A.set_default_window(p, 0x10000)
    a, b = A.add_instance(p, "ip"), A.add_instance(p, "ip")
    assert (a.base, b.base) == (0x40000000, 0x40010000)


def test_fixed_entry_keeps_its_address_and_others_skip_it():
    p = project()
    a = A.add_instance(p, "ip")
    a.base, a.fixed = 0x1000, True
    b = A.add_instance(p, "ip")
    c = A.add_instance(p, "ip")
    A.readdress(p)
    assert (a.base, b.base, c.base) == (0x1000, 0x0, 0x2000)


def test_bridge_children_are_inside_its_window_after_its_own_window():
    p = project()
    b = A.add_bridge(p, self_window=0x1000)
    c = A.add_instance(p, "ip", parent=b)
    assert c.base == b.base + 0x1000
    assert A.window_of(p, b) == 0x2000          # grows with its contents
    assert A.outside(p, b) == set()


def test_hand_sized_bridge_reports_overflow():
    p = project()
    b = A.add_bridge(p, window=0x1000)
    A.add_instance(p, "ip", parent=b)
    before = A._fill(p)
    A.add_instance(p, "ip", parent=b)
    assert A.fit_errors(p) and A.check_fits(p, before)


def test_refused_move_restores_every_address_and_lock():
    p = project()
    b = A.add_bridge(p, window=0x1000)
    A.add_instance(p, "ip", parent=b)
    x = A.add_instance(p, "ip")
    x.base, x.fixed = 0x100000, True
    A.readdress(p)
    keep = bases(p)
    with pytest.raises(ValueError):
        A.move(p, x, b)
    assert bases(p) == keep
    assert x in p.instances


def test_move_to_another_bus_makes_the_address_automatic():
    p = project()
    b = A.add_bridge(p)
    x = A.add_instance(p, "ip")
    x.base, x.fixed = 0x100000, True
    note = A.move(p, x, b)
    assert x in b.children and not x.fixed and note
    assert A.validate(p) == []


def test_bridge_cannot_move_into_itself():
    p = project()
    b0 = A.add_bridge(p)
    b1 = A.add_bridge(p, parent=b0)
    with pytest.raises(ValueError):
        A.move(p, b0, b1)


def test_typed_bridge_address_moves_its_contents():
    p = project()
    b = A.add_bridge(p, window=0x10000)
    k = A.add_instance(p, "ip", parent=b)
    k.base, k.fixed = 0x8000, True
    A.readdress(p)
    A.set_base(p, b, 0x100000)
    A.readdress(p)
    assert (b.base, k.base, k.fixed) == (0x100000, 0x108000, True)
    assert A.validate(p) == []


def test_readdress_overflow_leaves_the_map_unchanged():
    p = project()
    A.set_start(p, 0xFFFFE000)
    for _ in range(3):
        A.add_instance(p, "ip")
    keep = bases(p)
    with pytest.raises(ValueError):
        A.readdress(p)
    assert bases(p) == keep


def test_validation_errors():
    p = project()
    a, b = A.add_instance(p, "ip"), A.add_instance(p, "ip")
    b.base, b.fixed = a.base, True
    assert any("çakışıyor" in e for e in A.validate(p))
    p2 = project()
    assert A.validate(p2)                                   # empty map
    A.add_instance(p2, "ip")
    A.add_bridge(p2)
    assert any("boş" in e for e in A.validate(p2))          # empty Sub-bus Bridge


@pytest.mark.parametrize("module, clash", [("axilxbar", True), ("ip", True), ("ip_pk", True), ("my_xbar", False)])
def test_module_name_clashes(module, clash):
    p = project()
    A.add_instance(p, "ip")
    p.interconnect = {"module": module}
    assert bool(A.module_clashes(p)) == clash


def test_hierarchy_levels_and_names():
    p = hier_project()
    lv = A.levels(p)
    assert [None if b is None else b.name for b in lv] == [None, "SBB_0", "SBB_1"]
    assert A.validate(p) == []


@pytest.mark.parametrize("seed", range(150))
def test_random_maps_keep_the_invariants(seed):
    """Random edits: when the map validates, re-addressing changes nothing, every child lies in
    its bridge and no two windows of a bus overlap."""
    r = random.Random(seed)
    p = project(ip("a"), ip("b", 0x1FFC), ip("c", 0x7FFC))
    for _ in range(r.randint(1, 10)):
        bus = r.choice([None] + p.bridges())
        k = r.random()
        if k < .6 or not p.all_instances():
            A.add_instance(p, r.choice("abc"), parent=bus)
        elif k < .8:
            A.add_bridge(p, parent=bus, self_window=r.choice([0, 0x1000]), window=r.choice([0, 0, 0x10000]))
        elif k < .9:
            inst = r.choice(p.all_instances())
            inst.base, inst.fixed = r.randrange(0, 1 << 20) & ~0xFFF, True
        else:
            try:
                A.move(p, r.choice(p.all_instances()), r.choice([None] + p.bridges()), r.choice([None, 0, 1]))
            except ValueError:
                pass
        try:
            A.readdress(p)
        except ValueError:
            return
    if A.validate(p):
        return
    assert not A.readdress(p)
    for b in A.levels(p):
        assert not A.overlapping(p, b)
        if b is not None:
            assert not A.outside(p, b)
        assert A.xbar_spec(p, b).validate() == []
