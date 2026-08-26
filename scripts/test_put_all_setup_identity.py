"""setUp identity for fixed-replay fusion: the generated suite name is not state."""
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUT_ALL = os.path.join(REPO, "notes", "coverage", "scripts", "put_all.py")
spec = importlib.util.spec_from_file_location("put_all", PUT_ALL)
put_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(put_all)

MOCK = "contract ESBMCMock_ENS_{suite} {{\n    function owner(bytes32) external pure returns (address) {{ return address(3000); }}\n}}\n"
SETUP = ("function setUp() public {{\n"
         "    try new ESBMCMock_ENS_{suite}() returns (ESBMCMock_ENS_{suite} m) {{ mk = m; }} catch {{}}\n"
         "    c0 = new ReverseRegistrar(mk);\n}}")
BASIS = "ReverseRegistrarCovTest_ReverseRegistrar_owner_concrete3p1__basis_root"
PUT = "ReverseRegistrarCovTest_1_ReverseRegistrar_owner_put3p1"


def _suite(name, mock_body=MOCK):
    return mock_body.format(suite=name) + "contract " + name + " is Test {\n" + SETUP.format(suite=name) + "\n}\n"


def test_same_setup_modulo_suite_name_is_accepted():
    ps, bs = _suite(PUT), _suite(BASIS)
    assert put_all.setup_state_identity_error(ps, SETUP.format(suite=PUT), bs, SETUP.format(suite=BASIS)) is None


def test_identical_setup_is_accepted():
    s = _suite(PUT)
    assert put_all.setup_state_identity_error(s, SETUP.format(suite=PUT), s, SETUP.format(suite=PUT)) is None


def test_real_setup_difference_is_still_refused():
    ps, bs = _suite(PUT), _suite(BASIS)
    other = SETUP.format(suite=BASIS).replace("new ReverseRegistrar(mk)", "new ReverseRegistrar(address(0))")
    err = put_all.setup_state_identity_error(ps, SETUP.format(suite=PUT), bs, other)
    assert err == "PUT and certified basis replay use different setup state"


def test_different_mock_definition_is_refused():
    other_mock = MOCK.replace("address(3000)", "address(4000)")
    ps, bs = _suite(PUT), _suite(BASIS, other_mock)
    err = put_all.setup_state_identity_error(ps, SETUP.format(suite=PUT), bs, SETUP.format(suite=BASIS))
    assert err and "generated mock definitions differ" in err


def test_missing_mock_definition_is_refused():
    ps = "contract " + PUT + " is Test {\n" + SETUP.format(suite=PUT) + "\n}\n"
    bs = _suite(BASIS)
    err = put_all.setup_state_identity_error(ps, SETUP.format(suite=PUT), bs, SETUP.format(suite=BASIS))
    assert err and "generated mock definitions differ" in err
