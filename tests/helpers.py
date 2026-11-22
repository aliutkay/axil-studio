"""Small builders shared by the tests."""

from axil_studio.core import addrmap as A
from axil_studio.core.axilgen import FieldSpec, ModuleSpec, RegSpec, to_ipblock
from axil_studio.core.model import IPBlock, Project, Register


def ip(name="ip", last_offset=0):
    return IPBlock(name, registers=[Register("R", last_offset)])


def project(*ips):
    return Project("t", list(ips) or [ip()])


def tiny_spec(name="tst_reg", packed=True):
    """Two RW registers: enough to tell slaves apart in a bus simulation."""
    return ModuleSpec(name, "test IP", [RegSpec("VAL", 0, fields=[FieldSpec("VAL", 0, 32, "RW")]),
                                        RegSpec("ID", 4, fields=[FieldSpec("ID", 0, 32, "CONST", 0x1D)])],
                      packed=packed)


def hier_project(spec=None):
    """top: ip_0, SUB_0 (ip_1, ip_2, SUB_1 (ip_3, ip_5), SUB_2 (ip_6)), ip_4 - every IP the same
    definition. SUB_2 has a single slave, so no crossbar: crossbars top, SUB_0 and SUB_1."""
    spec = spec or tiny_spec()
    p = Project("hier", [to_ipblock(spec, 0, with_spec=True)])
    m = spec.module
    A.add_instance(p, m, "ip_0")
    b0 = A.add_bridge(p)
    A.add_instance(p, m, "ip_1", parent=b0)
    A.add_instance(p, m, "ip_2", parent=b0)
    b1 = A.add_bridge(p, parent=b0)
    A.add_instance(p, m, "ip_3", parent=b1)
    A.add_instance(p, m, "ip_5", parent=b1)
    b2 = A.add_bridge(p, parent=b0)
    A.add_instance(p, m, "ip_6", parent=b2)
    A.add_instance(p, m, "ip_4")
    A.readdress(p)
    return p


def bases(p):
    return {i.name: (i.base, i.fixed) for i in p.all_instances()}
