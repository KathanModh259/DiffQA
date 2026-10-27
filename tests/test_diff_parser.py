import pytest
from diffqa.analyzer.diff_parser import DiffParser


SAMPLE_DIFF = """diff --git a/src/components/Cart.tsx b/src/components/Cart.tsx
index 1234567..89abcdef 100644
--- a/src/components/Cart.tsx
+++ b/src/components/Cart.tsx
@@ -20,6 +20,8 @@ export function Cart() {
+  const [discount, setDiscount] = useState(0);
+  // Added promo validation
   return (
     <div className="cart">
"""


def test_diff_parser_extracts_files_and_routes():
    parser = DiffParser()
    parsed = parser.parse_string(SAMPLE_DIFF)

    assert len(parsed.files) == 1
    file_info = parsed.files[0]
    assert file_info.path == "src/components/Cart.tsx"
    assert file_info.added_count == 2
    assert file_info.is_ui_component is True
    assert "/cart" in file_info.detected_routes
    assert "/" in file_info.detected_routes
    assert parsed.total_added == 2

