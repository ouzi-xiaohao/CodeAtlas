from app.services.changes import extract_change_entities


def test_java_diff_extracts_files_classes_methods_and_routes():
    diff = """diff --git a/src/OrderController.java b/src/OrderController.java
--- a/src/OrderController.java
+++ b/src/OrderController.java
@@ -1,2 +1,5 @@
+public class OrderController {
+  @PostMapping("/orders")
+  public Order createOrder() { return service.create(); }
+}
"""
    files, symbols = extract_change_entities(diff)

    assert files == ["src/OrderController.java"]
    assert "OrderController" in symbols
    assert "createOrder" in symbols
    assert "/orders" in symbols
