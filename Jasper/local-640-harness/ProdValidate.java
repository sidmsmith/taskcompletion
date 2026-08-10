import net.sf.jasperreports.engine.*;
import net.sf.jasperreports.engine.query.JsonQueryExecuterFactory;

import java.io.FileInputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class ProdValidate {

    static java.util.List<String> collect(List<JRPrintElement> elements, int depth) {
        java.util.List<String> out = new java.util.ArrayList<>();
        for (JRPrintElement el : elements) {
            if (el instanceof JRPrintText) {
                String text = ((JRPrintText) el).getFullText();
                if (text != null && !text.trim().isEmpty()) {
                    out.add("  ".repeat(depth) + "[text] " + text.replace("\n", "\\n"));
                }
            } else if (el instanceof JRPrintFrame) {
                out.addAll(collect(((JRPrintFrame) el).getElements(), depth + 1));
            }
        }
        return out;
    }

    public static void main(String[] args) throws Exception {
        String jrxmlPath = args[0];
        String jsonPath = args[1];

        System.out.println("Compiling: " + jrxmlPath);
        JasperReport jasperReport = JasperCompileManager.compileReport(jrxmlPath);
        System.out.println("Compiled OK.");

        Map<String, Object> params = new HashMap<>();
        params.put(JsonQueryExecuterFactory.JSON_INPUT_STREAM, new FileInputStream(jsonPath));

        System.out.println("Filling report (two-arg fillReport, real jsonql query executer, bare array JSON)...");
        JasperPrint print = JasperFillManager.fillReport(jasperReport, params);
        System.out.println("Filled OK. Pages: " + print.getPages().size());

        boolean loc1 = false, loc2 = false, item1 = false, item2 = false, qty1 = false, qty2 = false;
        for (Object pageObj : print.getPages()) {
            JRPrintPage page = (JRPrintPage) pageObj;
            for (String line : collect(page.getElements(), 0)) {
                System.out.println(line);
                if (line.contains("A1AC0401")) loc1 = true;
                if (line.contains("A1AC0405")) loc2 = true;
                if (line.contains("Floral Print Dress")) item1 = true;
                if (line.contains("Nautical Rope Belt")) item2 = true;
                if (line.contains("(2)")) qty1 = true;
                if (line.contains("(493)")) qty2 = true;
            }
        }
        System.out.println("\n--- VALIDATION SUMMARY ---");
        System.out.println("Location A1AC0401 rendered: " + loc1);
        System.out.println("Location A1AC0405 rendered: " + loc2);
        System.out.println("Item 'Floral Print Dress' rendered: " + item1);
        System.out.println("Item 'Nautical Rope Belt' rendered: " + item2);
        System.out.println("Quantity (2) rendered: " + qty1);
        System.out.println("Quantity (493) rendered: " + qty2);
        boolean allGood = loc1 && loc2 && item1 && item2 && qty1 && qty2;
        System.out.println("ALL DATA RENDERED CORRECTLY: " + allGood);
    }
}
