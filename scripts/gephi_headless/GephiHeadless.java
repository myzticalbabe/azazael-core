import org.gephi.project.api.ProjectController;
import org.gephi.project.api.Workspace;
import org.gephi.io.importer.api.Container;
import org.gephi.io.importer.api.ImportController;
import org.gephi.io.processor.plugin.DefaultProcessor;
import org.gephi.graph.api.Graph;
import org.gephi.graph.api.GraphController;
import org.gephi.graph.api.GraphModel;
import org.gephi.graph.api.Node;
import org.gephi.graph.api.Table;
import org.gephi.graph.api.Column;
import org.gephi.layout.spi.Layout;
import org.gephi.layout.spi.LayoutBuilder;
import org.gephi.preview.api.PreviewController;
import org.gephi.preview.api.PreviewModel;
import org.gephi.preview.api.PreviewProperty;
import org.gephi.io.exporter.api.ExportController;
import org.gephi.io.exporter.preview.PNGExporter;
import org.openide.util.Lookup;
import org.openide.util.lookup.Lookups;

import java.awt.Color;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;
import java.util.TreeMap;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Headless Gephi engine CLI — no GUI, no window focus, deterministic.
 *
 * Loads any GraphML with Gephi's own import pipeline (modules resolved via
 * META-INF/services through a flat classpath), runs a chosen layout, renders a
 * PNG via Gephi's preview/export pipeline, and optionally dumps the node
 * attribute table (incl. post-layout x/y and computed degree) to CSV.
 *
 * Layouts are resolved dynamically against every LayoutBuilder the install
 * registers, so any Gephi layout plugin works — not just ForceAtlas2.
 *
 * Usage:
 *   java GephiHeadless --input in.graphml --output out.png
 *                      [--layout forceatlas2|fruchterman|yifanhu|openord|...]
 *                      [--iterations N] [--attributes out.csv]
 *                      [--width W] [--height H] [--margin M] [--no-labels]
 *                      [--list-layouts]
 *
 *   Positional legacy form (still accepted):
 *   java GephiHeadless in.graphml out.png [iterations]
 */
public class GephiHeadless {

    static {
        Logger.getLogger("org.gephi").setLevel(Level.SEVERE);
        Logger.getLogger("org.netbeans").setLevel(Level.SEVERE);
    }

    /** Alias tokens -> substring to match against builder class simple names. */
    private static final String[][] LAYOUT_ALIASES = {
        {"forceatlas2", "fa2", "fa-2", "fruchtermanreingoldbuilder"}, // default handled below
    };

    private static LayoutBuilder resolveBuilder(Lookup lk, String want) {
        Collection<? extends LayoutBuilder> builders =
                lk.lookupAll(LayoutBuilder.class);
        if (want == null || want.isEmpty()) {
            want = "forceatlas2";
        }
        String norm = normalize(want);
        // exact class-simple-name match first
        for (LayoutBuilder b : builders) {
            String simple = simpleName(b);
            String disp = b.getName() == null ? "" : normalize(b.getName());
            String cls = normalize(simple);
            if (cls.equals(norm) || disp.equals(norm)
                    || cls.replace("builder", "").equals(norm)) {
                return b;
            }
        }
        // prefix / token match (e.g. "fruchterman" -> FruchtermanReingoldBuilder)
        for (LayoutBuilder b : builders) {
            String cls = normalize(simpleName(b));
            String disp = b.getName() == null ? "" : normalize(b.getName());
            if (cls.replace("builder", "").startsWith(norm)
                    || cls.startsWith(norm)
                    || disp.replace(" ", "").startsWith(norm)
                    || norm.startsWith(cls.replace("builder", ""))) {
                return b;
            }
        }
        return null;
    }

    private static String normalize(String s) {
        return s.toLowerCase(Locale.ROOT)
                .replaceAll("[^a-z0-9]", "")
                .trim();
    }

    private static String simpleName(LayoutBuilder b) {
        String n = b.getClass().getName();
        int i = n.lastIndexOf('.');
        return i >= 0 ? n.substring(i + 1) : n;
    }

    private static void listLayouts(Lookup lk) {
        Collection<? extends LayoutBuilder> builders =
                lk.lookupAll(LayoutBuilder.class);
        System.out.println("Available layouts (" + builders.size() + "):");
        for (LayoutBuilder b : builders) {
            System.out.println("  " + simpleName(b)
                    + (b.getName() != null ? "  (" + b.getName() + ")" : ""));
        }
    }

    private static int runLayout(Layout layout, GraphModel gm, int iterations) {
        layout.setGraphModel(gm);
        layout.resetPropertiesValues();
        layout.initAlgo();
        int ran = 0;
        for (int i = 0; i < iterations && layout.canAlgo(); i++) {
            layout.goAlgo();
            ran++;
        }
        layout.endAlgo();
        return ran;
    }

    /**
     * Set an explicit node-label font size (text property read by the preview
     * NodeLabelBuilder). Makes labels legible at high node counts.
     */
    private static void setLabelSize(Graph graph, float size) {
        for (Node n : graph.getNodes()) {
            n.getTextProperties().setSize(size);
        }
        System.out.println("[4d] node label size set to " + size);
    }

    /**
     * Promote a column's value to the node display label (GraphML exporters
     * often store names in an attribute; element labels then default to ids).
     */
    private static void setLabelsFromColumn(Graph graph, GraphModel gm,
            String colId) {
        Column col = findColumn(gm.getNodeTable(), colId);
        if (col == null) {
            System.err.println("WARN: label column '" + colId
                    + "' not found; keeping element labels");
            return;
        }
        int set = 0;
        for (Node n : graph.getNodes()) {
            Object v = n.getAttribute(col);
            if (v != null && !v.toString().trim().isEmpty()) {
                String text = v.toString().trim();
                n.setLabel(text);
                // Gephi stores the rendered label text + visibility on the
                // node's TextProperties; both must be set for the preview.
                n.getTextProperties().setText(text);
                n.getTextProperties().setVisible(true);
                set++;
            }
        }
        System.out.println("[4e] node labels set from column '" + colId
                + "' (" + set + " nodes)");
    }

    /** Categorical palette (Order of precedence: Tab20-ish, colorblind-friendly). */
    private static final Color[] PALETTE = {
        new Color(0x4E79A7), new Color(0xF28E2B), new Color(0xE15759),
        new Color(0x76B7B2), new Color(0x59A14F), new Color(0xEDC948),
        new Color(0xB07AA1), new Color(0xFF9DA7), new Color(0x9C755F),
        new Color(0xBAB0AC), new Color(0x86BCB6), new Color(0xD37295),
    };

    /**
     * Color nodes by the values of one column (community partition). Each
     * distinct value gets a deterministic palette color (sorted by value so
     * "0..4" always maps to the first five palette entries).
     */
    private static Column findColumn(Table nt, String want) {
        // GraphML columns may carry a generated id but the file's attr name
        // as title (networkx writes <key id=dN attr.name="Community">).
        // Skip the element id/label columns (ids "id"/"label", titles
        // "Id"/"Label"): matching those by title returns node ids, not data.
        for (Column c : nt.toArray()) {
            if (want.equalsIgnoreCase(c.getTitle())
                    && (c.getId().equals("id") || c.getId().equals("label"))) {
                continue; // element column, not a data column
            }
            if (want.equals(c.getId()) || want.equals(c.getTitle())
                    || want.equalsIgnoreCase(c.getId())
                    || want.equalsIgnoreCase(c.getTitle())) {
                return c;
            }
        }
        return null;
    }

    private static void colorByColumn(Graph graph, GraphModel gm, String colId) {
        Table nt = gm.getNodeTable();
        Column col = findColumn(nt, colId);
        if (col == null) {
            System.err.println("WARN: column '" + colId + "' not found;"
                    + " skipping --color-by (no node colors applied)");
            return;
        }
        // deterministic ordering of the distinct values
        TreeMap<String, Color> valueColors = new TreeMap<>();
        int paletteIdx = 0;
        // first pass: assign colors to sorted distinct values
        java.util.SortedSet<String> values = new java.util.TreeSet<>();
        for (Node n : graph.getNodes()) {
            Object v = n.getAttribute(col);
            values.add(v == null ? "" : v.toString());
        }
        for (String v : values) {
            valueColors.put(v, PALETTE[paletteIdx++ % PALETTE.length]);
        }
        for (Node n : graph.getNodes()) {
            Object v = n.getAttribute(col);
            Color c = valueColors.get(v == null ? "" : v.toString());
            if (c != null) {
                n.setColor(c);
            }
        }
        System.out.println("[4c] node colors set by column '" + colId
                + "' (" + valueColors.size() + " groups)");
    }

    private static String csv(Object v) {
        if (v == null) {
            return "";
        }
        String s = v.toString();
        if (s.indexOf(',') >= 0 || s.indexOf('"') >= 0
                || s.indexOf('\n') >= 0 || s.indexOf('\r') >= 0) {
            return "\"" + s.replace("\"", "\"\"") + "\"";
        }
        return s;
    }

    private static void exportAttributes(Graph graph, GraphModel gm,
            File out) throws IOException {
        Table nt = gm.getNodeTable();
        Column[] cols = nt.toArray();
        try (BufferedWriter w = new BufferedWriter(new OutputStreamWriter(
                new FileOutputStream(out), StandardCharsets.UTF_8))) {
            // header
            StringBuilder h = new StringBuilder("Id,Label,X,Y,Degree");
            for (Column c : cols) {
                String id = c.getId();
                if (id.equals("id") || id.equals("label")) {
                    continue;
                }
                h.append(",").append(csv(c.getTitle() != null ? c.getTitle() : id));
            }
            w.write(h.toString());
            w.newLine();
            for (Node n : graph.getNodes()) {
                StringBuilder sb = new StringBuilder();
                sb.append(csv(n.getId()));
                sb.append(",").append(csv(n.getLabel()));
                sb.append(",").append(n.x());
                sb.append(",").append(n.y());
                sb.append(",").append(graph.getDegree(n));
                for (Column c : cols) {
                    String id = c.getId();
                    if (id.equals("id") || id.equals("label")) {
                        continue;
                    }
                    Object v = n.getAttribute(c);
                    sb.append(",").append(csv(v));
                }
                w.write(sb.toString());
                w.newLine();
            }
        }
    }

    public static void main(String[] args) throws Exception {
        // ---- parse args (flags + legacy positionals) ----
        File in = null;
        File out = null;
        String layoutName = null;
        int iterations = 300;
        File attrOut = null;
        int width = 2000;
        int height = 1400;
        int margin = 60;
        boolean labels = true;
        boolean listOnly = false;
        String colorBy = null;
        String labelBy = null;
        float labelSize = 0f; // 0 = leave default

        java.util.List<String> positional = new ArrayList<>();
        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            switch (a) {
                case "--input": in = new File(args[++i]); break;
                case "--output": out = new File(args[++i]); break;
                case "--layout": layoutName = args[++i]; break;
                case "--iterations": case "--iters":
                    iterations = Integer.parseInt(args[++i]); break;
                case "--attributes": attrOut = new File(args[++i]); break;
                case "--width": width = Integer.parseInt(args[++i]); break;
                case "--height": height = Integer.parseInt(args[++i]); break;
                case "--margin": margin = Integer.parseInt(args[++i]); break;
                case "--no-labels": labels = false; break;
                case "--color-by": colorBy = args[++i]; break;
                case "--label-by": labelBy = args[++i]; break;
                case "--label-size": labelSize = Float.parseFloat(args[++i]); break;
                case "--list-layouts": listOnly = true; break;
                default:
                    if (a.startsWith("-")) {
                        System.err.println("unknown option: " + a);
                        System.exit(2);
                    }
                    positional.add(a);
            }
        }
        // legacy positional form: in.graphml out.png [iterations]
        if (positional.size() >= 1 && in == null) {
            in = new File(positional.get(0));
        }
        if (positional.size() >= 2 && out == null) {
            out = new File(positional.get(1));
        }
        if (positional.size() >= 3) {
            iterations = Integer.parseInt(positional.get(2));
        }

        Lookup lk = Lookups.metaInfServices(GephiHeadless.class.getClassLoader());

        if (listOnly) {
            listLayouts(lk);
            System.exit(0);
        }

        if (in == null || out == null) {
            System.err.println("usage: GephiHeadless --input in.graphml --output out.png"
                    + " [--layout NAME] [--iterations N] [--attributes out.csv]"
                    + " [--list-layouts]");
            System.err.println("       (legacy positional form also accepted:"
                    + " GephiHeadless in.graphml out.png [iterations])");
            System.exit(2);
        }
        if (!in.exists()) {
            System.err.println("input not found: " + in.getAbsolutePath());
            System.exit(2);
        }

        // MetaInfServices Lookup: same service wiring the desktop app gets from
        // its module system, read straight from META-INF/services.
        ProjectController pc = lk.lookup(ProjectController.class);
        pc.newProject();
        Workspace ws = pc.getCurrentWorkspace();
        System.out.println("[1] project/workspace created");

        ImportController ic = lk.lookup(ImportController.class);
        Container container = ic.importFile(in);
        ic.process(container, new DefaultProcessor(), ws);
        System.out.println("[2] import OK: " + in.getName());

        GraphModel gm = lk.lookup(GraphController.class).getGraphModel();
        Graph graph = gm.getGraphVisible();
        System.out.println("[3] graph: nodes=" + graph.getNodeCount()
                + " edges=" + graph.getEdgeCount()
                + " directed=" + gm.isDirected());

        // ---- layout (default ForceAtlas2) ----
        LayoutBuilder builder = resolveBuilder(lk, layoutName);
        if (builder == null) {
            System.err.println("ERROR: no layout matches '" + layoutName + "'");
            listLayouts(lk);
            System.exit(2);
        }
        Layout layout = builder.buildLayout();
        String used = layoutName == null ? "forceatlas2" : layoutName;
        int ran = runLayout(layout, gm, iterations);
        System.out.println("[4] layout=" + used + " ("
                + simpleName(builder) + ") ran " + ran + " iterations");            // ---- optional community coloring (node colors by a column) ----
            if (colorBy != null) {
                colorByColumn(graph, gm, colorBy);
            }

            // ---- optional label source column + explicit font size ----
            if (labels && labelBy != null) {
                setLabelsFromColumn(graph, gm, labelBy);
            }
            if (labels && labelSize > 0f) {
                setLabelSize(graph, labelSize);
            }

            // ---- optional attributes export (post-layout x/y + degree + columns) ----
            if (attrOut != null) {
                exportAttributes(graph, gm, attrOut);
                long n = Files.size(attrOut.toPath());
                System.out.println("[4b] attributes export OK: " + attrOut.getAbsolutePath()
                        + " (" + n + " bytes)");
            }

        // ---- preview model + PNG export (NOT -Djava.awt.headless: the PNG
        // exporter builds its G2D target from the screen device) ----
        PreviewController pv = lk.lookup(PreviewController.class);
        PreviewModel pm = pv.getModel(ws);
        pm.getProperties().putValue(PreviewProperty.SHOW_NODE_LABELS, labels);
        if (labels && labelSize > 0f) {
            // explicit font size wins over per-node proportional scaling
            pm.getProperties().putValue(PreviewProperty.NODE_LABEL_PROPORTIONAL_SIZE,
                    Boolean.FALSE);
        }
        pv.refreshPreview(ws);

        ExportController ec = lk.lookup(ExportController.class);
        PNGExporter png = (PNGExporter) ec.getExporter("image/png");
        if (png == null) {
            png = new PNGExporter();
        }
        png.setWorkspace(ws);
        png.setWidth(width);
        png.setHeight(height);
        png.setMargin(margin);
        ec.exportFile(out, png);
        long size = Files.size(out.toPath());
        System.out.println("[5] PNG export OK: " + out.getAbsolutePath()
                + " (" + size + " bytes)");
        System.out.println("DONE");
    }
}
