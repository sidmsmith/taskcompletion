import net.sf.jasperreports.engine.*;
import net.sf.jasperreports.engine.data.JsonQLDataSource;

import java.io.File;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class JasperTest640 {
    public static void main(String[] args) throws Exception {
        String jrxmlPath = args[0];
        String jsonPath = args[1];

        System.out.println("Compiling: " + jrxmlPath);
        JasperReport jasperReport = JasperCompileManager.compileReport(jrxmlPath);
        System.out.println("Compiled OK.");

        JsonQLDataSource dataSource = new JsonQLDataSource(new File(jsonPath), "Data.*");

        Map<String, Object> params = new HashMap<>();

        System.out.println("Filling report...");
        JasperPrint print = JasperFillManager.fillReport(jasperReport, params, dataSource);
        System.out.println("Filled OK. Pages: " + print.getPages().size());

        System.out.println("\n--- Extracted text from every text element in the output ---");
        for (Object pageObj : print.getPages()) {
            JRPrintPage page = (JRPrintPage) pageObj;
            List<JRPrintElement> elements = page.getElements();
            printElements(elements, 0);
        }
    }

    static void printElements(List<JRPrintElement> elements, int depth) {
        for (JRPrintElement el : elements) {
            if (el instanceof JRPrintText) {
                String text = ((JRPrintText) el).getFullText();
                if (text != null && !text.trim().isEmpty()) {
                    System.out.println("  ".repeat(depth) + "[text] " + text.replace("\n", "\\n"));
                }
            } else if (el instanceof JRPrintFrame) {
                printElements(((JRPrintFrame) el).getElements(), depth + 1);
            }
        }
    }
}
