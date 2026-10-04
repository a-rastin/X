"""Run with python3 test_schema.py; requires lxml (already installed here).

Fixtures are synthetic format tests, not clinical models or probabilities.
"""
from copy import deepcopy
from itertools import permutations
from pathlib import Path

from lxml import etree


def main():
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    schema = etree.XMLSchema(etree.parse(str(Path(__file__).with_name("schema.xml")), parser))
    base = etree.fromstring(b"""<BIF VERSION="0.3"><NETWORK><NAME>Format_Test</NAME>
      <VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>
      <VARIABLE TYPE="nature"><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>
      <DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>
      <DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.8 0.2 0.1 0.9</TABLE></DEFINITION>
    </NETWORK></BIF>""", parser)
    checked = 0

    def check(label, expected, edit=lambda root: None):
        nonlocal checked
        root = deepcopy(base)
        edit(root)
        actual = schema.validate(root)
        assert actual == expected, (label, expected, str(schema.error_log))
        checked += 1

    def text_at(path, value):
        return lambda root: setattr(root.find(path), "text", value)

    def append_at(path, xml):
        return lambda root: root.find(path).append(etree.fromstring(xml, parser))

    def remove_at(path):
        def edit(root):
            node = root.find(path)
            node.getparent().remove(node)
        return edit

    check("ordinary discrete BN", True)
    check("scientific notation", True, text_at("NETWORK/DEFINITION/TABLE", "+5E-1 .5"))
    check("Unicode escaped property", True, append_at("NETWORK", "<PROPERTY>label=ارزیابی &amp; assessment</PROPERTY>".encode()))
    check("schema location hint", True, lambda r: r.set("{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation", "schema.xml"))
    for version in ("0.2", "0.30", "1.0"):
        check("wrong version " + version, False, lambda r, v=version: r.set("VERSION", v))
    check("missing version", False, lambda r: r.attrib.clear())
    check("empty BIF", False, remove_at("NETWORK"))
    check("missing network name", False, remove_at("NETWORK/NAME"))
    for value in ("", "1A", "A-B", "A B", " A", "A ", "A:B", "A!", "α"):
        check("invalid identifier " + repr(value), False, text_at("NETWORK/NAME", value))
    check("leading underscore", True, text_at("NETWORK/NAME", "_Test2"))
    check("unknown type", False, lambda r: r.find("NETWORK/VARIABLE").set("TYPE", "PD"))
    check("duplicate outcome", False, append_at("NETWORK/VARIABLE", b"<OUTCOME>no</OUTCOME>"))
    check("duplicate variable", False, lambda r: r.find("NETWORK").append(deepcopy(r.find("NETWORK/VARIABLE"))))
    check("duplicate definition", False, lambda r: r.find("NETWORK").append(deepcopy(r.find("NETWORK/DEFINITION"))))
    check("unknown FOR", False, text_at("NETWORK/DEFINITION/FOR", "Missing"))
    check("unknown GIVEN", False, text_at("NETWORK/DEFINITION/GIVEN", "Missing"))
    check("duplicate GIVEN", False, append_at("NETWORK/DEFINITION[2]", b"<GIVEN>A</GIVEN>"))
    check("missing FOR", False, remove_at("NETWORK/DEFINITION/FOR"))
    check("missing TABLE", False, remove_at("NETWORK/DEFINITION/TABLE"))
    check("duplicate FOR", False, append_at("NETWORK/DEFINITION", b"<FOR>A</FOR>"))
    check("duplicate TABLE", False, append_at("NETWORK/DEFINITION", b"<TABLE>1 0</TABLE>"))
    for value in ("", " ", "TODO", "NaN", "INF", "-INF", "1e999", "0.5,0.5", "50%", "0.5x", "1e"):
        check("invalid table " + repr(value), False, text_at("NETWORK/DEFINITION/TABLE", value))
    check("custom element", False, append_at("NETWORK", b"<DIAGNOSIS/>"))
    check("custom attribute", False, lambda r: r.find("NETWORK").set("clinical_question", "Q"))

    def two_networks(root):
        second = deepcopy(root.find("NETWORK"))
        second.find("NAME").text = "Second"
        root.append(second)

    check("variable names scoped per network", True, two_networks)
    check("duplicate network names", False, lambda r: r.append(deepcopy(r.find("NETWORK"))))

    def cross_network_reference(root):
        two_networks(root)
        root.find("NETWORK/VARIABLE/NAME").text = "OnlyInFirst"
        root.find("NETWORK/DEFINITION/FOR").text = "OnlyInFirst"
        root.find("NETWORK/DEFINITION/GIVEN").text = "OnlyInFirst"
        root.findall("NETWORK")[1].find("DEFINITION/GIVEN").text = "OnlyInFirst"

    check("cross-network reference rejected", False, cross_network_reference)
    check("definitions before variables", True, lambda r: r.find("NETWORK").insert(1, r.find("NETWORK/DEFINITION")))

    def influence_diagram(root):
        network = root.find("NETWORK")
        network.find("VARIABLE").set("TYPE", "decision")
        network.remove(network.find("DEFINITION"))
        network.append(etree.fromstring(b'<VARIABLE TYPE="utility"><NAME>U</NAME></VARIABLE>', parser))
        network.append(etree.fromstring(b"<DEFINITION><FOR>U</FOR><GIVEN>B</GIVEN><TABLE>-10 20</TABLE></DEFINITION>", parser))

    check("decision without CPT and signed utility", True, influence_diagram)
    for order in permutations(("<FOR>B</FOR>", "<GIVEN>A</GIVEN>", "<TABLE>.8 .2 .1 .9</TABLE>", "<PROPERTY>source=test</PROPERTY>")):
        def reorder(root, parts=order):
            old = root.find("NETWORK/DEFINITION[2]")
            old.getparent().replace(old, etree.fromstring(("<DEFINITION>" + "".join(parts) + "</DEFINITION>").encode(), parser))
        check("definition ordering", True, reorder)

    # These deliberately PASS: they document the XSD/semantic-validator boundary.
    check("normalization is a semantic check", True, text_at("NETWORK/DEFINITION/TABLE", "2 -1"))
    check("CPT size is a semantic check", True, text_at("NETWORK/DEFINITION/TABLE", "0.5"))
    check("self-parenting is a semantic check", True, append_at("NETWORK/DEFINITION", b"<GIVEN>A</GIVEN>"))
    check("cycles are a semantic check", True, append_at("NETWORK/DEFINITION", b"<GIVEN>B</GIVEN>"))
    check("missing nature CPT is a semantic check", True, remove_at("NETWORK/DEFINITION"))
    check("empty outcome set is a semantic check", True, lambda r: [v.remove(o) for v in r.findall("NETWORK/VARIABLE") for o in v.findall("OUTCOME")])
    print(f"PASS: schema compiled; {checked} format and boundary checks passed.")


if __name__ == "__main__":
    main()