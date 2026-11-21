"""The boundary between the two halves of AXIL Studio.

    part 1  input  -> spec      the tools (or the command line) build a register spec
                                (axilgen.ModuleSpec, "regspec") or crossbar specs (xbargen.XbarSpec)
    part 2  spec   -> HDL       axilgen / xbargen turn a spec, and nothing else, into files

The functions here give the exact input of part 2 as plain data (JSON-ready dicts), so it can be
shown read-only in the tools, printed by the command line (axilgen --dump-spec) and compared in
the tests: the same view in the tool and on the command line means part 1 did the same thing,
and the same view always gives the same files (part 2).

A register spec holds no address (one IP can be placed several times); the address an output
uses (C header default, testbench bus address) is a separate generation input, shown next to it.
"""

from . import addrmap
from . import axilgen as G
from . import xbargen as X
from .model import hex32


def ip_view(spec: "G.ModuleSpec", base=0):
    """Everything axilgen.generate_all() uses: the register spec and the generation inputs."""
    return {"regspec": spec.to_dict(),
            "generation": {"base": hex32(base),
                           "files": list(G.output_texts_names(spec))}}


def xbar_view(spec: "X.XbarSpec"):
    """Everything xbargen.write_xbar() uses (the spec holds the addresses itself)."""
    return {"xbarspec": spec.to_dict(), "generation": {"files": X.output_names(spec)}}


def ip_base(project, ip_name):
    """Generation address of a project IP: its first address-map instance (0 when not placed)."""
    insts = project.instances_of(ip_name)
    return insts[0].base if insts else 0


def project_ip_view(project, ip):
    spec = G.spec_from_ipblock(ip)
    view = ip_view(spec, ip_base(project, ip.name))
    view["instances"] = [{"name": i.name, "base": hex32(i.base)} for i in project.instances_of(ip.name)]
    return view


def project_xbar_specs(project, style=None):
    """One XbarSpec per interconnect, top first (the address map is laid out first)."""
    try:
        addrmap.readdress(project)
    except ValueError:
        pass                    # reported by validate(); the specs then show the addresses as they are
    return [addrmap.xbar_spec(project, b, style) for b in addrmap.levels(project)]


def project_view(project):
    """Every spec of a project: the IPs' register specs and the interconnects' crossbar specs."""
    return {"ips": [project_ip_view(project, ip) for ip in project.ips],
            "interconnects": [xbar_view(s) for s in project_xbar_specs(project)],
            "errors": addrmap.validate(project)}
