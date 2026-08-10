import net.sf.jasperreports.engine.data.JsonQLDataSource;
import net.sf.jasperreports.engine.JRField;
import net.sf.jasperreports.engine.JRPropertiesMap;

import java.io.File;
import java.lang.reflect.Proxy;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;

public class JsonQLProbe {

    static JRField field(String name) {
        JRPropertiesMap props = new JRPropertiesMap();
        props.setProperty("net.sf.jasperreports.jsonql.field.expression", name);
        return (JRField) Proxy.newProxyInstance(
            JsonQLProbe.class.getClassLoader(),
            new Class[]{JRField.class},
            new InvocationHandler() {
                public Object invoke(Object proxy, Method m, Object[] args) {
                    switch (m.getName()) {
                        case "getName": return name;
                        case "getValueClass": return String.class;
                        case "getValueClassName": return "java.lang.String";
                        case "getPropertiesMap": return props;
                        case "hasProperties": return true;
                        case "getDescription": return null;
                        case "setDescription": return null;
                        case "getPropertyExpressions": return new net.sf.jasperreports.engine.JRPropertyExpression[0];
                        case "getParentProperties": return null;
                        case "clone": return proxy;
                        case "toString": return "JRFieldProxy(" + name + ")";
                        case "hashCode": return System.identityHashCode(proxy);
                        case "equals": return proxy == args[0];
                        default: return null;
                    }
                }
            });
    }

    static void testRoot(File f, String expr) throws Exception {
        System.out.println("\n=== Root query: [" + expr + "] ===");
        try {
            JsonQLDataSource ds = new JsonQLDataSource(f, expr);
            int count = 0;
            while (ds.next()) {
                count++;
                Object locId = ds.getFieldValue(field("LocationId"));
                Object dispLoc = ds.getFieldValue(field("DisplayLocation"));
                System.out.println("  record " + count + ": LocationId=" + locId + " DisplayLocation=" + dispLoc);

                // now test nested subDataSource with both variants
                testSub(ds, "Items");
                testSub(ds, "Items.*");
            }
            System.out.println("  TOTAL root records: " + count);
        } catch (Throwable t) {
            System.out.println("  THREW: " + t);
        }
    }

    static void testSub(JsonQLDataSource parent, String subExpr) {
        System.out.println("    -- subDataSource(\"" + subExpr + "\") --");
        try {
            JsonQLDataSource sub = parent.subDataSource(subExpr);
            int c = 0;
            while (sub.next()) {
                c++;
                Object itemId = sub.getFieldValue(field("ItemId"));
                Object desc = sub.getFieldValue(field("ItemDescription"));
                Object qty = sub.getFieldValue(field("OnHandSum"));
                System.out.println("      item " + c + ": ItemId=" + itemId + " Desc=" + desc + " OnHandSum=" + qty);
            }
            System.out.println("      sub record count: " + c);
        } catch (Throwable t) {
            System.out.println("      THREW: " + t);
        }
    }

    public static void main(String[] args) throws Exception {
        File f = new File(args[0]);
        testRoot(f, "Data");
        testRoot(f, "Data.*");
    }
}
