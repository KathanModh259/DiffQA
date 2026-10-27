from diffqa.agent.actions import ActionType, AgentAction, ExecutionStep
from diffqa.judge.oracle import BugSeverity, JudgeVerdict
from diffqa.synthesizer.test_writer import PlaywrightTestSynthesizer


def test_synthesize_typescript_spec():
    synthesizer = PlaywrightTestSynthesizer()

    verdict = JudgeVerdict(
        flow_id="cart_decrement_bug",
        passed=False,
        verdict="FAIL",
        title="Cart quantity becomes negative on decrement",
        root_cause_hypothesis="Missing Math.max bounds check in Cart component",
        severity=BugSeverity.CRITICAL,
    )

    steps = [
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

    spec_code = synthesizer.synthesize_typescript(
        verdict=verdict,
        steps=steps,
        initial_url="http://localhost:3000",
        flow_title="Cart boundary test",
        invariants=["Quantity >= 0", "Subtotal >= 0"],
    )

    assert "import { test, expect } from '@playwright/test';" in spec_code
    assert "reproduce: Cart quantity becomes negative on decrement" in spec_code
    assert "page.locator('#btn-add-to-cart-1').click()" in spec_code
    assert "page.locator('#btn-qty-decrement-1').click()" in spec_code
    assert "Quantity >= 0" in spec_code

