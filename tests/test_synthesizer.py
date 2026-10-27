"""Tests for PlaywrightTestSynthesizer — validates generated spec output."""

import pytest

from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.judge.oracle import BugSeverity, JudgeVerdict
from diffqa.synthesizer.test_writer import PlaywrightTestSynthesizer


@pytest.fixture
def synthesizer():
    return PlaywrightTestSynthesizer()


@pytest.fixture
def sample_verdict():
    return JudgeVerdict(
        flow_id="cart_decrement_bug",
        passed=False,
        verdict="FAIL",
        title="Cart quantity becomes negative on decrement",
        root_cause_hypothesis="Missing Math.max bounds check in Cart component",
        severity=BugSeverity.CRITICAL,
    )


@pytest.fixture
def sample_steps():
    return [
        ExecutionStep(
            step_number=1,
            action=AgentAction(
                thought="Click add to cart",
                action=ActionType.CLICK,
                target_id="btn-add-1",
                selector="#btn-add-to-cart-1",
            ),
            url_before="http://localhost:3000",
        ),
        ExecutionStep(
            step_number=2,
            action=AgentAction(
                thought="Click decrement twice",
                action=ActionType.CLICK,
                target_id="btn-dec-1",
                selector="#btn-qty-decrement-1",
            ),
            url_before="http://localhost:3000/cart",
        ),
    ]


class TestTypeScriptSynthesis:

    def test_generates_valid_typescript(self, synthesizer, sample_verdict, sample_steps):
        spec = synthesizer.synthesize_typescript(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
            invariants=["Quantity >= 0", "Subtotal >= 0"],
        )
        assert "import { test, expect } from '@playwright/test';" in spec
        assert "reproduce:" in spec
        assert "Cart quantity becomes negative" in spec

    def test_includes_selectors(self, synthesizer, sample_verdict, sample_steps):
        spec = synthesizer.synthesize_typescript(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
        )
        assert "#btn-add-to-cart-1" in spec
        assert "#btn-qty-decrement-1" in spec

    def test_includes_invariant_comments(self, synthesizer, sample_verdict, sample_steps):
        spec = synthesizer.synthesize_typescript(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
            invariants=["Quantity >= 0"],
        )
        assert "Invariant: Quantity >= 0" in spec

    def test_includes_metadata(self, synthesizer, sample_verdict, sample_steps):
        spec = synthesizer.synthesize_typescript(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
        )
        assert "CRITICAL" in spec
        assert "Missing Math.max" in spec

    def test_saves_to_file(self, synthesizer, sample_verdict, sample_steps, tmp_path):
        out = tmp_path / "test.spec.ts"
        synthesizer.synthesize_typescript(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
            output_file=out,
        )
        assert out.exists()
        content = out.read_text()
        assert "import { test, expect }" in content


class TestPythonSynthesis:

    def test_generates_valid_python(self, synthesizer, sample_verdict, sample_steps):
        spec = synthesizer.synthesize_python(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
        )
        assert "import pytest" in spec
        assert "async def test_repro_" in spec
        assert "page.goto" in spec

    def test_saves_python_to_file(self, synthesizer, sample_verdict, sample_steps, tmp_path):
        out = tmp_path / "test_repro.py"
        synthesizer.synthesize_python(
            verdict=sample_verdict,
            steps=sample_steps,
            initial_url="http://localhost:3000",
            flow_title="Cart boundary test",
            output_file=out,
        )
        assert out.exists()


class TestEmptySteps:

    def test_typescript_with_no_steps(self, synthesizer, sample_verdict):
        spec = synthesizer.synthesize_typescript(
            verdict=sample_verdict,
            steps=[],
            initial_url="http://localhost:3000",
            flow_title="Empty flow",
        )
        assert "import { test, expect }" in spec
        assert "page.goto" in spec

    def test_python_with_no_steps(self, synthesizer, sample_verdict):
        spec = synthesizer.synthesize_python(
            verdict=sample_verdict,
            steps=[],
            initial_url="http://localhost:3000",
            flow_title="Empty flow",
        )
        assert "page.goto" in spec
