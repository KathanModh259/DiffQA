"""Tests for Superhuman QA Oracle and issue reporting."""

from diffqa.judge.superhuman import SuperhumanIssue, SuperhumanAuditReport


class TestSuperhumanOracle:

    def test_superhuman_issue_model(self):
        issue = SuperhumanIssue(
            category="layout_overflow",
            severity="HIGH",
            message="Scroll width exceeds viewport width",
            details={"width": 1400},
        )
        assert issue.category == "layout_overflow"
        assert issue.severity == "HIGH"

    def test_score_calculation(self):
        issues = [
            SuperhumanIssue(category="c1", severity="CRITICAL", message="msg"),
            SuperhumanIssue(category="c2", severity="HIGH", message="msg"),
        ]
        crit = sum(1 for i in issues if i.severity == "CRITICAL")
        high = sum(1 for i in issues if i.severity == "HIGH")
        penalty = (crit * 30) + (high * 15)
        score = max(0, 100 - penalty)
        assert score == 55

    def test_audit_report_creation(self):
        report = SuperhumanAuditReport(
            url="https://example.com",
            total_issues=2,
            critical_issues=1,
            high_issues=1,
            medium_issues=0,
            low_issues=0,
            quality_score=55,
        )
        assert report.quality_score == 55
        assert report.critical_issues == 1

