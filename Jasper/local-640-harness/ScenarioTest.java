import net.sf.jasperreports.engine.*;
import net.sf.jasperreports.engine.query.JsonQueryExecuterFactory;

import java.io.File;
import java.io.FileInputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class ScenarioTest {

    static void runScenario(String label, String jrxmlPath, String jsonPath) {
        System.out.println("\n========== Scenario " + label + " ==========");
        System.out.println("JRXML: " + jrxmlPath);
        System.out.println("JSON:  " + jsonPath);
        try {
            JasperReport jasperReport = JasperCompileManager.compileReport(jrxmlPath);
            Map<String, Object> params = new HashMap<>();
            params.put(JsonQueryExecuterFactory.JSON_INPUT_STREAM, new FileInputStream(jsonPath));

            // two-arg fillReport: lets JasperReports' own jsonql query executer
            // run the report's actual <queryString>, exactly like MAWM's real call
            JasperPrint print = JasperFillManager.fillReport(jasperReport, params);
            System.out.println("Filled OK. Pages: " + print.getPages().size());

            boolean sawLocationHeader = false;
            boolean sawItemRow = false;
            for (Object pageObj : print.getPages()) {
                JRPrintPage page = (JRPrintPage) pageObj;
                for (String line : collect(page.getElements(), 0)) {
                    System.out.println(line);
                    if (line.contains("Location:")) sawLocationHeader = true;
                    if (line.contains("Floral Print Dress") || line.contains("Nautical Rope Belt")) sawItemRow = true;
                }
            }
            System.out.println("--> Location header rendered: " + sawLocationHeader + " | Item rows rendered: " + sawItemRow);
        } catch (Throwable t) {
            System.out.println("THREW: " + t);
            Throwable c = t.getCause();
            while (c != null) {
                System.out.println("  caused by: " + c);
                c = c.getCause();
            }
        }
    }

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
        String scratch = args[0]; // directory containing the scratch jrxml/json files

        runScenario("A (full envelope root, query=Data.*)",
            scratch + "/scenario_AC.jrxml", scratch + "/mawm_sample.json");

        runScenario("B (bare Data array root, query=*)",
            scratch + "/scenario_B.jrxml", scratch + "/mawm_sample_bare_array.json");

        runScenario("C (bare Data array root, query=Data.* unchanged)",
            scratch + "/scenario_AC.jrxml", scratch + "/mawm_sample_bare_array.json");
    }
}
