"""Tests for Adversarial Fuzzer payloads and models."""

import pytest
from diffqa.agent.fuzzer import FUZZ_PAYLOADS, AdversarialFuzzer, FuzzPayload


class TestAdversarialFuzzer:

    def test_fuzz_payloads_catalog(self):
        assert len(FUZZ_PAYLOADS) >= 8
        categories = {p.category for p in FUZZ_PAYLOADS}
        assert "numeric" in categories
        assert "unicode" in categories
        assert "injection" in categories
        assert "overflow" in categories

    def test_boundary_payload_values(self):
        negative_one = [p for p in FUZZ_PAYLOADS if p.name == "negative_one"][0]
        assert negative_one.value == "-1"

        rtl = [p for p in FUZZ_PAYLOADS if p.name == "rtl_arabic"][0]
        assert len(rtl.value) > 0

