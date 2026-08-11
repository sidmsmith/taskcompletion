import net.sf.jasperreports.engine.*;
import net.sf.jasperreports.engine.query.JsonQueryExecuterFactory;

import java.io.FileInputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * Compiles + fills + exports a real PDF from a JRXML and a JSON payload,
 * using the same two-argument fillReport() path MAWM's own runtime uses
 * (see the main README's "critical gotcha" section) - so this should
 * match what the WMS actually produces, letting you QC layout/data
 * changes locally before uploading to the WMS.
 *
 * Usage: java -cp "cp/*;." RenderPdf <jrxml> <json> <output.pdf>
 */
public class RenderPdf {
    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: java -cp \"cp/*;.\" RenderPdf <jrxml> <json> <output.pdf>");
            System.exit(1);
        }
        String jrxmlPath = args[0];
        String jsonPath = args[1];
        String outPath = args[2];

        System.out.println("Compiling: " + jrxmlPath);
        JasperReport jasperReport = JasperCompileManager.compileReport(jrxmlPath);
        System.out.println("Compiled OK.");

        Map<String, Object> params = new HashMap<>();
        params.put(JsonQueryExecuterFactory.JSON_INPUT_STREAM, new FileInputStream(jsonPath));

        System.out.println("Filling report against: " + jsonPath);
        JasperPrint print = JasperFillManager.fillReport(jasperReport, params);
        System.out.println("Filled OK. Pages: " + print.getPages().size());

        JasperExportManager.exportReportToPdfFile(print, outPath);
        System.out.println("Wrote PDF: " + outPath);
    }
}
