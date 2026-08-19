"""Focused tests for selected-contract setup reconciliation."""

import unittest

from rq1_anchor_setup_recovery import (reconcile_selected_contract_setup,
                                       token_stream_sha256)


class SetupRecoveryTest(unittest.TestCase):
    """Exercise fail-closed setup equivalence decisions."""

    def test_reconciles_only_selected_contract(self):
        """Only setUp in the selected test's contract may be replaced."""
        put = """contract A { function setUp() public { c0 = new C(); }
          function test_put_x(uint x) public {} }
          contract B { function setUp() public { unsafe(); } }"""
        emit = """contract A { function setUp() public {
          // retained setup
          c0 = new C(); }
          function test_cov_0() public {} }
          contract B { function setUp() public { unsafe(); } }"""
        selector = {"tier": "code_equal_format_or_comment_only", "semantic_setup": {
            "put_tokens_sha256": token_stream_sha256(" c0 = new C(); "),
            "emit_tokens_sha256": token_stream_sha256(" c0 = new C(); "),
        }}
        reconciled, kind = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", selector)
        self.assertEqual(kind, "selected-contract-code-equivalent/v1")
        self.assertIn("contract B { function setUp() public { unsafe(); } }", reconciled)

    def test_rejects_unsealed_state_difference(self):
        """A state-changing mismatch remains a re-verification obligation."""
        put = "contract A { function setUp() public { x = 1; } function test_put_x() public {} }"
        emit = "contract A { function setUp() public { x = 2; } function test_cov_0() public {} }"
        reconciled, error = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", {"setup": {}})
        self.assertIsNone(reconciled)
        self.assertEqual(error, "setup equivalence is not hash-sealed")

    def test_rejects_string_literal_difference(self):
        """Comment normalization must retain string literal semantics."""
        put = ('contract A { function setUp() public { x = "PUT"; } '
               'function test_put_x() public {} }')
        emit = ('contract A { function setUp() public { x = "EMIT"; } '
                'function test_cov_0() public {} }')
        selector = {"tier": "code_equal_format_or_comment_only", "semantic_setup": {
            "put_tokens_sha256": token_stream_sha256(' x = "PUT"; '),
            "emit_tokens_sha256": token_stream_sha256(' x = "EMIT"; '),
        }}
        reconciled, _error = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", selector)
        self.assertIsNone(reconciled)

    def test_rejects_parameterized_setup(self):
        """Foundry setup recovery accepts only a zero-argument setup declaration."""
        put = ("contract A { function setUp(uint x) public { y=1; } "
               "function test_put_x() public {} }")
        emit = "contract A { function setUp() public { y=1; } function test_cov_0() public {} }"
        selector = {"tier": "code_equal_format_or_comment_only", "semantic_setup": {
            "put_tokens_sha256": token_stream_sha256(" y=1; "),
            "emit_tokens_sha256": token_stream_sha256(" y=1; ")}}
        reconciled, _error = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", selector)
        self.assertIsNone(reconciled)

    def test_rejects_general_prank_window(self):
        """Deployer equivalence cannot authorize sender-sensitive calls."""
        put_body = "vm.startPrank(alice, alice); target.f(); vm.stopPrank();"
        emit_body = "vm.startPrank(alice); target.f(); vm.stopPrank();"
        selector = {"tier": "deployer_context_only", "semantic_setup": {
            "put_setup_sha256": token_stream_sha256(put_body),
            "emit_setup_sha256": token_stream_sha256(emit_body),
            "put_tokens_sha256": token_stream_sha256(put_body),
            "emit_tokens_sha256": token_stream_sha256(emit_body),
        }, "deployment_safety_facts": {
            "constructor_count": 0, "initializer_call_count": 0,
            "nested_new_count": 0, "deployment_environment_tokens": []}}
        put = (f"contract A {{ function setUp() public {{ {put_body} }} "
               "function test_put_x() public {} }")
        emit = (f"contract A {{ function setUp() public {{ {emit_body} }} "
                "function test_cov_0() public {} }")
        reconciled, _error = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", selector)
        self.assertIsNone(reconciled)

    def test_preserves_token_boundaries(self):
        """Whitespace removal cannot merge a declaration into one identifier."""
        put_body = "E e;"
        emit_body = "Ee;"
        selector = {"tier": "code_equal_format_or_comment_only", "semantic_setup": {
            "put_tokens_sha256": token_stream_sha256(put_body),
            "emit_tokens_sha256": token_stream_sha256(emit_body)}}
        put = f"contract A {{ function setUp() public {{ {put_body} }} function test_put_x() public {{}} }}"
        emit = f"contract A {{ function setUp() public {{ {emit_body} }} function test_cov_0() public {{}} }}"
        reconciled, _error = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", selector)
        self.assertIsNone(reconciled)

    def test_comment_preserves_token_boundary(self):
        """A removed block comment still separates its neighboring tokens."""
        put_body = "E/* retained separator */e;"
        emit_body = "Ee;"
        selector = {"tier": "code_equal_format_or_comment_only", "semantic_setup": {
            "put_tokens_sha256": token_stream_sha256(put_body),
            "emit_tokens_sha256": token_stream_sha256(emit_body)}}
        put = f"contract A {{ function setUp() public {{ {put_body} }} function test_put_x() public {{}} }}"
        emit = f"contract A {{ function setUp() public {{ {emit_body} }} function test_cov_0() public {{}} }}"
        reconciled, _error = reconcile_selected_contract_setup(
            put, "test_put_x", emit, "test_cov_0", selector)
        self.assertIsNone(reconciled)


if __name__ == "__main__":
    unittest.main()
