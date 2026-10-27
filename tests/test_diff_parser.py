"""Tests for DiffParser — covers standard diffs, edge cases, and route inference."""

import pytest

from diffqa.analyzer.diff_parser import DiffParser


# ── Fixtures ────────────────────────────────────────────────────────────────

SIMPLE_DIFF = """\
diff --git a/src/components/Cart.tsx b/src/components/Cart.tsx
index 1234567..89abcdef 100644
--- a/src/components/Cart.tsx
+++ b/src/components/Cart.tsx
@@ -20,6 +20,8 @@ export function Cart() {
+  const [discount, setDiscount] = useState(0);
+  // Added promo validation
   return (
     <div className="cart">
"""

MULTI_FILE_DIFF = """\
diff --git a/src/pages/checkout.tsx b/src/pages/checkout.tsx
--- a/src/pages/checkout.tsx
+++ b/src/pages/checkout.tsx
@@ -1,3 +1,5 @@
+import { validateCard } from '../utils/payment';
+
 export default function Checkout() {
diff --git a/src/api/orders.ts b/src/api/orders.ts
--- a/src/api/orders.ts
+++ b/src/api/orders.ts
@@ -10,3 +10,4 @@
-  return await db.createOrder(data);
+  const validated = sanitize(data);
+  return await db.createOrder(validated);
"""

NEW_FILE_DIFF = """\
diff --git a/src/components/Modal.tsx b/src/components/Modal.tsx
new file mode 100644
--- /dev/null
+++ b/src/components/Modal.tsx
@@ -0,0 +1,5 @@
+export function Modal({ children }) {
+  return <div className="modal">{children}</div>;
+}
"""

RENAME_DIFF = """\
diff --git a/src/old-utils.js b/src/new-utils.js
similarity index 90%
rename from src/old-utils.js
rename to src/new-utils.js
--- a/src/old-utils.js
+++ b/src/new-utils.js
@@ -1,2 +1,2 @@
-export const helper = () => {};
+export const helper = () => { return 42; };
"""

STYLE_DIFF = """\
diff --git a/src/styles/main.css b/src/styles/main.css
--- a/src/styles/main.css
+++ b/src/styles/main.css
@@ -5,3 +5,4 @@
   color: red;
+  display: none;
"""


# ── Tests ───────────────────────────────────────────────────────────────────


class TestDiffParser:
    """Unit tests for DiffParser."""

    def setup_method(self):
        self.parser = DiffParser()

    def test_parse_simple_diff(self):
        result = self.parser.parse_string(SIMPLE_DIFF)
        assert len(result.files) == 1
        f = result.files[0]
        assert f.path == "src/components/Cart.tsx"
        assert f.added_count == 2
        assert f.removed_count == 0
        assert f.is_ui_component is True
        assert f.change_type == "modified"

    def test_detected_routes_for_component(self):
        result = self.parser.parse_string(SIMPLE_DIFF)
        f = result.files[0]
        assert "/cart" in f.detected_routes
        assert "/" in f.detected_routes

    def test_multi_file_diff(self):
        result = self.parser.parse_string(MULTI_FILE_DIFF)
        assert len(result.files) == 2
        paths = [f.path for f in result.files]
        assert "src/pages/checkout.tsx" in paths
        assert "src/api/orders.ts" in paths

    def test_pages_route_inference(self):
        result = self.parser.parse_string(MULTI_FILE_DIFF)
        checkout_file = [f for f in result.files if "checkout" in f.path][0]
        assert "/checkout" in checkout_file.detected_routes

    def test_api_route_detection(self):
        result = self.parser.parse_string(MULTI_FILE_DIFF)
        api_file = [f for f in result.files if "api/" in f.path][0]
        assert api_file.is_api_route is True

    def test_new_file_detection(self):
        result = self.parser.parse_string(NEW_FILE_DIFF)
        assert len(result.files) == 1
        f = result.files[0]
        assert f.change_type == "added"
        assert f.added_count == 3

    def test_rename_detection(self):
        result = self.parser.parse_string(RENAME_DIFF)
        assert len(result.files) == 1
        f = result.files[0]
        assert f.path == "src/new-utils.js"
        assert f.old_path == "src/old-utils.js"

    def test_style_file_detection(self):
        result = self.parser.parse_string(STYLE_DIFF)
        f = result.files[0]
        assert f.is_style_file is True
        assert f.is_ui_component is False

    def test_total_counts(self):
        result = self.parser.parse_string(MULTI_FILE_DIFF)
        assert result.total_added == 4
        assert result.total_removed == 1

    def test_suspected_routes_aggregation(self):
        result = self.parser.parse_string(MULTI_FILE_DIFF)
        routes = result.suspected_routes
        assert "/checkout" in routes

    def test_empty_diff(self):
        result = self.parser.parse_string("")
        assert len(result.files) == 0
        assert result.total_added == 0
        assert result.suspected_routes == ["/"]

    def test_parse_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.parser.parse_file("/nonexistent/path.diff")

    def test_ui_files_property(self):
        result = self.parser.parse_string(SIMPLE_DIFF + "\n" + STYLE_DIFF)
        assert len(result.ui_files) == 1
        assert result.ui_files[0].path == "src/components/Cart.tsx"

    def test_hunk_content(self):
        result = self.parser.parse_string(SIMPLE_DIFF)
        hunks = result.files[0].hunks
        assert len(hunks) == 1
        assert any("discount" in line for line in hunks[0].added_lines)
